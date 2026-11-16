# Patient configuration reference

`patient.json` is the single input that drives every stage. Paths in the file
may be absolute or relative to the patient directory. Environment variables
with the same meaning as the `runtime` fields override the file.

A minimal complete example is `examples/demo/patient.json`; the template is
`cfd/templates/patient.json`.

## Top-level fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `patient_id` | string | yes | Anonymous study identifier; never put a name or MRN here |
| `case_id` | string | no | Folder name under `geometry/`; defaults to `patient_id` |
| `description` | string | no | Free text |
| `geometry.lumen_stl` | path | yes | Closed lumen surface, metres |
| `geometry.openings` | path | yes | JSON list of capped openings |
| `runtime` | object | no | ANSYS and Python paths; blanks are auto-discovered |
| `mesh` | object | yes | Surface and prism sizing |
| `solver` | object | yes | Cycle count, time step, cores, steady iterations |
| `physics` | object | yes | Inlet velocity, density, viscosity, outlet pressure, period |
| `zones` | object | yes | One or more balloon zones |
| `source_cases` | array | no | Extra cases that reuse a base mesh (e.g. compression-only) |
| `cases` | array | no | Written by `prep` after reading `case_manifest.json` |

## Geometry

`lumen.stl` must be a closed, consistently oriented triangle surface in metres.
The preparation stage checks the openings against the surface and extends each
opening outward into a cylindrical stem before meshing. Units are never guessed.

`openings.json`:

```json
[
  {"zone": "inlet",        "center": [0, 0, 0.00], "normal": [0, 0, -1], "radius": 0.012},
  {"zone": "outlet_arch",  "center": [0.02, 0.01, 0.28], "normal": [0.2, 0.1, 0.97], "radius": 0.006}
]
```

- `zone` becomes the Fluent zone name. Use `inlet` for the inlet; any name
  starting with `outlet` is treated as an outlet. Aortic studies typically use
  `outlet_arch`, `outlet_renal`, `outlet_iliac` (or numbered variants).
- `center` is on the opening plane, in metres.
- `normal` points out of the lumen. It is normalized internally.
- `radius` is the opening radius in metres.

At least two openings are required. The mesh must have one inlet and at least
one outlet after preparation.

## Runtime

```json
"runtime": {
  "ansys_root": "D:\\ANSYS2024R2\\ANSYS Inc\\v242",
  "ansys_python": "",
  "python": "",
  "fluent": "",
  "spaceclaim": "",
  "prime_root": "",
  "prime_site": "",
  "prime_port": 50080,
  "cores": 4,
  "fluent_batch": ""
}
```

Leave a field empty to derive it from `ansys_root`. The defaults are the
standard 2024 R2 layout:

- Python: `commonfiles/CPython/3_10/winx64/Release/python/python.exe`
- Fluent: `fluent/ntbin/win64/fluent.exe`
- SpaceClaim: `scdm/SpaceClaim.exe`
- Prime: `meshing/Prime`
- Prime site-packages: `meshing/site`

`ansys_python` is used only for `mesh`, `solve` and `hemo` (PyFluent and
PyPrimeMesh). `python` is used for `prep`, `qc`, `metrics`, `master`, `report`
and `validate`; if empty it means the interpreter running `cfd`.

## Mesh

```json
"mesh": {
  "min_mm": 0.4,
  "max_mm": 2.0,
  "layers": 5,
  "first_mm": 0.02,
  "growth": 1.2
}
```

- `min_mm` / `max_mm`: Watertight surface mesh bounds on the extended geometry.
- `layers`: prism layer count added by PyPrimeMesh.
- `first_mm`: first-layer height in millimetres.
- `growth`: prism growth rate.

The boundary layer is added only after the Watertight volume exists. The
Fluent-created gap topology is retained; do not replace it with a mesh rebuilt
from the STL/STEP.

## Solver

```json
"solver": {
  "cycles": 3,
  "time_step_s": 0.002,
  "cores": 4,
  "steady_iterations": 400
}
```

- `cycles`: number of cardiac cycles in the transient run. Two cycles are the
  minimum for the cycle-drift check; three are recommended.
- `time_step_s`: transient time step. With a 0.5 s period and 0.002 s, one
  cycle is 250 steps.
- `cores`: Fluent processor count.
- `steady_iterations`: steady-state iteration cap before the transient.

## Physics

```json
"physics": {
  "inlet_velocity": 0.0487,
  "density": 1050.0,
  "viscosity": 0.0035,
  "outlet_pressure": 8000.0,
  "period_s": 0.5
}
```

- `inlet_velocity`: mean inlet velocity in m/s for the steady case. The
  transient inlet is the Womersley profile in the UDF; this value scales it.
- `density` / `viscosity`: Newtonian blood properties in SI units.
- `outlet_pressure`: initial gauge pressure at the three outlet groups, in Pa.
- `period_s`: cardiac period used by the UDF and the cycle-drift parser.

The three outlet groups (arch, renal, iliac) share a Windkessel model whose
parameters are compiled into the UDF. If your case has a different outlet
grouping, edit `engine/wk_aorta_ext4.c` and document the change.

## Zones

```json
"zones": {
  "ZoneI": {
    "enabled": true,
    "z_center": 0.25,
    "center_xy": [0.0, 0.0],
    "gaps_mm": [1.0, 1.44],
    "length_mm": 40.0,
    "end_mm": 6.0,
    "r_max_m": 0.012,
    "complete": false,
    "stem": {"enabled": true},
    "z_min_m": 0.20,
    "z_max_m": 0.30
  }
}
```

- `z_center`: axial centre of the balloon in metres.
- `center_xy`: lumen centre in the cross-section plane.
- `gaps_mm`: annular gap heights to build. Each becomes one case.
- `length_mm`: balloon length.
- `end_mm`: distance from the opening used to place the balloon end.
- `r_max_m`: maximum lumen radius used to size the balloon.
- `complete`: if true, a complete occlusion (no open gap) is also built.
- `stem`: whether to extend the nearest opening for the balloon stem.
- `z_min_m` / `z_max_m`: optional axial window for per-zone metrics. If omitted,
  the metrics engine falls back to `0.20-0.30 m` (Zone I) and `0.03-0.10 m`
  (Zone III). Set these explicitly for a new anatomy.

`prep` writes the generated case list into `patient.json` under `cases`. Do not
hand-edit that list unless you know the manifest tags.

## Source cases

`source_cases` builds cases that share a base mesh but change the balloon
source term (for example compression-only cases):

```json
"source_cases": [
  {"tag": "P01_comp_i", "label": "compression I", "zone": "ZoneI", "base": "P01_zonei_g10"}
]
```

`base` must name an existing case tag. `mesh` copies its two mesh files; `solve`
reuses them. This is intentional: a source-term study must not change the mesh.

## Generated files

`prep` writes, under `geometry/<case_id>/`:

- `case_manifest.json` - tags, STL/STEP paths, mesh settings, `step_ok`;
- `<case_id>_ext.stl` / `.json` - extended lumen;
- `openings_base.json` - normalized openings;
- `<tag>.stl`, `<tag>_sc.stp` - per-case geometry;
- `complete_zone_centers.json` - balloon centre per zone, used by the UDF.

The manifest is the contract between `prep` and `mesh`/`solve`.
