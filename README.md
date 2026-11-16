# CFD -- patient-specific aortic CFD, from STL to wall-shear metrics

`cfd` is a Windows-first, scriptable pipeline for patient-specific aortic
computational fluid dynamics. It takes a lumen surface (STL), a set of outlet
openings, and a small JSON configuration, then drives ANSYS Fluent and
PyPrimeMesh to produce:

- a Watertight-Genesis volume mesh;
- a prism boundary layer added by PyPrimeMesh **without rebuilding the
  Watertight gap topology**;
- a steady solution and a pulsatile transient solution driven by a
  three-outlet Windkessel UDF;
- cardiac-phase wall-shear frames and derived metrics (TAWSS, OSI, RRT, LSA,
  ECAP, transWSS, WSSG);
- a flux audit, cycle-drift audit, CSV/XLSX master table, report and
  validation record.

This is research software. It is **not** a medical device and must not be used
for diagnosis or treatment.

## Why this repository exists

The hard part of this geometry is not the Navier-Stokes solve. It is getting a
boundary layer onto a balloon-induced annular gap without changing the gap
topology. Rebuilding the gap from STL/STEP in a mesher produces a topologically
different narrow region, and the transient solve then fails at the first step
with a floating-point exception.

This pipeline keeps the Fluent Watertight volume as the source of truth:

1. Fluent Watertight meshes the extended STEP and writes a volume mesh.
2. Fluent Meshing exports **boundary-only** faces as a legacy `.msh`, preserving
   the zone names attached to the caps.
3. PyPrimeMesh imports that boundary mesh, remeshes only the surface, computes
   closed volumes, adds prism layers and fills with tetrahedra.
4. Fluent converts the result back to `.msh.h5`.

The result is a mesh with a real boundary layer whose gap topology is still the
one Fluent produced.

## Status

The packaging, configuration layer, doctor, preparation stage, and the
Watertight -> Prime -> Fluent mesh path are exercised on this machine. The
synthetic demo is a smoke test for `doctor`, `prep` and `mesh`.

A complete transient solve additionally needs the cap faces to arrive with
**named zones** (`inlet`, `outlet_arch`, `outlet_renal`, `outlet_iliac`). The
synthetic STEP created by `prep` currently has one solid and unnamed caps, so
`mesh` produces a wall-only boundary and `solve` stops with a clear
`KeyError`. Cases whose STEP carries named cap groups can run the whole chain;
automatic cap naming is the next engineering item. See
`docs/TROUBLESHOOTING.md`.

The 12-case REBOA study, patient CT data and production meshes are **not** in
this repository.

## Requirements

| Component | Requirement |
|---|---|
| OS | Windows 10/11 x64 (the PyFluent launcher shim and batch files are Windows-specific) |
| ANSYS | **2024 R2** (`v242`) with Fluent, Fluent Meshing, SpaceClaim and PyPrimeMesh |
| License | A valid ANSYS license; this repository does not provide one |
| Python | 3.10+ for the `cfd` CLI; NumPy, SciPy, h5py, pandas, manifold3d, openpyxl |
| Disk | Several GB per patient for meshes, case/data files and frames |

PyPrimeMesh is used from the ANSYS installation
(`<ANSYS_ROOT>\meshing\site`), not from PyPI. The CLI puts that directory on
`PYTHONPATH` for the Prime subprocess.

## Install

```powershell
git clone https://github.com/yjhd91/CFD.git
cd CFD
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
```

`pip install -e .` installs NumPy, SciPy, h5py, pandas, manifold3d and
openpyxl. `ansys-fluent-core` is normally already present in the ANSYS Python;
if you use a separate Python for the solver stages, install a matching
`ansys-fluent-core` there too.

## Configure ANSYS

The CLI auto-discovers `D:\ANSYS2024R2\ANSYS Inc\v242`. On another machine set
one environment variable before running anything:

```powershell
$env:CFD_ANSYS_ROOT = "C:\Program Files\ANSYS Inc\v242"
```

Every path can also be written into the `runtime` block of `patient.json`;
environment variables win over the file.

| Variable | Meaning |
|---|---|
| `CFD_ANSYS_ROOT` | ANSYS `v242` installation root |
| `CFD_ANSYS_PYTHON` | Python used for Fluent/PyFluent stages |
| `CFD_PYTHON` | Python used for the `cfd` CLI (defaults to the current interpreter) |
| `CFD_FLUENT` | `fluent.exe` |
| `CFD_SPACECLAIM` | `SpaceClaim.exe` |
| `CFD_PRIME_ROOT` | `<ANSYS_ROOT>\meshing\Prime` |
| `CFD_PRIME_SITE` | `<ANSYS_ROOT>\meshing\site` |
| `CFD_PRIME_PORT` | PyPrimeMesh gRPC port (default `50080`) |
| `CFD_FLUENT_BATCH` | `engine\fluent_batch.ps1` (auto-detected) |
| `CFD_ENGINE` | directory containing `pipeline.py` (auto-detected) |

