from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cfd.config import load_openings, load_patient

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"


class PatientConfigTest(unittest.TestCase):
    def test_demo_patient_loads(self) -> None:
        p = load_patient(DEMO / "patient.json")
        self.assertEqual(p.case_id, "DEMO")
        self.assertTrue(p.geometry.exists())
        self.assertTrue((p.geometry / "lumen.stl").exists())
        self.assertIn("zones", p.data)

    def test_openings_are_normalised(self) -> None:
        p = load_patient(DEMO / "patient.json")
        ops = load_openings(p)
        self.assertGreaterEqual(len(ops), 2)
        self.assertEqual(ops[0]["zone"], "inlet")
        self.assertIn("normal", ops[0])
        self.assertIn("radius", ops[0])

    def test_relative_paths_resolve_under_patient(self) -> None:
        p = load_patient(DEMO / "patient.json")
        self.assertTrue(str(p.data["geometry"]["lumen_stl"]).endswith("lumen.stl"))
        self.assertTrue(p.geometry.is_dir())


class CliSmokeTest(unittest.TestCase):
    def test_help_exits_zero(self) -> None:
        out = subprocess.run(
            [sys.executable, "-m", "cfd", "--help"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("doctor", out.stdout)
        self.assertIn("run", out.stdout)

    def test_dry_run_does_not_launch_ansys(self) -> None:
        out = subprocess.run(
            [sys.executable, "-m", "cfd", "run", "--patient",
             str(DEMO / "patient.json"), "--stage", "mesh", "--dry-run"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("would run mesh", out.stdout)


if __name__ == "__main__":
    unittest.main()
