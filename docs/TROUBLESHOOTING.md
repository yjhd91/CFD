# Troubleshooting

## `doctor` says the geometry is missing

`python -m cfd doctor` exits 2 when `geometry.lumen_stl` or
`geometry.openings` does not exist. This is a normal input error, not an ANSYS
failure. Check that relative paths in `patient.json` are relative to the
patient directory (the directory containing `patient.json`).

## `No module named 'ansys.meshing'` / `No module named 'ansys'`

The `cfd` CLI is using a Python that cannot see the ANSYS packages. Two separate
Pythons are involved:

- `mesh`, `solve` and `hemo` use `runtime.ansys_python`;
- `prep`, `qc`, `metrics`, `master`, `report` and `validate` use the interpreter
  that runs `cfd`.

Set `CFD_ANSYS_PYTHON` to the `v242` CPython and `CFD_PRIME_SITE` to
`<ANSYS_ROOT>\meshing\site`. `doctor` prints both paths and probes the imports.

## `No module named 'grpc'` during `launch_prime`

Install the gRPC runtime into the Python that launches Prime:

```powershell
python -m pip install grpcio protobuf
```

PyPrimeMesh is a client/server package. The server comes from ANSYS; the Python
client still needs `grpcio`.

## `runPrime.bat not found`

`CFD_PRIME_ROOT` must point at the `Prime` directory, not at the ANSYS root:

```text
D:\ANSYS2024R2\ANSYS Inc\v242\meshing\Prime
```

not

```text
D:\ANSYS2024R2\ANSYS Inc\v242
```

## `Provided file extension is not supported. Supported extensions are .msh and .msh.gz`

PyPrimeMesh 0.6.x does not import Fluent's HDF5 `.msh.h5`. The pipeline converts
the Watertight volume to a legacy `.msh` boundary file first:

```text
/file/hdf-files no
/file/write-boundaries "boundary_<tag>.msh" * ()
```

Do not switch the export back to HDF5 for the Prime step.

## `Already volume meshed` or `Overlapping faces along multi-connection found`

This happens when a full volume mesh is imported and then re-meshed in Prime,
or when the volume is deleted before the surface is remeshed. The supported
route is:

1. export boundary faces only from Fluent Meshing;
2. import that boundary `.msh` into Prime;
3. remesh the surface;
4. compute closed volumes;
5. add prisms and fill with TET.

Do not import a volume mesh and delete its volumes.

## Transient aborts at step 1 with `floating point exception`

This is the failure mode that motivated the boundary-only route. A gap topology
rebuilt from STL/STEP can be geometrically plausible and still fail on the first
transient step. Check, in order:

1. Was the mesh built from the Fluent Watertight boundary export, not from a
   Prime-rebuilt STL/STEP surface?
2. Does the case have a positive gap volume and no inverted prisms?
3. Does the steady case converge before the transient?
4. Is the time step small enough for the gap? The default is 0.002 s; reducing
   it does not fix a topology problem, but it can rule out a CFL-only failure.

Do not paper over this by mixing an unvalidated boundary-layer mesh into a
comparison table. Report the mesh and the failure.

## `SpaceClaim.exe` not found

Set `CFD_SPACECLAIM` or `runtime.spaceclaim`. The standard location is:

```text
<ANSYS_ROOT>\scdm\SpaceClaim.exe
```

The preparation stage uses SpaceClaim to convert the per-case STL to STEP. If
SpaceClaim is unavailable, the STEP conversion fails and `step_ok` is false;
the mesh stage will then have no STEP to read.

## `git` cannot prompt for credentials

DSH/CI shells often have interactivity disabled. Do not retry `git credential
fill` in a loop. Use one of:

- a fine-grained PAT with `Contents: read/write` and `Metadata: read`, supplied
  through a credential helper or an environment-based askpass;
- the GitHub OAuth device flow, completed by a human in a browser.

Never commit a token to the repository.

## The demo meshes but the real case is slow

The demo is a 24 mm tube. A patient aorta with 13 openings and a 1 mm annular
gap has a much larger surface and a much thinner near-wall region. Expect the
Watertight mesh and the Prime prism step to dominate wall time. Keep at most two
Fluent sessions alive; each session holds a license and a large commit charge.

## Where to look

| Symptom | File |
|---|---|
| stage failed | `<patient>/logs/<stage>.log` |
| Prime failed | `<patient>/logs/mesh_<tag>_prime.log` |
| Fluent failed | `<patient>/logs/mesh_<tag>_fluent*.log`, solver logs |
| QC failed | `<patient>/results/FLUX_ACCOUNT.json`, `CYCLE_DRIFT.json` |
| doctor failed | `<patient>/validation/doctor.json` |
