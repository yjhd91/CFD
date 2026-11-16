"""wt_mesh_ext.py -- mesh extended geometry (STEP) with boundary layers (WT workflow)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pyfluent_shim  # noqa
import ansys.fluent.core as pf

step, out = sys.argv[1], sys.argv[2]
mn = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0015
mx = float(sys.argv[4]) if len(sys.argv) > 4 else 0.006
bl_layers = int(sys.argv[5]) if len(sys.argv) > 5 else 8
bl_first = float(sys.argv[6]) if len(sys.argv) > 6 else 0.00012
bl_rate = float(sys.argv[7]) if len(sys.argv) > 7 else 1.2
m = pf.launch_fluent(mode="meshing", precision="double",
                     processor_count=int(os.environ.get("CFD_CORES", "4")),
                     ui_mode="no_gui", start_timeout=600)
print("launched", m.get_fluent_version(), flush=True)
m.workflow.InitializeWorkflow(WorkflowType="Watertight Geometry")
t = m.workflow.TaskObject["Import Geometry"]
t.Arguments.update_dict({"FileName": step, "LengthUnit": "m"})
t.Execute()
print("[OK] import", flush=True)
for nm, args in [
    ("Add Local Sizing", {"AddChild": "no"}),
    ("Generate the Surface Mesh", {"CFDSurfaceMeshControls": {"MinSize": mn, "MaxSize": mx,
                                                              "GrowthRate": 1.2}}),
    ("Describe Geometry", {"SetupType": "The geometry consists of only fluid regions with no voids",
                           "CappingRequired": "No", "WallToInternal": "No",
                           "InvokeShareTopology": "Yes"}),
]:
    t0 = time.time()
    t = m.workflow.TaskObject[nm]
    if args:
        t.Arguments.update_dict(args)
    t.Execute()
    print("[OK] %s (%.0fs)" % (nm, time.time() - t0), flush=True)
# boundary layers
try:
    t0 = time.time()
    t = m.workflow.TaskObject["Add Boundary Layers"]
    t.Arguments.update_dict({"NumberOfLayers": bl_layers, "FirstHeight": bl_first, "Rate": bl_rate,
                             "MaxLayerHeight": 0.003, "TransitionRatio": 0.3})
    t.Execute()
    print("[OK] BL (%.0fs)" % (time.time() - t0), flush=True)
except Exception as e:
    print("[WARN] BL failed:", str(e)[-400:], flush=True)
t0 = time.time()
t = m.workflow.TaskObject["Generate the Volume Mesh"]
t.Arguments.update_dict({"Solver": "Fluent", "VolumeFill": "poly-hexcore",
                         "MeshFluidRegions": True, "MeshSolidRegions": False,
                         "SizingMethod": "Global"})
t.Execute()
print("[OK] volume (%.0fs)" % (time.time() - t0), flush=True)
m.meshing.File.WriteMesh(FileName=out)
print("[OK] write", out, flush=True)
m.exit()
