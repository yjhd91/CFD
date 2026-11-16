"""prepare_case.py -- 通用病例几何准备（REBOA CFD 流水线 v1）。

输入：水密管腔 STL + 病例配置（开口中心/法向/分组、球囊区 z、环缝档位、完全阻断开关）。
输出：<workdir>/<case>/ 下：延伸几何 STL、openings_base.json、各 tag 流体域 STL/_sc.stp、case_manifest.json。
"""
import argparse
import json
import os
import struct
import subprocess
from collections import defaultdict

import numpy as np

WS = os.environ.get("CFD_WORKSPACE", os.getcwd())
GEOM = os.environ.get("CFD_GEOM", os.path.join(WS, "meshes"))
SC = os.environ.get("CFD_SPACECLAIM", r"D:\ANSYS2024R2\ANSYS Inc\v242\scdm\SpaceClaim.exe")


def load_tri(path):
    d = open(path, "rb").read()
    if d[:5].lower() == b"solid":
        toks = d.decode("ascii", errors="ignore").split()
        verts = []
        i = 0
        while i < len(toks):
            if toks[i] == "vertex":
                verts.append((float(toks[i + 1]), float(toks[i + 2]), float(toks[i + 3])))
                i += 4
            else:
                i += 1
        return np.asarray(verts, dtype=np.float64).reshape(-1, 3, 3)
    n = int(np.frombuffer(d[80:84], dtype=np.uint32)[0])
    rec = np.frombuffer(d[84:84 + n * 50], dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]))
    return rec["v"].reshape(-1, 3, 3).astype(np.float64)


def write_tri(path, tri):
    tri = np.asarray(tri, dtype=np.float64)
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tri)))
        for t in tri:
            nrm = np.cross(t[1] - t[0], t[2] - t[0])
            ln = np.linalg.norm(nrm)
            nrm = nrm / ln if ln > 0 else np.array([0.0, 0.0, 1.0])
            f.write(struct.pack("<3f", *nrm.astype(np.float32)))
            for v in t:
                f.write(struct.pack("<3f", *v.astype(np.float32)))
            f.write(struct.pack("<H", 0))


def cap_faces(tri, center, normal, radius):
    cen = tri.mean(axis=1)
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ln = np.linalg.norm(fn, axis=1)
    fn = fn / np.maximum(ln, 1e-15)[:, None]
    d = np.linalg.norm(cen - np.asarray(center, float)[None, :], axis=1)
    cosd = np.abs((fn * np.asarray(normal, float)[None, :]).sum(axis=1))
    idx = np.where((d < float(radius) * 1.35) & (cosd > 0.85))[0]
    if len(idx) == 0:
        return idx
    edge_faces = defaultdict(list)
    for i in idx:
        t = tri[i]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            key = tuple(sorted((tuple(np.round(t[a], 7)), tuple(np.round(t[b], 7)))))
            edge_faces[key].append(int(i))
    keep = set(int(i) for i in idx)
    seen, best = set(), []
    for s in idx:
        if int(s) in seen:
            continue
        stack = [int(s)]; seen.add(int(s)); comp = []
        while stack:
            i = stack.pop(); comp.append(i)
            t = tri[i]
            for a, b in ((0, 1), (1, 2), (2, 0)):
                key = tuple(sorted((tuple(np.round(t[a], 7)), tuple(np.round(t[b], 7)))))
                for j in edge_faces[key]:
                    if j not in seen and j in keep:
                        seen.add(j); stack.append(j)
        if len(comp) > len(best):
            best = comp
    return np.asarray(best, dtype=int)