Check the installation without running a mesh:

```powershell
python -m cfd doctor --patient examples\demo\patient.json
```

`doctor` writes `examples\demo\validation\doctor.json`. It checks the ANSYS
binaries, the Python imports, the geometry files, the opening count and the zone
definitions. A missing geometry is a normal `exit 2`; a missing ANSYS component
is a hard failure.

## Run the synthetic demo

The demo is a straight 300 mm tube. It contains no patient data.

```powershell
python -m cfd run --patient examples\demo\patient.json --stage prep
python -m cfd run --patient examples\demo\patient.json --stage mesh
```

The `mesh` stage builds the Watertight volume, exports the boundary-only legacy
mesh, adds the Prime prism layer and converts back to `.msh.h5`. Inspect
`examples\demo\logs\mesh_DEMO_zonei_g10.log` if it fails.

The demo stops at the boundary-condition step because its synthetic STEP has no
named caps. To run a full case, supply a STEP whose cap faces are named as
described in `docs/PATIENT.md`.

`--dry-run` prints the plan and never launches ANSYS. `--force` re-runs a
completed stage.

## Patient input

A patient directory contains:

```text
patient.json
geometry\lumen.stl
geometry\openings.json
```

`openings.json` is a list of objects with `name`, `center` (metres), `normal`
(outward unit vector) and `radius` (metres). The first opening is the inlet.
The names `inlet`, `outlet_arch`, `outlet_renal`, `outlet_iliac` are the
contract used by the solver.

`patient.json` controls the runtime, mesh sizes, solver settings, balloon-zone
windows and the per-gap cases. Start from `cfd/templates/patient.json`; the full
field reference is in `docs/PATIENT.md`.

## Stages

| Stage | What it does |
|---|---|
| `check` | validates geometry, openings and configuration |
| `prep` | extends openings, cuts each balloon gap, writes per-tag STL/STEP |
| `mesh` | Watertight -> boundary legacy -> Prime prism -> Fluent `.msh.h5` |
| `solve` | steady -> pulsatile transient -> systole WSS export |
| `qc` | flux balance and cycle drift |
| `hemo` | 25 cardiac-phase WSS frames |
| `metrics` | TAWSS/OSI/RRT/LSA/ECAP/transWSS/WSSG |
| `master` | CSV/XLSX master table |
| `report` | Markdown report |
| `validate` | acceptance gates |

`python -m cfd run --patient <dir> --stage all` runs them in order and skips
completed stages unless `--force` is given.

## Validation gates

A case is accepted only when all of the following hold:

- **Flux balance**: `abs(sum(Q_out) - Q_in) / Q_in < 1 %` in every exported
  state.
- **Cycle drift**: the mean outlet flow in cycle 2 differs from cycle 1 by less
  than 5 percentage points.
- **Frames**: at least 25 wall-shear frames per case.
- **Metrics**: every case appears in `results/BL_WSS_METRICS.json`.

## Metrics

- `TAWSS = mean(|tau_w|)` over the cardiac cycle.
- `OSI = 0.5 * (1 - |mean(tau_w)| / mean(|tau_w|))`.
- `RRT = 1 / ((1 - 2*OSI) * TAWSS)`, reported as a median over
  `TAWSS >= 0.1 Pa` and `OSI <= 0.49`.
- `LSA` is the relative area with `TAWSS < 0.5 Pa`.
- `ECAP = OSI / TAWSS`, reported as the median over `TAWSS >= 0.1 Pa`.
- `transWSS` is the time-mean magnitude of the component perpendicular to the
  time-mean WSS direction.
- `WSSG` is the spatial gradient magnitude of TAWSS.

Zone windows are axial in metres and configurable through
`zones[*].z_min_m` / `z_max_m`; the defaults match the REBOA aortic model.

## Repository layout

```text
cfd/            CLI, config, runner, doctor
engine/         ANSYS-facing scripts (Watertight, Prime, Fluent, metrics)
examples/demo/  synthetic tube and patient.json
docs/           patient schema, method, troubleshooting
tests/          configuration and CLI tests
```

## Limitations

- The pipeline assumes a single lumen with capped openings and a balloon gap.
  It does not segment CT/MRI, place the balloon, or choose clinical parameters.
- Automatic naming of unnamed cap faces is not implemented; supply named caps
  or extend `engine/mesh_tag.py`.
- The demo tube is a numerical smoke test, not a validation against
  measurements.
- Wall-shear metrics are mesh-sensitive. Report the mesh, first-layer height,
  y+ estimate and flux/cycle gates with any result.

## License

MIT. See [LICENSE](LICENSE).
