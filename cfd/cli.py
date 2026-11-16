from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import load_patient
from .doctor import diagnose
from .runner import STAGES, Runner

TEMPLATE = Path(__file__).resolve().parent / "templates" / "patient.json"


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir).expanduser().resolve()
    if target.exists() and any(target.iterdir()) and not args.force:
        raise SystemExit(f"target exists and is not empty: {target} (use --force)")
    target.mkdir(parents=True, exist_ok=True)
    for sub in ("geometry", "work", "meshes", "results", "reports", "logs", "validation"):
        (target / sub).mkdir(exist_ok=True)
    patient = target / "patient.json"
    text = TEMPLATE.read_text(encoding="utf-8")
    case_id = args.case_id or args.patient_id
    text = text.replace("__PATIENT_ID__", args.patient_id).replace("__CASE_ID__", case_id)
    if patient.exists() and not args.force:
        raise SystemExit(f"{patient} already exists (use --force)")
    patient.write_text(text, encoding="utf-8")
    print(f"created {target}")
    print("next:")
    print(f"  1. put lumen.stl and openings.json under {target / 'geometry'}")
    print(f"  2. edit {patient}")
    print(f"  3. python -m cfd doctor --patient {patient}")
    print(f"  4. python -m cfd run --patient {patient} --stage all")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    report = diagnose(load_patient(args.patient))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


def cmd_run(args: argparse.Namespace) -> int:
    patient = load_patient(args.patient)
    runner = Runner(patient, force=args.force, dry_run=args.dry_run)
    stages = args.stage or ["all"]
    runner.run(stages)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    patient = load_patient(args.patient)
    runner = Runner(patient)
    runner.validate()
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cfd", description="Automated aortic CFD patient pipeline")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create a patient directory from the template")
    p.add_argument("--dir", required=True)
    p.add_argument("--patient-id", default="P01")
    p.add_argument("--case-id", default="")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="check ANSYS, Python packages and patient inputs")
    p.add_argument("--patient", required=True)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("run", help="run one or more stages")
    p.add_argument("--patient", required=True)
    p.add_argument("--stage", action="append", choices=STAGES + ["all"],
                   help="repeatable; default all")
    p.add_argument("--force", action="store_true", help="rebuild meshes / ignore cached stages")
    p.add_argument("--dry-run", action="store_true", help="print commands, run nothing")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("validate", help="re-run the acceptance checks on existing results")
    p.add_argument("--patient", required=True)
    p.set_defaults(func=cmd_validate)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
