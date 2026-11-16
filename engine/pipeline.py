"""pipeline_extall2.py -- 13 开口几何（入口+出口延伸）流水线：不合并分区，直接用多分区 WK UDF。

用法: python pipeline_extall2.py <tag> <step_sc.stp> [min] [max]
输出: tools/geom/mesh_<tag>*.h5, tools/wkcase/st_<tag>*, wk_<tag>_out/syst, wss_<tag>_wall.txt, libudf_<tag>/
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

import numpy as np

WS = os.environ.get("CFD_WORKSPACE", os.getcwd())
ENGINE = os.path.dirname(os.path.abspath(__file__))
GEOM = os.environ.get("CFD_GEOM", os.path.join(WS, "meshes"))
WK = os.environ.get("CFD_WK", os.path.join(WS, "work", "wkcase"))
LOGS = os.environ.get("CFD_LOGS", os.path.join(WS, "logs"))
ANSYS_PY = os.environ.get("CFD_ANSYS_PYTHON", sys.executable)
PY = os.environ.get("CFD_PYTHON", sys.executable)
FB = os.environ.get("CFD_FLUENT_BATCH", os.path.join(ENGINE, "fluent_batch.ps1"))
LOG = None
CORES = 4
CYCLES = 2
COMPLETE = 0
BL_LAYERS = 6
BL_FIRST = 0.00005
BL_RATE = 1.2


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def sh(cmd, env=None, timeout=21600):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(cmd, env=e, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or "")[-3000:], (r.stderr or "")[-3000:]


def fluent_journal(name, lines, cores=None, timeout=21600):
    cores = CORES if cores is None else cores
    jp = os.path.join(WK, name + ".jou")
    lp = os.path.join(WK, name + ".log")
    open(jp, "w", encoding="ascii").write("\n".join(lines) + "\n")
    rc, so, se = sh(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", FB,
                     "-Journal", jp, "-Cores", str(cores), "-WorkDir", WK, "-LogPath", lp,
                     "-TimeoutSec", str(timeout)])
    log("%s rc=%d" % (name, rc))


def make_plane_files(tag, ext_json):
    d = json.load(open(ext_json))
    env_base = os.environ.get("REBOA_OPENINGS_BASE")
    base = json.load(open(env_base)) if env_base else json.load(open(os.path.join(GEOM, "openings.json")))
    planes = []
    ops = json.loads(json.dumps(base))
    for it in d["openings"]:
        k = it["index"]
        out = np.array(it["outward"])
        planes.append(dict(zone=base[k]["zone"], centroid=it["far_center"],
                           normal=(out / np.linalg.norm(out)).tolist(), radius=float(it["R"]),
                           L=float(it["L"])))
        ops[k]["centroid"] = it["far_center"]
    pj = os.path.join(GEOM, "planes_%s.json" % tag)
    oj = os.path.join(GEOM, "openings_%s.json" % tag)
    json.dump(planes, open(pj, "w"), indent=1)
    json.dump(ops, open(oj, "w"), indent=1)
    return pj, oj


def valid_mesh(path):
    try:
        import h5py
        f = h5py.File(path, "r")
        n = int(f["meshes/1/cells/zoneTopology/maxId"][0])
        f.close()
        return n > 1000
    except Exception:
        return False


def step_mesh(tag, step, mn, mx):
    mesh = os.path.join(GEOM, "mesh_%s.msh.h5" % tag)
    bc = os.path.join(GEOM, "mesh_%s_bc.msh.h5" % tag)
    ext_json = os.environ.get("REBOA_EXT_JSON") or step.replace("_sc.stp", ".json")
    if not os.path.exists(ext_json):
        ext_json = os.path.join(GEOM, "lumen_extall.json")
        log("ext json fallback -> %s" % ext_json)
    if os.path.exists(mesh) and not valid_mesh(mesh):
        log("invalid existing mesh -> remesh")
        for p in (mesh, bc, os.path.join(GEOM, "mesh_%s_zones.json" % tag)):
            if os.path.exists(p):
                os.remove(p)
        for p in (os.path.join(GEOM, "mesh_%s_sep1.msh.h5" % tag),
                  os.path.join(GEOM, "mesh_%s_sep2.msh.h5" % tag)):
            if os.path.exists(p):
                os.remove(p)
    if not (os.path.exists(mesh) and os.path.getsize(mesh) > 1e6):
        log("meshing %s min=%s max=%s" % (tag, mn, mx))
        rc, so, se = sh([ANSYS_PY, os.path.join(ENGINE, "wt_mesh_ext.py"), step, mesh, mn, mx,
                         str(BL_LAYERS), "%.6f" % BL_FIRST, str(BL_RATE)])
        mlog = os.path.join(ENGINE, "meshing_%s.log" % tag)
        with open(mlog, "w", encoding="utf-8", errors="replace") as sf:
            sf.write(so or "")
            sf.write(chr(10) + "=== STDERR ===" + chr(10))
            sf.write(se or "")
        log("meshing rc=%d log=%s" % (rc, mlog))
    if os.path.exists(bc) and not valid_mesh(bc):
        log("invalid existing bc mesh -> reassign")
        for p in (bc, os.path.join(GEOM, "mesh_%s_zones.json" % tag)):
            if os.path.exists(p):
                os.remove(p)
    if not (os.path.exists(bc) and os.path.getsize(bc) > 1e6):
        pj, oj = make_plane_files(tag, ext_json)
        log("plane-based cap assignment")
        rc, so, se = sh([PY, os.path.join(ENGINE, "assign_caps_planes.py"),
                         mesh, pj, oj, "mesh_%s" % tag])
        log("assign rc=%d" % rc)
        if not os.path.exists(bc):
            raise SystemExit("assignment failed: " + se[-500:])
    return bc


def parse_groups(bc):
    import h5py
    f = h5py.File(bc, "r")
    g = f["meshes/1/faces/zoneTopology"]
    ids = g["id"][:]
    types = g["zoneType"][:]
    names = g["name"][0].decode("latin-1").split(";")
    f.close()
    groups = {"outlet_arch": [], "outlet_renal": [], "outlet_iliac": []}
    names_g = {"outlet_arch": [], "outlet_renal": [], "outlet_iliac": []}
    inlet = None
    walls = []
    for zid, t, nm in zip(ids, types, names):
        t = int(t)
        if t == 10:
            inlet = int(zid)
        elif t == 5:
            for base in groups:
                if nm == base or nm.startswith(base + "_part"):
                    groups[base].append(int(zid))
                    names_g[base].append(nm)
        elif t == 3:
            walls.append(nm)
    log("groups: %s" % {k: (len(v), names_g[k]) for k, v in groups.items()})
    log("walls: %s" % walls)
    return groups, names_g, inlet, walls


def gen_udf(tag, groups, inlet):
    lib = "libudf_" + tag
    base = open(os.path.join(WK, "wk_aorta_ext4.c"), encoding="utf-8", errors="ignore").read()
    for key, fn in (("outlet_arch", "wk_arch"), ("outlet_renal", "wk_renal"), ("outlet_iliac", "wk_iliac")):
        ids = groups[key]
        ids_str = ", ".join(str(i) for i in ids) if ids else "0"
        line = "static WK %s = {%%s, %d, {%s}};" % (fn, len(ids), ids_str)
        if key == "outlet_arch":
            line = line % "2.42e7, 4.61e8, 2.48e-9, 7.61e3, 0.0"
        elif key == "outlet_renal":
            line = line % "2.76e7, 5.24e8, 2.18e-9, 7.60e3, 0.0"
        else:
            line = line % "2.11e7, 4.00e8, 2.85e-9, 7.60e3, 0.0"
        pat = r"static WK %s\s*=\s*\{.*?\}\};" % fn
        base2 = re.sub(pat, line, base, count=1, flags=re.S)
        if base2 == base:
            raise SystemExit("udf template substitution failed for " + fn)
        base = base2
    comp_tags = {x for x in os.environ.get("CFD_COMP_TAGS", "").split(",") if x}
    if tag in comp_tags or tag.startswith("comp_"):
        comp_map = dict(x.split(":", 1) for x in os.environ.get("CFD_COMP_ZONES", "").split(",") if ":" in x)
        zone = comp_map.get(tag) or ("I" if tag.startswith("comp_i") else "III")
        base = base.replace("#define ENABLE_SRC 0", "#define ENABLE_SRC 1")
        base = base.replace("static const double B_AX = 0.025;",
                            "static const double B_AX = %s;" % os.environ.get("CFD_BALLOON_AXIS", "0.050"))
        base = base.replace("balloon_params.txt", "balloon_params_%s.txt" % tag)
        c = json.load(open(os.path.join(GEOM, "complete_zone_centers.json"), encoding="utf-8"))[zone]
        radius = os.environ.get("CFD_BALLOON_RADIUS", "0.025")
        open(os.path.join(WK, "balloon_params_%s.txt" % tag), "w").write(
            "%.6f %.6f %.6f 0 0 1 %s\n" % (c["x"], c["y"], c["z"], radius))
    src = "wk_aorta_%s.c" % tag
    open(os.path.join(WK, src), "w", encoding="ascii").write(base)
    shutil.rmtree(os.path.join(WK, lib), ignore_errors=True)
    fluent_journal("compile_udf_" + tag, ["/define/user-defined/compiled-functions/",
                                         "compile", lib, "y", src, '""', '""', 'exit'], cores=1, timeout=900)
    if not os.path.exists(os.path.join(WK, lib, "win64", "3ddp_host", "libudf.dll")):
        raise SystemExit("udf compile failed")
    zt = dict(groups, inlet=[inlet])
    zj = os.path.join(WK, "zones_%s.json" % tag)
    json.dump(zt, open(zj, "w"), indent=1)
    return lib, zj


def setup_lines(case, lib, names_g):
    L = ['/file/read-case-data "%s"' % case,
         '/file/auto-save/', 'data-frequency', '0', 'case-frequency', 'if-mesh-is-modified', 'q',
         '/define/user-defined/compiled-functions/', 'load', lib,
         '/define/user-defined/function-hooks/', 'adjust', 'wk_update::%s' % lib, '""', 'q', 'q',
         '/define/boundary-conditions/zone-type', 'inlet', 'velocity-inlet',
         '/define/boundary-conditions/velocity-inlet', 'inlet',
         'no', 'no', 'yes', 'yes', 'yes', 'yes', 'udf', 'inlet_velocity::%s' % lib, 'no', '0']
    for base, fn in (("outlet_arch", "wk_arch_p"), ("outlet_renal", "wk_renal_p"),
                     ("outlet_iliac", "wk_iliac_p")):
        for nm in names_g[base]:
            L += ['/define/boundary-conditions/pressure-outlet', nm,
                  'yes', 'yes', 'yes', 'udf', '%s::%s' % (fn, lib), 'no', 'yes', 'yes', 'no', 'no', 'no']
    L += ['/define/models/unsteady-1st-order', 'yes', '/solve/set/time-step',
          os.environ.get("CFD_TIME_STEP", "0.002")]
    return L


def steady(tag, bc, zj):
    st = os.path.join(WK, "st_%s" % tag)
    if os.path.exists(st + ".dat.gz"):
        log("steady exists, skip")
        return st
    log("steady init")
    env = dict(os.environ, NP=os.environ.get("CFD_CORES", "8"),
               NITER=os.environ.get("CFD_STEADY_ITERS", "400"))
    slog = os.path.join(ENGINE, "steady_%s.log" % tag)
    rc, so, se = sh([ANSYS_PY, os.path.join(ENGINE, "ext4_steady.py"), bc, zj, st], env=env, timeout=14400)
    with open(slog, "w", encoding="utf-8", errors="replace") as sf:
        sf.write(so or "")
        sf.write(chr(10) + "=== STDERR ===" + chr(10))
        sf.write(se or "")
    log("steady rc=%d log=%s" % (rc, slog))
    if not os.path.exists(st + ".dat.gz"):
        raise SystemExit("steady failed")
    return st


def transient(tag, lib, names_g):
    base = WK.replace("\\", "/")
    out = os.path.join(WK, "wk_%s_out.cas.gz" % tag)
    if os.path.exists(out) and os.path.getsize(out) > 1e6:
        log("transient exists, skip")
        return out
    # 清理同名输出（含 .dat.gz），否则 write-case-data 会卡在 Overwrite 提示
    for suffix in ("out.cas.gz", "out.dat.gz"):
        fp = os.path.join(WK, "wk_%s_%s" % (tag, suffix))
        if os.path.exists(fp):
            os.remove(fp)
    L = setup_lines(base + "/st_%s.cas.gz" % tag, lib, names_g)
    steps = int(500 * CYCLES / 2)
    L += ['/solve/dual-time-iterate', str(steps), '20',
          '/file/write-case-data "%s/wk_%s_out.cas.gz"' % (base, tag), 'exit']
    fluent_journal("wk_" + tag, L, cores=CORES, timeout=28800)
    if not os.path.exists(out):
        raise SystemExit("transient failed")
    return out


def syst_export(tag, lib, names_g, walls):
    base = WK.replace("\\", "/")
    syst = os.path.join(WK, "wk_%s_syst.cas.gz" % tag)
    if not os.path.exists(syst):
        # 只清理 syst 输出；transient 输出必须保留供读取
        for suffix in ("syst.cas.gz", "syst.dat.gz"):
            fp = os.path.join(WK, "wk_%s_%s" % (tag, suffix))
            if os.path.exists(fp):
                os.remove(fp)
        if not os.path.exists(os.path.join(WK, "wk_%s_out.cas.gz" % tag)):
            raise SystemExit("missing transient output before syst: %s" % tag)
        L = setup_lines(base + "/wk_%s_out.cas.gz" % tag, lib, names_g)
        L += ['/solve/dual-time-iterate', '76', '20',
              '/file/write-case-data "%s/wk_%s_syst.cas.gz"' % (base, tag), 'exit']
        fluent_journal("syst_" + tag, L, cores=CORES, timeout=10800)
    if not os.path.exists(os.path.join(WK, "wss_%s_wall.txt" % tag)):
        wf = os.path.join(WK, "wss_%s_wall.txt" % tag)
        if os.path.exists(wf):
            os.remove(wf)
        ws = " ".join('"%s"' % w for w in walls)
        L = ['/file/read-case-data "%s/wk_%s_syst.cas.gz"' % (base, tag),
             '/define/user-defined/compiled-functions/', 'load', lib,
             '/file/export/ascii', base + "/wss_%s_wall.txt" % tag,
             ws, '()', 'no',
             'x-wall-shear', 'y-wall-shear', 'z-wall-shear', '()', 'no', 'exit', 'yes']
        fluent_journal("export_" + tag, L, cores=CORES, timeout=3600)


def main():
    global LOG
    tag = sys.argv[1]
    lock = os.path.join(LOGS, 'pipeline_%s.lock' % tag)
    if os.path.exists(lock):
        print('LOCK exists, another run active:', lock)
        raise SystemExit(0)
    open(lock, 'w').write(str(os.getpid()))
    try:
        _main(tag)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _main(tag):
    global LOG, CORES, CYCLES, BL_LAYERS, BL_FIRST, BL_RATE, COMPLETE
    step = sys.argv[2]
    mn = sys.argv[3] if len(sys.argv) > 3 else "0.0006"
    mx = sys.argv[4] if len(sys.argv) > 4 else "0.002"
    if len(sys.argv) > 5:
        CORES = int(sys.argv[5])
    if len(sys.argv) > 6:
        BL_LAYERS = int(sys.argv[6])
    if len(sys.argv) > 7:
        BL_FIRST = float(sys.argv[7])
    if len(sys.argv) > 8:
        BL_RATE = float(sys.argv[8])
    if len(sys.argv) > 9:
        CYCLES = int(sys.argv[9])
    COMPLETE = 1 if tag.startswith("comp_") else 0
    LOG = os.path.join(ENGINE, "pipeline_%s.log" % tag)
    open(LOG, "w", encoding="utf-8").write("")
    log("cores=%d cycles=%d BL=%d x %.5f rate %.2f" % (CORES, CYCLES, BL_LAYERS, BL_FIRST, BL_RATE))
    log("pipeline %s start" % tag)
    bc = step_mesh(tag, step, mn, mx)
    groups, names_g, inlet, walls = parse_groups(bc)
    lib, zj = gen_udf(tag, groups, inlet)
    steady(tag, bc, zj)
    transient(tag, lib, names_g)
    syst_export(tag, lib, names_g, walls)
    log("PIPELINE %s DONE" % tag)


if __name__ == "__main__":
    main()
