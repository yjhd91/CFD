"""Configuration and runtime discovery for the CFD patient pipeline.

The package deliberately keeps *all* machine-specific paths out of source code.
A patient directory carries a ``patient.json``; the runtime section (or CFD_*
environment variables) locates ANSYS, Fluent, SpaceClaim and Python.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_ANSYS_ROOT = r"D:\ANSYS2024R2\ANSYS Inc\v242"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value else default


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


@dataclass
class Runtime:
    ansys_root: str
    ansys_python: str
    python: str
    fluent: str
    spaceclaim: str
    prime_root: str
    prime_site: str = ""
    prime_port: int = 50080
    cores: int = 4
    fluent_batch: str = ""

    def as_env(self) -> dict[str, str]:
        env = {
            "CFD_ANSYS_ROOT": self.ansys_root,
            "CFD_ANSYS_PYTHON": self.ansys_python,
            "CFD_PYTHON": self.python,
            "CFD_FLUENT": self.fluent,
            "CFD_SPACECLAIM": self.spaceclaim,
            "CFD_PRIME_ROOT": self.prime_root,
            "CFD_PRIME_SITE": self.prime_site,
            "CFD_PRIME_PORT": str(self.prime_port),
            "CFD_CORES": str(self.cores),
            "CFD_FLUENT_BATCH": self.fluent_batch,
        }
        if self.prime_site:
            old = os.environ.get("PYTHONPATH", "")
            env["PYTHONPATH"] = self.prime_site + (os.pathsep + old if old else "")
        return env


@dataclass
class Patient:
    path: Path
    data: dict[str, Any]
    runtime: Runtime
    engine: Path

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def patient_id(self) -> str:
        return str(self.data.get("patient_id") or self.root.name)

    @property
    def case_id(self) -> str:
        return str(self.data.get("case_id") or self.patient_id)

    @property
    def work(self) -> Path:
        return self._dir("work", "work")

    @property
    def geometry(self) -> Path:
        return self._dir("geometry", "geometry")

    @property
    def meshes(self) -> Path:
        return self._dir("meshes", "meshes")

    @property
    def results(self) -> Path:
        return self._dir("results", "results")

    @property
    def reports(self) -> Path:
        return self._dir("reports", "reports")

    @property
    def logs(self) -> Path:
        return self._dir("logs", "logs")

    @property
    def wkcase(self) -> Path:
        return self.work / "wkcase"

    @property
    def hemo(self) -> Path:
        return self.work / "hemo"

    @property
    def validation(self) -> Path:
        return self._dir("validation", "validation")

    def _dir(self, key: str, default: str) -> Path:
        raw = (self.data.get("paths") or {}).get(key, default)
        path = Path(raw)
        return path if path.is_absolute() else self.root / path

    def resolve(self, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def ensure_dirs(self) -> None:
        for path in (self.work, self.geometry, self.meshes, self.results,
                     self.reports, self.logs, self.validation, self.wkcase, self.hemo):
            path.mkdir(parents=True, exist_ok=True)

    def zone_config(self) -> dict[str, dict[str, Any]]:
        return self.data.get("zones") or {}

    def cases(self) -> list[dict[str, Any]]:
        return self.data.get("cases") or []

    def comp_tags(self) -> dict[str, str]:
        """tag -> zone for every source-term (complete occlusion) case."""
        out: dict[str, str] = {}
        for c in self.cases():
            kind = str(c.get("kind", ""))
            if kind == "comp" or kind.startswith("comp"):
                out[str(c["tag"])] = str(c.get("zone") or "ZoneI")
        return out

    def base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self.runtime.as_env())
        env.update({
            "CFD_WORKSPACE": str(self.root),
            "CFD_GEOM": str(self.meshes),
            "CFD_WK": str(self.wkcase),
            "CFD_HEMO": str(self.hemo),
            "CFD_RESULTS": str(self.results),
            "CFD_LOGS": str(self.logs),
            "CFD_ENGINE": str(self.engine),
            "CFD_PATIENT": str(self.path),
            "CFD_COMP_TAGS": ",".join(self.comp_tags()),
            "CFD_COMP_ZONES": ",".join(f"{k}:{v}" for k, v in self.comp_tags().items()),
        })
        physics = self.data.get("physics") or {}
        for key, env_name in (
            ("inlet_velocity", "CFD_INLET_VELOCITY"),
            ("density", "CFD_DENSITY"),
            ("viscosity", "CFD_VISCOSITY"),
            ("outlet_pressure", "CFD_OUTLET_PRESSURE"),
            ("period_s", "CFD_PERIOD_S"),
        ):
            if key in physics:
                env[env_name] = str(physics[key])
        solver = self.data.get("solver") or {}
        if solver.get("time_step_s") is not None:
            env["CFD_TIME_STEP"] = str(solver["time_step_s"])
        if solver.get("steady_iterations") is not None:
            env["CFD_STEADY_ITERS"] = str(solver["steady_iterations"])
        if solver.get("cores") is not None:
            env["CFD_CORES"] = str(solver["cores"])
            env["NP"] = str(solver["cores"])
        return env


def _load_json(path: Path) -> dict[str, Any]:
    # utf-8-sig tolerates the BOM that Windows editors commonly add.
    with path.open(encoding="utf-8-sig") as fh:
        return json.load(fh)


def discover_engine() -> Path:
    explicit = _env("CFD_ENGINE")
    if explicit:
        path = Path(explicit).resolve()
        if (path / "pipeline.py").exists():
            return path
        raise SystemExit(f"CFD_ENGINE does not look like an engine directory: {path}")
    candidate = Path(__file__).resolve().parents[1] / "engine"
    if (candidate / "pipeline.py").exists():
        return candidate
    raise SystemExit(
        "Cannot find engine/. Run from the repository clone, or set CFD_ENGINE "
        "to the directory that contains pipeline.py."
    )


def runtime_from(patient_data: dict[str, Any]) -> Runtime:
    rt = patient_data.get("runtime") or {}
    ansys_root = str(_first(_env("CFD_ANSYS_ROOT"), rt.get("ansys_root"), DEFAULT_ANSYS_ROOT))
    ansys_root_path = Path(ansys_root)

    def under(*parts: str) -> Path:
        return ansys_root_path.joinpath(*parts)

    ansys_python = str(_first(
        _env("CFD_ANSYS_PYTHON"), rt.get("ansys_python"),
        under("commonfiles", "CPython", "3_10", "winx64", "Release", "python", "python.exe"),
    ))
    python = str(_first(_env("CFD_PYTHON"), rt.get("python"), sys.executable))
    fluent = str(_first(
        _env("CFD_FLUENT"), rt.get("fluent"),
        under("fluent", "ntbin", "win64", "fluent.exe"),
    ))
    spaceclaim = str(_first(
        _env("CFD_SPACECLAIM"), rt.get("spaceclaim"),
        under("scdm", "SpaceClaim.exe"),
    ))
    prime_root = str(_first(
        _env("CFD_PRIME_ROOT"), rt.get("prime_root"),
        under("meshing", "Prime"),
    ))
    prime_site = str(_first(
        _env("CFD_PRIME_SITE"), rt.get("prime_site"),
        under("meshing", "site"),
    ))
    engine = discover_engine()
    return Runtime(
        ansys_root=ansys_root,
        ansys_python=ansys_python,
        python=python,
        fluent=fluent,
        spaceclaim=spaceclaim,
        prime_root=prime_root,
        prime_site=prime_site,
        prime_port=int(_first(_env("CFD_PRIME_PORT"), rt.get("prime_port"), 50080)),
        cores=int(_first(_env("CFD_CORES"), rt.get("cores"), 4)),
        fluent_batch=str(_first(
            _env("CFD_FLUENT_BATCH"), rt.get("fluent_batch"),
            engine / "fluent_batch.ps1",
        )),
    )


def load_patient(path: str | Path) -> Patient:
    path = Path(path).resolve()
    if not path.exists():
        raise SystemExit(f"patient.json not found: {path}")
    data = _load_json(path)
    rt = runtime_from(data)
    engine = discover_engine()
    patient = Patient(path=path, data=data, runtime=rt, engine=engine)
    return patient


def load_openings(patient: Patient) -> list[dict[str, Any]]:
    """Return the openings list from the patient config.

    ``openings`` may be an inline list or a path to a JSON list.  A path is
    resolved relative to the patient directory.
    """
    raw = patient.data.get("openings")
    if isinstance(raw, list):
        return raw
    geom = patient.data.get("geometry") or {}
    rel = raw or geom.get("openings")
    if not rel:
        raise SystemExit("patient.json needs an openings list or geometry.openings path")
    path = patient.resolve(rel)
    if not path.exists():
        raise SystemExit(f"openings file not found: {path}")
    loaded = _load_json(path)
    if isinstance(loaded, dict):
        loaded = loaded.get("openings") or []
    if not isinstance(loaded, list) or not loaded:
        raise SystemExit(f"openings file is empty or malformed: {path}")
    return loaded
