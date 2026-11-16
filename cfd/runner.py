"""Stage runner: check -> prep -> mesh -> solve -> qc -> hemo -> metrics -> report."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import Patient, load_openings

STAGES = ["check", "prep", "mesh", "solve", "qc", "hemo", "metrics", "master", "report", "validate"]


class Runner:
    def __init__(self, patient: Patient, *, force: bool = False, dry_run: bool = False):
        self.p = patient
        self.force = force
        self.dry_run = dry_run
        self.p.ensure_dirs()
        self.manifest_path = self.p.geometry / self.p.case_id / "case_manifest.json"

    # ------------------------------------------------------------------ utils
    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with (self.p.logs / "run.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _run(self, name: str, cmd: list[str], *, timeout: int = 24 * 3600,
             cwd: Path | None = None) -> None:
        log_path = self.p.logs / f"{name}.log"
        self.log(f"{name}: {' '.join(cmd)}")
        if self.dry_run:
            return
        env = self.p.base_env()
        with log_path.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write("\n$ " + " ".join(cmd) + "\n")
            fh.flush()
            proc = subprocess.run(cmd, cwd=str(cwd or self.p.root), env=env,
                                  stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"{name} failed rc={proc.returncode}; see {log_path}")

    def _engine(self, name: str) -> str:
        return str(self.p.engine / name)

    # ----------------------------------------------------------------- stages
    def check(self) -> None:
        from .doctor import diagnose
        report = diagnose(self.p)
        out = self.p.validation / "doctor.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not report["ok"]:
            missing = [k for k, v in report["checks"].items() if not v.get("ok")]
            self.log("doctor: missing/blocked: " + ", ".join(missing))

    def prep(self) -> None:
        data = copy.deepcopy(self.p.data)
        data["case_id"] = self.p.case_id
        lumen = data.get("geometry", {}).get("lumen_stl") or data.get("lumen_stl")
        if not lumen:
            raise SystemExit("patient.json: geometry.lumen_stl is required")
        data["lumen_stl"] = str(self.p.resolve(lumen))
        data["openings"] = load_openings(self.p)
        if not data.get("zones"):
            raise SystemExit("patient.json: zones is empty; define at least one balloon zone")
        cfg_path = self.p.work / "prepare_config.json"
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.p.geometry.mkdir(parents=True, exist_ok=True)
        self._run("prep", [sys.executable, self._engine("prepare_case.py"),
                           "--config", str(cfg_path), "--workdir", str(self.p.geometry)])
        if not self.manifest_path.exists():
            raise RuntimeError(f"prepare_case did not write {self.manifest_path}")
        self._adopt_manifest()
        self._copy_assets()

    def _rel(self, value: Any) -> Any:
        """Prefer a patient-relative path so patient.json stays portable."""
        if not value:
            return value
        try:
            return Path(str(value)).resolve().relative_to(self.p.root).as_posix()
        except (OSError, ValueError):
            return str(value)

    def _adopt_manifest(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        cases: list[dict[str, Any]] = []
        for tag, info in (manifest.get("tags") or {}).items():
            zone = info.get("zone") or ""
            gap = info.get("gap_mm")
            kind = info.get("kind") or "gap"
            if kind == "complete":
                label = f"{zone} complete"
                key = f"{zone.lower()}_complete"
            else:
                label = f"{zone} {gap:g} mm" if isinstance(gap, (int, float)) else f"{zone} {gap} mm"
                key = f"{zone.lower()}_g{str(gap).replace('.', '')}"
            cases.append(dict(tag=tag, label=label, key=key, zone=zone, kind=kind,
                              gap_mm=gap, step=self._rel(info.get("step")),
                              stl=self._rel(info.get("stl"))))
        # Optional complete-occlusion source-term cases run on a base mesh.
        for src in self.p.data.get("source_cases") or []:
            base = str(src.get("base") or "")
            cases.append(dict(tag=src["tag"], label=src.get("label", src["tag"]),
                              key=src.get("key", src["tag"]), zone=src.get("zone", "ZoneI"),
                              kind="comp", gap_mm=None, base=base))
        self.p.data["cases"] = cases
        if not self.p.data.get("case_id"):
            self.p.data["case_id"] = self.p.case_id
        self.p.data.pop("_manifest", None)
        self._save_patient()

    def _save_patient(self) -> None:
        path = self.p.path
        backup = path.with_suffix(".json.bak")
        if path.exists() and not backup.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(json.dumps(self.p.data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    def _copy_assets(self) -> None:
        """Copy files the engine expects into the geometry directory."""
        wd = self.p.geometry / self.p.case_id
        targets = [self.p.meshes]
        for src in wd.glob("*.json"):
            if src.name in {"case_manifest.json"}:
                continue
            for dst in targets:
                shutil.copy2(src, dst / src.name)
        # Original-case STL/STEP are referenced by absolute paths in the manifest;
        # only JSON metadata needs to be local to the geometry directory.
        self.p.meshes.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.p.engine / "wk_aorta_ext4.c", self.p.wkcase / "wk_aorta_ext4.c")

    def mesh(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        mesh_cfg = self.p.data.get("mesh") or {}
        solver = self.p.data.get("solver") or {}
        layers = int(mesh_cfg.get("layers", 5))
        first_mm = float(mesh_cfg.get("first_mm", 0.02))
        max_mm = float(mesh_cfg.get("max_mm", 2.0))
        min_mm = float(mesh_cfg.get("min_mm", 0.4))
        rate = float(mesh_cfg.get("growth", 1.2))
        cores = int(solver.get("cores") or self.p.runtime.cores)
        for case in self.p.cases():
            tag = case["tag"]
            if self.force:
                for suffix in ("mesh_%s_bc.msh.h5" % tag, "mesh_%s.msh.h5" % tag,
                               "wt_%s.msh.h5" % tag, "boundary_%s.msh" % tag,
                               "prime_%s.msh" % tag):
                    (self.p.meshes / suffix).unlink(missing_ok=True)
            if case.get("kind") == "comp":
                base = case.get("base")
                if not base:
                    raise SystemExit(f"source case {tag}: base mesh tag is required")
                for suffix in ("mesh_%s.msh.h5" % base, "mesh_%s_bc.msh.h5" % base):
                    src = self.p.meshes / suffix
                    if not src.exists():
                        raise SystemExit(f"source case {tag}: missing {src}")
                    shutil.copy2(src, self.p.meshes / suffix.replace(base, tag))
                self.log(f"mesh: {tag} copied from {base}")
                continue
            step = case.get("step") or (manifest["tags"].get(tag) or {}).get("step")
            if not step:
                raise SystemExit(f"mesh: no STEP for {tag}")
            self._run("mesh_%s" % tag, [
                self.p.runtime.python, self._engine("mesh_tag.py"),
                "--step", str(step), "--tag", tag, "--workdir", str(self.p.meshes),
                "--layers", str(layers), "--first-mm", str(first_mm),
                "--rate", str(rate), "--min-mm", str(min_mm), "--max-mm", str(max_mm),
                "--cores", str(cores),
            ], timeout=6 * 3600)

    def solve(self) -> None:
        mesh_cfg = self.p.data.get("mesh") or {}
        solver = self.p.data.get("solver") or {}
        first = float(mesh_cfg.get("first_mm", 0.02)) / 1000.0
        for case in self.p.cases():
            tag = case["tag"]
            args = [self.p.runtime.ansys_python, self._engine("pipeline.py"), tag,
                    str(case.get("step") or ""),
                    str(float(mesh_cfg.get("min_mm", 0.4)) / 1000.0),
                    str(float(mesh_cfg.get("max_mm", 2.0)) / 1000.0),
                    str(int(solver.get("cores") or self.p.runtime.cores)),
                    str(int(mesh_cfg.get("layers", 5))), str(first),
                    str(float(mesh_cfg.get("growth", 1.2))),
                    str(int(solver.get("cycles", 3)))]
            self._run("solve_%s" % tag, args, timeout=36 * 3600)

    def qc(self) -> None:
        tags = [c["tag"] for c in self.p.cases()]
        self._run("qc_flux", [sys.executable, self._engine("flux.py"), *tags])
        self._run("qc_cycle", [sys.executable, self._engine("cycle.py"), *tags])

    def hemo(self) -> None:
        cores = str(int((self.p.data.get("solver") or {}).get("cores") or self.p.runtime.cores))
        for case in self.p.cases():
            self._run("hemo_%s" % case["tag"],
                      [self.p.runtime.ansys_python, self._engine("hemo_frames.py"),
                       case["tag"], cores], timeout=8 * 3600)

    def metrics(self) -> None:
        self._run("metrics", [sys.executable, self._engine("metrics.py"),
                              "--config", str(self.p.path), "--out", str(self.p.results)])

    def master(self) -> None:
        metrics = self.p.results / "BL_WSS_METRICS.json"
        self._run("master", [sys.executable, self._engine("master.py"),
                             "--config", str(self.p.path), "--metrics", str(metrics),
                             "--out", str(self.p.results)])

    def report(self) -> None:
        metrics = self.p.results / "BL_WSS_METRICS.json"
        master = self.p.results / "CASE_MASTER_RESULTS.csv"
        self._run("report", [sys.executable, self._engine("report.py"),
                             "--config", str(self.p.path), "--metrics", str(metrics),
                             "--master", str(master),
                             "--out", str(self.p.reports / "REPORT.md")])

    def validate(self) -> None:
        metrics = self.p.results / "BL_WSS_METRICS.json"
        self._run("validate", [sys.executable, self._engine("validate.py"),
                               "--config", str(self.p.path), "--metrics", str(metrics)])

    # ------------------------------------------------------------------- driver
    def run(self, stages: list[str]) -> None:
        if stages == ["all"]:
            stages = STAGES
        for stage in stages:
            if stage not in STAGES:
                raise SystemExit(f"unknown stage {stage}; choose from {', '.join(STAGES)}")
            if self.dry_run:
                self.log(f"[dry-run] would run {stage}")
                continue
            self.log(f"=== {stage} ===")
            getattr(self, stage)()
        self.log("done" + (" (dry-run)" if self.dry_run else ""))

