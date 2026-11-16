# Method

This document states what the code computes. It is deliberately narrower than a
paper: the equations, the discretisation choices and the acceptance gates.

## 1. Geometry

The input lumen is a closed triangulated surface in metres. Each opening is
extended outward along its normal into a cylindrical stem so that the inlet and
outlet boundary conditions sit away from the curved lumen. The balloon is a
cylindrical shell around the lumen axis; subtracting it leaves an annular gap of
`g` millimetres. The gap is the pathology model, not a meshing artifact.

## 2. Volume mesh and boundary layer

The extended geometry is meshed with the Fluent Watertight workflow
(poly-hexcore volume fill). The Watertight mesh is the topological source of
truth for the gap.

The boundary layer is added in a second step:

1. Fluent Meshing writes a boundary-only legacy `.msh` with
   `/file/write-boundaries`, preserving the original zone names.
2. PyPrimeMesh imports that file, remeshes the surface, computes closed volumes,
   and adds prism layers on the wall.
3. The prism parameters are: 5 layers, first height 0.02 mm, growth rate 1.2.
4. PyPrimeMesh fills the remaining volume with tetrahedra.
5. Fluent converts the result to `.msh.h5`.

The reason for this ordering is numerical, not cosmetic. Rebuilding the annular
gap from STL/STEP in a surface mesher changes the narrow-gap topology; the
transient solver then aborts at the first step with a floating-point exception,
even with no prism layers and with a tenfold smaller time step. Keeping the
Watertight topology and remeshing only its boundary avoids that failure mode.

## 3. Flow model

- Laminar, incompressible, Newtonian.
- Density 1050 kg/m3, dynamic viscosity 0.0035 Pa s.
- Steady inlet mean velocity 0.0487 m/s; three outlet groups at 8000 Pa gauge.
- Pulsatile inlet: Womersley profile from the UDF; three-element Windkessel at
  the arch, renal and iliac outlet groups.
- Steady case: hybrid initialisation, 400 iteration cap.
- Transient: 0.002 s time step, 20 inner iterations, `cycles` cardiac cycles
  (default 3), followed by a 76-step systole export.

The UDF is `engine/wk_aorta_ext4.c`. Its three Windkessel parameter sets and the
balloon source term are compiled per case; the source term is enabled only for
compression cases.

## 4. Wall-shear frames

After the transient, the solver exports 25 phase-locked wall-shear samples over
one cardiac period, 10 solver steps apart. Each frame is a table

```text
x y z tau_x tau_y tau_z
```

on every wall face centre. The frames are the only input to the metrics stage.

## 5. Metrics

Let `tau(t)` be the wall-shear vector at a wall point at phase `t`, and let
`|tau|` be its magnitude. With `N = 25` frames:

```text
TAWSS   = (1/N) sum |tau(t_i)|
OSI     = 0.5 * (1 - |(1/N) sum tau(t_i)| / TAWSS)
RRT     = 1 / ((1 - 2 OSI) * TAWSS)
ECAP    = OSI / TAWSS
LSA%    = 100 * fraction(TAWSS < 0.5 Pa)
```

`transWSS` is the time mean of the component of `tau(t)` perpendicular to the
time-mean direction `e = mean(tau)/|mean(tau)|`:

```text
transWSS = (1/N) sum |tau(t_i) - (tau(t_i) . e) e|
```

`WSSG` is the magnitude of the spatial gradient of TAWSS on the wall, computed
by finite differences over the wall-point coordinates.

For `RRT`, the reported value is the median over points with
`TAWSS >= 0.1 Pa` and `OSI <= 0.49`. For `ECAP`, the median is over
`TAWSS >= 0.1 Pa`. These masks avoid dividing by nearly zero and avoid the
singularity at `OSI = 0.5`. `LSA` uses the conventional 0.5 Pa threshold.

Per-zone values use axial windows. The defaults are:

| Zone | Window |
|---|---|
| Zone I | 0.20-0.30 m |
| Zone III | 0.03-0.10 m |

They are overridden by `zones[*].z_min_m` / `z_max_m`. A zone window is a
geometric slice, not a segmentation of the vessel wall; report the window with
the result.

## 6. Flux and cycle gates

For each exported state:

```text
flux_error = |sum(Q_out) - Q_in| / Q_in
```

The acceptance threshold is 1 %. For the transient, the mean outlet flow of
cycle 2 is compared with cycle 1; the difference in percentage points must be
below 5. A case that fails either gate is not eligible for comparison.

## 7. What is not claimed

- The mesh is not adaptively refined; a mesh-independence study must be run for
  a new anatomy.
- The WSS is a wall-shear estimate from a laminar Newtonian solve; it is not a
  measurement.
- The balloon is a rigid no-slip shell. Movement studies are a separate
  quasi-steady exposure calculation, not a moving-mesh WSS simulation.
- The pipeline does not segment CT/MRI or select clinical parameters.

## 8. Reproducibility

Every stage writes a log under `<patient>/logs/` and a machine-readable artifact
under `<patient>/results/` or `<patient>/validation/`. The mesh files, the UDF
copy used for the case, the solver logs and the 25 frames are retained, so a
reported number can be traced back to the mesh and the flow field that produced
it.
