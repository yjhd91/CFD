"""Build one BL mesh: STEP -> Watertight volume -> boundary legacy .msh
-> Prime surface remesh + prism -> Fluent .msh.h5.

The Fluent Watertight topology is deliberately retained.  Prime is used only
for the surface/prism operation, never to rebuild the gap topology from
STEP/STL (that route is numerically unstable for this geometry).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
ANSYS_PY = os.environ.get("CFD_ANSYS_PYTHON", sys.executable)
PRIME_PY = os.environ.get("CFD_PYTHON", sys.executable)
FLUENT = os.environ.get("CFD_FLUENT", "")
LOGS = Path(os.environ.get("CFD_LOGS", Path.cwd() / "logs"))


def run(cmd: list[str], log: Path, timeout: int = 7200) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write("\n$ " + " ".join(cmd) + "\n")
        fh.flush()
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed rc={proc.returncode}; log={log}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--layers", type=int, default=5)
    ap.add_argument("--first-mm", type=float, default=0.02)
    ap.add_argument("--rate", type=float, default=1.2)
    ap.add_argument("--min-mm", type=float, default=0.4)
    ap.add_argument("--max-mm", type=float, default=2.0)
    ap.add_argument("--cores", type=int, default=4)
    args = ap.parse_args()

    wd = Path(args.workdir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    final = wd / f"mesh_{args.tag}_bc.msh.h5"
    if final.exists() and final.stat().st_size > 1_000_000:
        print("[SKIP] exists", final)
        return

    wt = wd / f"wt_{args.tag}.msh.h5"
    boundary = wd / f"boundary_{args.tag}.msh"
    prime_out = wd / f"prime_{args.tag}.msh"

    # 1) Watertight volume mesh.  The WT "Add Boundary Layers" step is not used;
    #    Prime adds the real prism layers from the boundary-only mesh.
    if not wt.exists():
        run([ANSYS_PY, str(ENGINE / "wt_mesh.py"), args.step, str(wt),
             str(args.min_mm / 1000.0), str(args.max_mm / 1000.0), "0",
             str(args.first_mm / 1000.0), str(args.rate)],
            LOGS / f"mesh_{args.tag}_wt.log", timeout=7200)

    # 2) Boundary-only legacy .msh: names and Watertight topology are preserved.
    journal = wd / f"export_boundary_{args.tag}.jou"
    journal.write_text("\n".join([
        f'/file/read-mesh "{wt.as_posix()}"',
        '/file/hdf-files', 'no',
        '/file/write-boundaries', f'"{boundary.as_posix()}"', '*', '()',
        'exit', 'yes',
    ]) + "\n", encoding="ascii")
    run([FLUENT, "3ddp", "-meshing", "-g", f"-t{args.cores}", "-i", str(journal)],
        LOGS / f"mesh_{args.tag}_boundary.log", timeout=3600)

    # 3) Prime: surface remesh -> closed volume -> prism -> export legacy .msh.
    if not prime_out.exists():
        run([PRIME_PY, str(ENGINE / "prime_bl.py"), str(boundary), str(prime_out),
             str(args.layers), str(args.first_mm / 1000.0),
             str(args.min_mm / 1000.0), str(args.max_mm / 1000.0)],
            LOGS / f"mesh_{args.tag}_prime.log", timeout=7200)

    # 4) Convert Prime's legacy .msh to the HDF5 mesh Fluent Solver reads.
    journal2 = wd / f"convert_{args.tag}.jou"
    journal2.write_text("\n".join([
        f'/file/read-mesh "{prime_out.as_posix()}"',
        '/file/write-mesh', f'"{final.as_posix()}"',
        'exit', 'yes',
    ]) + "\n", encoding="ascii")
    run([FLUENT, "3ddp", "-meshing", "-g", f"-t{args.cores}", "-i", str(journal2)],
        LOGS / f"mesh_{args.tag}_convert.log", timeout=3600)
    if not final.exists():
        raise RuntimeError(f"conversion did not write {final}")
    shutil.copy2(final, wd / f"mesh_{args.tag}.msh.h5")
    print("[OK]", final, flush=True)


if __name__ == "__main__":
    main()