def extend(tri, openings, inlet_len, outlet_len):
    elim, caps = set(), []
    for o in openings:
        cf = cap_faces(tri, o["center"], o["normal"], o["radius"])
        caps.append(cf)
        elim.update(cf.tolist())
    out = [tri[i] for i in range(len(tri)) if i not in elim]
    for k, (o, cf) in enumerate(zip(openings, caps)):
        if len(cf) < 4:
            raise RuntimeError("cap faces not found for opening %d" % k)
        faces = tri[cf]
        em = defaultdict(int)
        for f in faces:
            for a, b in ((0, 1), (1, 2), (2, 0)):
                key = tuple(sorted((tuple(np.round(f[a], 7)), tuple(np.round(f[b], 7)))))
                em[key] += 1
        boundary = [key for key, c in em.items() if c == 1]
        uniq = np.unique(np.asarray([list(p) for key in boundary for p in key], float), axis=0)
        c = np.asarray(o["center"], float)
        nn = np.asarray(o["normal"], float)
        u = np.cross(nn, [0.0, 0.0, 1.0])
        if np.linalg.norm(u) < 0.1:
            u = np.cross(nn, [0.0, 1.0, 0.0])
        u = u / np.linalg.norm(u)
        w = np.cross(nn, u)
        ang = np.arctan2((uniq - c) @ w, (uniq - c) @ u)
        loop = uniq[np.argsort(ang)]
        L = inlet_len if k == 0 else outlet_len
        far = loop + nn[None, :] * L
        for i in range(len(loop)):
            j = (i + 1) % len(loop)
            out.append(np.array([loop[i], loop[j], far[i]]))
            out.append(np.array([loop[j], far[j], far[i]]))
        fc = far.mean(axis=0)
        for i in range(len(loop)):
            j = (i + 1) % len(loop)
            out.append(np.array([far[i], far[j], fc]))
        o["far_center"] = fc.tolist()
        o["loop_n"] = int(len(loop))
    return np.asarray(out, dtype=np.float64)


def mesh_indexed(tri):
    tri = np.asarray(tri, dtype=np.float64)
    flat = np.round(tri.reshape(-1, 3), 7)
    V, inv = np.unique(flat, axis=0, return_inverse=True)
    F = inv.reshape(-1, 3)
    return V, F


def slice_ring(tri, zc, nbins=36, cxy=None, rmax=None):
    pts = []
    for t in tri:
        z = t[:, 2]
        if z.min() > zc or z.max() < zc:
            continue
        for i in range(3):
            a, b = t[i], t[(i + 1) % 3]
            if (a[2] - zc) * (b[2] - zc) < 0:
                s = (zc - a[2]) / (b[2] - a[2])
                pts.append(a + s * (b - a))
    if len(pts) < 12:
        return None
    pts = np.asarray(pts)
    if cxy is not None and rmax is not None:
        d = np.linalg.norm(pts[:, :2] - np.asarray(cxy, float)[None, :], axis=1)
        pts = pts[d < float(rmax)]
        if len(pts) < 12:
            return None
    c = pts[:, :2].mean(0)
    for _ in range(3):
        rr = np.linalg.norm(pts[:, :2] - c, axis=1)
        med = np.median(rr)
        keep = rr < med * 1.4
        if keep.sum() < 12:
            break
        pts = pts[keep]
        c = pts[:, :2].mean(0)
    rel = pts[:, :2] - c
    ang = np.arctan2(rel[:, 1], rel[:, 0])
    r = np.linalg.norm(rel, axis=1)
    bins = np.floor((ang + np.pi) / (2 * np.pi) * nbins).astype(int) % nbins
    rad = np.full(nbins, np.nan)
    for b in range(nbins):
        rr = r[bins == b]
        if len(rr):
            rad[b] = rr.max()
    good = np.isfinite(rad)
    if good.sum() < nbins // 3:
        return None
    # 抑制分支方向的半径尖峰（主动脉主环半径 = 分箱中位数的 1.25 倍封顶）
    radcap = float(np.median(rad[good])) * 1.25
    rad = np.minimum(rad, radcap)
    x = np.arange(nbins)
    rad = np.interp(x, x[good], rad[good], period=nbins)
    th = -np.pi + (x + 0.5) * 2 * np.pi / nbins
    return np.column_stack([c[0] + np.cos(th) * rad, c[1] + np.sin(th) * rad])


