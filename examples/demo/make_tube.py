"""Generate a small synthetic straight-tube lumen STL and openings.json.

The geometry is deliberately simple (no patient data): a 300 mm long, 12 mm
radius tube with one inlet and one outlet.  It exists so that `cfd doctor`, the
preparation stage and the Watertight -> Prime meshing path can be exercised
without a clinical dataset.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

R = 0.012
Z0, Z1 = 0.0, 0.30
N = 64


def write_stl(path: Path, tris: np.ndarray) -> None:
    with path.open("wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", len(tris)))
        for tri in tris:
            n = np.cross(tri[1] - tri[0], tri[2] - tri[0])
            n /= max(np.linalg.norm(n), 1e-15)
            fh.write(struct.pack("<3f", *n.astype(np.float32)))
            for v in tri:
                fh.write(struct.pack("<3f", *v.astype(np.float32)))
            fh.write(struct.pack("<H", 0))


def ring(z: float) -> np.ndarray:
    a = np.linspace(0, 2 * np.pi, N, endpoint=False)
    return np.column_stack([R * np.cos(a), R * np.sin(a), np.full(N, z)])


def main() -> None:
    out = Path(__file__).resolve().parent / "geometry"
    out.mkdir(parents=True, exist_ok=True)
    lo, hi = ring(Z0), ring(Z1)
    tris = []
    for i in range(N):
        j = (i + 1) % N
        tris.append(np.array([lo[i], lo[j], hi[j]]))
        tris.append(np.array([lo[i], hi[j], hi[i]]))
    c0, c1 = np.array([0.0, 0.0, Z0]), np.array([0.0, 0.0, Z1])
    for i in range(N):
        j = (i + 1) % N
        tris.append(np.array([c0, lo[j], lo[i]]))       # outward -z
        tris.append(np.array([c1, hi[i], hi[j]]))       # outward +z
    write_stl(out / "lumen.stl", np.asarray(tris))
    openings = [
        {"zone": "inlet", "center": c0.tolist(), "normal": [0, 0, -1], "radius": R},
        {"zone": "outlet_arch", "center": c1.tolist(), "normal": [0, 0, 1], "radius": R},
    ]
    (out / "openings.json").write_text(json.dumps(openings, indent=2), encoding="utf-8")
    print(f"wrote {out / 'lumen.stl'} ({len(tris)} triangles) and openings.json")


if __name__ == "__main__":
    main()

