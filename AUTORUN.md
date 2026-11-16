# AUTORUN -- one patient, no manual GUI

This is the short version. The full reference is in `README.md`,
`docs/PATIENT.md` and `docs/TROUBLESHOOTING.md`.

## 1. Check

```powershell
python -m cfd doctor --patient patients\P01\patient.json
```

All checks must be `ok`. A missing geometry is `exit 2`; a missing ANSYS
component is a hard failure.

## 2. Prepare

```powershell
python -m cfd run --patient patients\P01\patient.json --stage prep
```

This extends the openings, cuts each balloon gap, writes the per-case STL/STEP
and writes `cases` back into `patient.json`. Inspect
`geometry\<case_id>\case_manifest.json`; `step_ok` must be true.

## 3. Mesh

```powershell
python -m cfd run --patient patients\P01\patient.json --stage mesh
```

This is the expensive stage. It builds the Watertight volume, exports the
boundary-only legacy mesh, adds the PyPrimeMesh prism layer and converts back
to `.msh.h5`. Watch `logs\mesh_<tag>.log` and `logs\mesh_<tag>_prime.log`.

The cap faces must arrive with named zones (`inlet`, `outlet_arch`,
`outlet_renal`, `outlet_iliac`). If the mesh has only a wall, stop and read
`docs/TROUBLESHOOTING.md`; do not continue to `solve`.

## 4. Solve

```powershell
python -m cfd run --patient patients\P01\patient.json --stage solve
```

Steady -> pulsatile transient -> systole wall-shear export. Keep the machine
otherwise idle; two concurrent Fluent sessions are the practical maximum.

## 5. Everything else

```powershell
python -m cfd run --patient patients\P01\patient.json --stage all
```

Stages already completed are skipped unless `--force` is given. The acceptance
gates are:

- flux error < 1 % in every exported state;
- cycle drift < 5 percentage points;
- 25 wall-shear frames per case;
- every case present in `results\BL_WSS_METRICS.json`.

`--dry-run` prints the plan without launching ANSYS.

## 6. Deliver

The deliverables are under the patient directory:

```text
results\BL_WSS_METRICS.csv
results\CASE_MASTER_RESULTS.csv
results\CASE_MASTER_RESULTS.xlsx
reports\REPORT.md
validation\doctor.json
validation\validate.json
```

Do not report a wall-shear number without the mesh, first-layer height, flux
error and cycle drift that produced it.
