"""Environment doctor.  Never modifies the patient tree."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Patient


def _probe(exe: str, code: str, timeout: int = 60, env: dict[str, str] | None = None) -> dict:
    if not exe or not Path(exe).exists() and not shutil.which(exe):
        return {"ok": False, "detail": f"not found: {exe}"}
    call_env = dict(os.environ)
    if env:
        call_env.update(env)
    try:
        r = subprocess.run([exe, "-c", code], capture_output=True, text=True, timeout=timeout,
                           env=call_env)
        return {"ok": r.returncode == 0, "detail": (r.stdout or r.stderr or "").strip()[-500:]}
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"ok": False, "detail": str(exc)[:500]}


def _exists(path: str | Path) -> dict:
    p = Path(path)
    return {"ok": p.exists(), "detail": str(p)}


def diagnose(patient: Patient | None = None) -> dict:
    checks: dict[str, dict] = {}
    if patient is None:
        return {"ok": False, "checks": {"patient": {"ok": False, "detail": "no patient.json"}}}

    rt = patient.runtime
    checks["ansys_root"] = _exists(rt.ansys_root)
    checks["ansys_python"] = _exists(rt.ansys_python)
    checks["fluent"] = _exists(rt.fluent)
    checks["spaceclaim"] = _exists(rt.spaceclaim)
    checks["prime_root"] = _exists(rt.prime_root)
    checks["prime_site"] = _exists(rt.prime_site)
    checks["powershell"] = {"ok": shutil.which("powershell") is not None,
                            "detail": shutil.which("powershell") or ""}
    checks["python"] = _exists(rt.python)
    checks["ansys.fluent.core"] = _probe(
        rt.ansys_python,
        "import ansys.fluent.core as p; print(p.__version__)",
    )
    prime_env = {"PYTHONPATH": rt.prime_site} if rt.prime_site else None
    checks["ansys.meshing.prime"] = _probe(
        rt.python,
        "import ansys.meshing.prime as p; print(p.__version__)",
        env=prime_env,
    )
    deps = {
        "numpy": "import numpy; print(numpy.__version__)",
        "pandas": "import pandas; print(pandas.__version__)",
        "scipy": "import scipy; print(scipy.__version__)",
        "h5py": "import h5py; print(h5py.__version__)",
        "manifold3d": "import manifold3d; print('ok')",
        "openpyxl": "import openpyxl; print(openpyxl.__version__)",
    }
    for name, code in deps.items():
        checks[name] = _probe(rt.python, code)

    # Patient inputs
    geom = patient.data.get("geometry") or {}
    lumen_rel = geom.get("lumen_stl") or patient.data.get("lumen_stl") or ""
    lumen = str(patient.resolve(lumen_rel)) if lumen_rel else ""
    checks["lumen_stl"] = _exists(lumen) if lumen else {"ok": False, "detail": "missing geometry.lumen_stl"}
    try:
        from .config import load_openings
        ops = load_openings(patient)
        checks["openings"] = {"ok": len(ops) >= 2, "detail": f"n={len(ops)}"}
    except SystemExit as exc:
        checks["openings"] = {"ok": False, "detail": str(exc)}
    zones = patient.data.get("zones") or {}
    checks["zones"] = {"ok": bool(zones), "detail": f"n={len(zones)}"}
    for name, z in zones.items():
        required = ("z_center",)
        missing = [k for k in required if k not in z]
        checks[f"zone:{name}"] = {"ok": not missing,
                                  "detail": "missing " + ",".join(missing) if missing else "ok"}
    checks["patient"] = {"ok": True, "detail": str(patient.path)}

    ok = all(v.get("ok", False) for v in checks.values())
    return {"ok": ok, "patient": patient.patient_id, "runtime": rt.__dict__, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Check the CFD runtime and patient inputs")
    ap.add_argument("--patient", required=True)
    args = ap.parse_args(argv)
    from .config import load_patient
    report = diagnose(load_patient(args.patient))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
