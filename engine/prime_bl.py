"""Import a Fluent boundary-only legacy .msh into Prime, remesh the surface,
add prism boundary layers, and export a Fluent legacy .msh.

The input MUST be a boundary-only mesh exported from Fluent Meshing with
``/file/hdf-files no`` and ``/file/write-boundaries``.  This preserves the
Watertight topology and zone names; do not feed a STEP/STL here.
"""
from __future__ import annotations

import os
import sys

# ANSYS ships PyPrimeMesh under <ansys_root>/meshing/site.  Make that explicit
# so the tool works with a Python that does not have ansys-meshing-prime pip'd.
_prime_site = os.environ.get("CFD_PRIME_SITE", "")
if _prime_site and _prime_site not in sys.path:
    sys.path.insert(0, _prime_site)

import ansys.meshing.prime as prime
from ansys.meshing.prime.lucid import Mesh

ROOT = os.environ.get("CFD_PRIME_ROOT", r"D:\ANSYS2024R2\ANSYS Inc\v242\meshing\Prime")
PORT = int(os.environ.get("CFD_PRIME_PORT", "50080"))
LABEL = os.environ.get("CFD_PRISM_LABEL", "wall")

src, out = sys.argv[1], sys.argv[2]
layers = int(sys.argv[3]) if len(sys.argv) > 3 else 5
first = float(sys.argv[4]) if len(sys.argv) > 4 else 2e-5
min_size = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0004
max_size = float(sys.argv[6]) if len(sys.argv) > 6 else 0.002


def main() -> None:
    c = prime.launch_prime(prime_root=ROOT, port=PORT, timeout=600)
    try:
        m = c.model
        fio = prime.FileIO(m)
        res = fio.import_fluent_meshing_meshes([src], prime.ImportFluentMeshingMeshParams(m))
        print("[OK] import", res, flush=True)
        print("parts", [(p.id, p.name) for p in m.parts], flush=True)
        if not m.parts:
            raise SystemExit("Prime imported no parts")
        part = m.parts[0]
        print("volumes", list(part.get_volumes()),
              "cell zonelets", len(list(part.get_cell_zonelets())), flush=True)

        try:
            Mesh(m).surface_mesh(min_size=min_size, max_size=max_size)
            print("[OK] surface remesh", flush=True)
        except Exception as exc:  # noqa: BLE001 - report and let AutoMesh decide
            print("[WARN] surface remesh", str(exc)[:500], flush=True)

        try:
            vols = list(part.get_volumes())
            if vols:
                part.delete_volumes(vols, prime.DeleteVolumesParams(m))
            print("[OK] delete volumes", list(part.get_volumes()), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("[WARN] delete volumes", str(exc)[:500], flush=True)

        try:
            part.compute_closed_volumes(prime.ComputeVolumesParams(m))
            print("[OK] recompute volumes", list(part.get_volumes()), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("[WARN] recompute volumes", str(exc)[:300], flush=True)
        if not list(part.get_volumes()):
            raise SystemExit("Prime could not close the boundary mesh into a volume")

        pc = m.control_data.create_prism_control()
        pc.set_surface_scope(prime.ScopeDefinition(m, part_expression=part.name,
                                                   label_expression=LABEL))
        pc.set_growth_params(prime.PrismControlGrowthParams(
            m, n_layers=layers, growth_rate=1.2, first_height=first))
        pc.set_volume_scope(prime.ScopeDefinition(
            m, entity_type=prime.ScopeEntity.VOLUME, part_expression=part.name,
            zone_expression="*"))
        print("[OK] prism control", pc.id, "label", LABEL, flush=True)

        vc = m.control_data.create_volume_control()
        vc.set_params(prime.VolumeControlParams(m, cell_zonelet_type=prime.CellZoneletType.FLUID))
        vc.set_scope(prime.ScopeDefinition(
            m, part_expression=part.name, zone_expression="*",
            entity_type=prime.ScopeEntity.VOLUME,
            evaluation_type=prime.ScopeEvaluationType.ZONES))
        params = prime.AutoMeshParams(m)
        params.volume_fill_type = prime.VolumeFillType.TET
        params.prism_control_ids = [pc.id]
        params.volume_control_ids = [vc.id]
        prime.AutoMesh(m).mesh(part.id, params)
        print("[OK] automesh", flush=True)
        fio.export_fluent_meshing_mesh(out, prime.ExportFluentMeshingMeshParams(m))
        print("[OK] export", out, flush=True)
    finally:
        c.exit()


if __name__ == "__main__":
    main()