def balloon_shell(tri, zc, gap_m, length_m, end_m, cxy=None, rmax=None):
    half = length_m * 0.5
    zs = np.arange(zc - half, zc + half + 1e-9, 0.001)
    rings, C, zused = [], [], []
    for z in zs:
        ring = slice_ring(tri, float(z), cxy=cxy, rmax=rmax)
        if ring is None:
            continue
        rings.append(ring); C.append(ring.mean(0)); zused.append(float(z))
    if len(rings) < 8:
        raise RuntimeError("too few slices at z=%.4f" % zc)
    C = np.asarray(C)
    Lc = max(0.0, half - end_m)
    rings2 = []
    for ring, cz, z in zip(rings, C, zused):
        t = abs(z - zc)
        f = 1.0 if t <= Lc else float(np.sqrt(max(0.0, 1 - ((t - Lc) / max(1e-9, (half - Lc))) ** 2)))
        if f < 0.05:
            continue
        outer = np.linalg.norm(ring - cz, axis=1)
        newr = np.maximum(0.0, (outer - gap_m)) * f
        nring = cz + (ring - cz) / np.maximum(outer, 1e-9)[:, None] * newr[:, None]
        rings2.append(np.column_stack([nring, np.full(len(ring), z)]))
    V, F, idx = [], [], []
    n = len(rings2[0])
    for ring in rings2:
        idx.append(len(V)); V.extend(ring.tolist())
    for k in range(len(rings2) - 1):
        i0, i1 = idx[k], idx[k + 1]
        for i in range(n):
            j = (i + 1) % n
            F.append([i0 + i, i0 + j, i1 + i]); F.append([i0 + j, i1 + j, i1 + i])
    for k, direction in ((0, -1), (len(rings2) - 1, 1)):
        base_ring = np.asarray(V[idx[k]:idx[k] + n])
        center = base_ring.mean(0) + np.array([0, 0, direction * 1e-4])
        ci = len(V); V.append(center.tolist()); base = idx[k]
        for i in range(n):
            j = (i + 1) % n
            F.append([base + i, ci, base + j] if direction < 0 else [base + i, base + j, ci])
    ball = np.asarray(V, float)
    ballF = np.asarray(F, np.int64)
    tri_ball = ball[ballF]; cen = ball.mean(0)
    normals = np.cross(tri_ball[:, 1] - tri_ball[:, 0], tri_ball[:, 2] - tri_ball[:, 0])
    dirs = tri_ball.mean(1) - cen
    if (np.einsum("ij,ij->i", normals, dirs) < 0).mean() > 0.5:
        ballF = ballF[:, ::-1]
    return ball, ballF


def _to_manifold(V, F):
    from manifold3d import Manifold, Mesh
    V = np.asarray(V, np.float32)
    F = np.asarray(F, np.uint32)
    m = Manifold(Mesh(vert_properties=V, tri_verts=F))
    if m.volume() < 0:
        m = Manifold(Mesh(vert_properties=V, tri_verts=F[:, ::-1].copy()))
    return m


def manifold_boolean(aV, aF, bV, bF):
    A = _to_manifold(aV, aF)
    B = _to_manifold(bV, bF)
    m = (A - B).to_mesh()
    return np.asarray(m.vert_properties)[:, :3].astype(np.float64), np.asarray(m.tri_verts, np.int64)


def stem_cylinder(p0, p1, r):
    p0 = np.asarray(p0, float); p1 = np.asarray(p1, float)
    d = p1 - p0
    L = np.linalg.norm(d)
    if L < 1e-9:
        raise RuntimeError("zero-length stem")
    e1 = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(d / L, e1)) > 0.9:
        e1 = np.array([1.0, 0.0, 0.0])
    u = np.cross(d / L, e1); u = u / np.linalg.norm(u)
    v = np.cross(d / L, u)
    n = 16
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    V = []
    for p in (p0, p1):
        for t in th:
            V.append(p + r * np.cos(t) * u + r * np.sin(t) * v)
    F = []
    for i in range(n):
        j = (i + 1) % n
        F.append([i, j, n + i]); F.append([j, n + j, n + i])
    # 两端封口（扇形中心点）
    c0 = len(V)
    V.append(p0.tolist())
    c1 = len(V)
    V.append(p1.tolist())
    for i in range(n):
        j = (i + 1) % n
        F.append([c0, j, i])
        F.append([c1, n + i, n + j])
    return np.asarray(V), np.asarray(F)


def stem_for_zone(tri_ext, zc, gap_m, cfg_s):
    ring = slice_ring(tri_ext, zc)
    if ring is None:
        raise RuntimeError("no ring at stem z")
    c = ring.mean(axis=0)
    rr = np.linalg.norm(ring - c, axis=1)
    r_wall = float(np.percentile(rr, 95))
    ang = np.deg2rad(cfg_s.get("angle_deg", 90.0))
    dirv = np.array([np.cos(ang), np.sin(ang)])
    r_tube = cfg_s.get("r_m", 0.0008)
    p0 = np.array([c[0] + dirv[0] * (r_wall - gap_m - 0.0015),
                   c[1] + dirv[1] * (r_wall - gap_m - 0.0015), zc])
    p1 = np.array([c[0] + dirv[0] * (r_wall + 0.004), c[1] + dirv[1] * (r_wall + 0.004), zc])
    return stem_cylinder(p0, p1, r_tube)


