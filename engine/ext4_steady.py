"""ext4_steady.py -- steady initial field on extended (inlet extension) mesh.

Usage: python ext4_steady.py <bc_mesh.h5> <zones_json> <out_stem>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pyfluent_shim  # noqa: F401
import ansys.fluent.core as pf

mesh, zones_json, stem = sys.argv[1], sys.argv[2], sys.argv[3]
zon = json.load(open(zones_json))


def names_of(group):
    ids = zon[group]
    return [group] + ["%s_part%d" % (group, i + 1) for i in range(1, len(ids))]


s = pf.launch_fluent(mode="solver", precision="double",
                     processor_count=int(os.environ.get("NP", "8")),
                     ui_mode="no_gui", start_timeout=600)
print("launched", flush=True)
cmd = '/file/read-case "%s"\n' % mesh.replace("\\", "/")
s.scheme_eval.scheme_eval("(ti-menu-load-string %s)" % json.dumps(cmd))
print("[OK] mesh read", flush=True)
s.setup.models.viscous.model = "laminar"
mat = s.setup.materials.fluid["air"]
mat.density.value = float(os.environ.get("CFD_DENSITY", "1050.0"))
mat.viscosity.value = float(os.environ.get("CFD_VISCOSITY", "0.0035"))
bc = s.setup.boundary_conditions
bc.velocity_inlet["inlet"].momentum.velocity.value = float(os.environ.get("CFD_INLET_VELOCITY", "0.0487"))
out_names = []
for g in ("outlet_arch", "outlet_renal", "outlet_iliac"):
    for nm in names_of(g):
        out_names.append(nm)
        try:
            bc.pressure_outlet[nm].momentum.gauge_pressure.value = float(os.environ.get("CFD_OUTLET_PRESSURE", "8000.0"))
        except Exception as ex:
            print("skip", nm, str(ex)[:120], flush=True)
print("[OK] outlets:", out_names, flush=True)
s.solution.initialization.hybrid_initialize()
s.solution.run_calculation.iterate(iter_count=int(os.environ.get("NITER", "400")))
print("[OK] iterations", flush=True)
s.scheme_eval.scheme_eval("(ti-menu-load-string %s)" % json.dumps(
    '/file/write-case-data "%s.cas.gz"\n' % stem.replace("\\", "/")))
print("[OK] wrote", stem, flush=True)
try:
    s.scheme_eval.scheme_eval("(ti-menu-load-string %s)" % json.dumps(
        "(report/fluxes/mass-flow () inlet outlet_arch outlet_renal outlet_iliac ())"))
except Exception as ex:
    print("report err", str(ex)[:200], flush=True)
s.exit()