def select_component(V, F, point):
    parent = np.arange(len(V))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for f in F:
        union(int(f[0]), int(f[1])); union(int(f[1]), int(f[2]))
    target = int(np.argmin(np.linalg.norm(V - point, axis=1)))
    root = find(target)
    keepF = np.array([f for f in F if find(int(f[0])) == root])
    used = np.unique(keepF.ravel())
    remap = {int(o): i for i, o in enumerate(used)}
    return V[used], np.array([[remap[int(a)], remap[int(b)], remap[int(c)]] for a, b, c in keepF])


def convert_steps(pairs, log_path):
    lines = ["# -*- coding: utf-8 -*-", "LOG = r'%s'" % log_path,
             "def log(m):", "    f=open(LOG,'a'); f.write(str(m)+'\\n'); f.close()",
             "log('=== start ===')", "try:", "    from SpaceClaim.Api.V242 import *",
             "    from SpaceClaim.Api.V242.Geometry import *", "    log('api ok')"]
    for stl, stp in pairs:
        lines += ["    try:", "        opts = ImportOptions.Create()",
                  "        opts.Stl.ImportType = StlImportType.SolidOrSurface",
                  "        doc = Document.Open(r'%s', opts)" % stl,
                  "        part = doc.MainPart", "        eo = ExportOptions.Create()",
                  "        part.Export(PartExportFormat.Step, r'%s', True, eo)" % stp,
                  "        log('OK %s')" % os.path.basename(stp),
                  "    except Exception as ex:", "        log('ERR %s: '+str(ex))" % os.path.basename(stl)]
    lines += ["except Exception as ex:", "    log('FATAL: '+str(ex))", "log('=== done ===')", "Application.Exit()"]
    sc = os.path.join(GEOM, "_asset_convert.py")
    open(sc, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    if os.path.exists(log_path):
        os.remove(log_path)
    subprocess.run([SC, "/RunScript=%s" % sc, "/ExitAfterScript=True"], timeout=7200)
    txt = open(log_path).read() if os.path.exists(log_path) else ""
    return "FATAL" not in txt and all(("OK %s" % os.path.basename(p[1])) in txt for p in pairs), txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", default=os.path.join(GEOM, "asset_cases"))
    args = ap.parse_args()
    cfg = json.load(open(args.config, encoding="utf-8"))
    case = cfg["case_id"]
    wd = os.path.join(args.workdir, case)
    os.makedirs(wd, exist_ok=True)
    os.makedirs(GEOM, exist_ok=True)
    openings = cfg.get("openings", [])
    if cfg.get("prepared_ext_stl"):
        tri_ext = load_tri(cfg["prepared_ext_stl"])
        ext_stl = cfg["prepared_ext_stl"]
        ext_json = cfg.get("prepared_ext_json")
        base_json = cfg.get("prepared_openings_base")
        print("prepared ext:", ext_stl, "tris:", len(tri_ext), flush=True)
    else:
        tri = load_tri(cfg["lumen_stl"])
        print("input tris:", len(tri), "openings:", len(openings), flush=True)
        tri_ext = extend(tri, openings, cfg.get("inlet_extension_mm", 40) * 1e-3,
                         cfg.get("outlet_extension_mm", 10) * 1e-3)
        ext_stl = os.path.join(wd, case + "_ext.stl")
        write_tri(ext_stl, tri_ext)
    if not cfg.get("prepared_ext_stl"):
        ext_json = os.path.join(wd, case + "_ext.json")
        json.dump(dict(openings=[dict(index=k, center=o["center"], outward=o["normal"],
                                  L=(cfg.get("inlet_extension_mm", 40) if k == 0 else cfg.get("outlet_extension_mm", 10)) * 1e-3,
                                  far_center=o.get("far_center"), R=float(o["radius"]), loop_n=o.get("loop_n"))
                             for k, o in enumerate(openings)]),
                  open(ext_json, "w"), indent=1)
        base = []
        for k, o in enumerate(openings):
            if k == 0:
                zone = "inlet"
            else:
                g = o.get("group", "iliac")
                zone = {"arch": "outlet1-geom-2-flu1", "renal": "outlet1-geom-2-flu2",
                        "iliac": "outlet1-geom-2-flu3"}.get(g, "outlet1-geom-2-flu3")
            base.append(dict(zone=zone, centroid=o.get("far_center") or o["center"]))
        base_json = os.path.join(wd, "openings_base.json")
        json.dump(base, open(base_json, "w"), indent=1)
    manifest = dict(case_id=case, lumen_stl=cfg.get("lumen_stl", cfg.get("prepared_ext_stl")), ext_stl=ext_stl, ext_json=ext_json,
                    openings_base=base_json, tags={}, mesh=cfg["mesh"], zones=cfg.get("zones", {}),
                    cycles=cfg.get("cycles", 2), time_step=cfg.get("time_step", 0.002))
    pairs = []
    for zone, zc in cfg.get("zones", {}).items():
        if not zc.get("enabled", True):
            continue
        for gap in zc.get("gaps_mm", []):
            tag = "%s_%s_g%s" % (case, zone.lower(), str(gap).replace(".", ""))
            ballV, ballF = balloon_shell(tri_ext, zc["z_center"], gap * 1e-3,
                                         zc.get("length_mm", 40) * 1e-3, zc.get("end_mm", 6) * 1e-3,
                                         cxy=zc.get("center_xy"), rmax=zc.get("r_max_m"))
            baseV, baseF = mesh_indexed(tri_ext)
            fluidV, fluidF = manifold_boolean(baseV, baseF, ballV, ballF)
            if zc.get("stem", {}).get("enabled", True):
                stemV, stemF = stem_for_zone(tri_ext, zc["z_center"], gap * 1e-3, zc.get("stem", {}))
                fluidV, fluidF = manifold_boolean(fluidV, fluidF, stemV, stemF)
            stl = os.path.join(wd, tag + ".stl")
            write_tri(stl, fluidV[fluidF])
            stp = os.path.join(wd, tag + "_sc.stp")
            pairs.append((stl, stp))
            manifest["tags"][tag] = dict(kind="partial", zone=zone, gap_mm=gap, stl=stl, step=stp)
        if zc.get("complete", False):
            tag = "%s_%s_complete" % (case, zone.lower())
            z0 = zc["z_center"]; t = zc.get("slab_mm", 4.0) * 1e-3
            boxV = np.array([[-1, -1, z0 - t / 2], [1, -1, z0 - t / 2], [1, 1, z0 - t / 2], [-1, 1, z0 - t / 2],
                             [-1, -1, z0 + t / 2], [1, -1, z0 + t / 2], [1, 1, z0 + t / 2], [-1, 1, z0 + t / 2]])
            boxF = np.array([[0, 1, 2], [0, 2, 3], [4, 6, 5], [4, 7, 6], [0, 4, 5], [0, 5, 1],
                             [1, 5, 6], [1, 6, 2], [2, 6, 7], [2, 7, 3], [3, 7, 4], [3, 4, 0]])
            baseV, baseF = mesh_indexed(tri_ext)
            cutV, cutF = manifold_boolean(baseV, baseF, boxV, boxF)
            if openings:
                inlet = np.asarray(openings[0]["center"], float) - np.asarray(openings[0]["normal"], float) * 0.02
            else:
                ext_ops = json.load(open(ext_json, encoding="utf-8"))["openings"]
                inlet = np.asarray(ext_ops[0]["center"], float) - np.asarray(ext_ops[0]["outward"], float) * 0.02
            proxV, proxF = select_component(cutV, cutF, inlet)
            stl = os.path.join(wd, tag + ".stl")
            write_tri(stl, proxV[proxF])
            stp = os.path.join(wd, tag + "_sc.stp")
            pairs.append((stl, stp))
            manifest["tags"][tag] = dict(kind="complete", zone=zone, stl=stl, step=stp)
    ok, logtxt = convert_steps(pairs, os.path.join(wd, "spaceclaim.log"))
    manifest["step_ok"] = ok
    centers = {}
    for zone, zc in cfg.get("zones", {}).items():
        cxy = zc.get("center_xy") or [0.0, 0.0]
        centers[zone] = {"x": float(cxy[0]), "y": float(cxy[1]), "z": float(zc["z_center"])}
    json.dump(centers, open(os.path.join(wd, "complete_zone_centers.json"), "w"), indent=1)
    json.dump(manifest, open(os.path.join(wd, "case_manifest.json"), "w"), indent=1, ensure_ascii=False)
    print("done:", os.path.join(wd, "case_manifest.json"), "step_ok:", ok, "tags:", len(manifest["tags"]), flush=True)


if __name__ == "__main__":
    main()
