"""BL WSS-derived metrics from 25 HEMO frames (TAWSS/OSI/RRT/LSA/ECAP/transWSS/WSSG)."""
import glob
import json
import os

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

WS = os.environ.get("CFD_WORKSPACE", os.getcwd())
HEMO = os.environ.get("CFD_HEMO", os.path.join(WS, "work", "hemo"))
OUT = os.environ.get("CFD_RESULTS", os.path.join(WS, "results"))
STA = os.environ.get("CFD_STATS", os.path.join(OUT, "stats"))
os.makedirs(OUT, exist_ok=True)
os.makedirs(STA, exist_ok=True)

TAGS = [
    ("u4_blfine", "baseline", "baseline"),
    ("u4_blcomp_i", "comp_i", "comp_i"),
    ("u4_blcomp_iii", "comp_iii", "comp_iii"),
    ("u4_blgap_wt", "I 1.0", "i_g10"),
    ("u4_bli_g12", "I 1.2", "i_g12"),
    ("u4_bli_g15", "I 1.5", "i_g15"),
    ("u4_bli_g18", "I 1.8", "i_g18"),
    ("u4_bliii_g12", "III 1.2", "iii_g12"),
    ("u4_bliii_g124", "III 1.24", "iii_g124"),
    ("u4_bliii_g144", "III 1.44", "iii_g144"),
    ("u4_bliii_g15", "III 1.5", "iii_g15"),
    ("u4_bliii_g18", "III 1.8", "iii_g18"),
]
ZONES = [("ZoneI", 0.20, 0.30), ("ZoneIII", 0.03, 0.10)]


def load_frames(tag):
    files = sorted(glob.glob(os.path.join(HEMO, tag, "frames", "*.txt")))
    mats, coords = [], None
    for f in files:
        try:
            df = pd.read_csv(f, sep=r"\s+", header=None, skiprows=1, engine="python",
                             on_bad_lines="skip")
            if df.shape[1] < 7 or len(df) < 50:
                continue
            a = df.iloc[:, :7].to_numpy(dtype=float)
        except Exception:
            continue
        c = a[:, 1:4]
        if coords is None:
            coords = c
        if len(c) != len(coords) or not np.allclose(c, coords, atol=1e-9):
            continue
        mats.append(np.column_stack([a[:, 6], a[:, 5], a[:, 4]]))
    if len(mats) < 5:
        return None
    return coords, np.stack(mats)


def fd_gradient(coords, values, k=8):
    N = len(coords)
    k = min(k, N - 1)
    tree = cKDTree(coords)
    dist, idx = tree.query(coords, k=k + 1)
    dist, idx = dist[:, 1:], idx[:, 1:]
    good = dist > 1e-7
    d = np.maximum(dist, 1e-12)
    if values.ndim == 1:
        values = values[:, None]
    diff = values[idx] - values[:, None, :]
    ratio = np.where(good[..., None], diff / d[..., None], 0.0)
    return np.sqrt(np.mean(ratio ** 2, axis=1))


def metrics(tag):
    r = load_frames(tag)
    if r is None:
        return None
    coords, tau = r
    mag = np.linalg.norm(tau, axis=2)
    tawss = mag.mean(axis=0)
    m = tau.mean(axis=0)
    mm = np.linalg.norm(m, axis=1)
    osi = 0.5 * (1.0 - mm / np.maximum(tawss, 1e-12))
    e = m / np.maximum(mm[:, None], 1e-12)
    proj = np.einsum("fni,ni->fn", tau, e)
    perp = tau - proj[..., None] * e[None, :, :]
    trans = np.linalg.norm(perp, axis=2).mean(axis=0)
    denom = np.maximum((1.0 - 2.0 * osi) * tawss, 1e-12)
    rrt = 1.0 / denom
    ecap = osi / np.maximum(tawss, 1e-12)
    valid = tawss >= 0.1
    ecap_v = ecap[valid]
    wssg = fd_gradient(coords, tawss)[:, 0]
    out = dict(
        n=int(len(coords)), frames=int(tau.shape[0]),
        wss_mean=float(mag.mean()), wss_p99=float(np.percentile(mag, 99)),
        tawss=float(tawss.mean()), tawss_p99=float(np.percentile(tawss, 99)),
        osi=float(osi.mean()), osi_p90=float(np.percentile(osi, 90)),
        rrt_mean=float(rrt[(tawss >= 0.1) & (osi <= 0.49)].mean()) if ((tawss >= 0.1) & (osi <= 0.49)).any() else None,
        rrt_median=float(np.median(rrt[(tawss >= 0.1) & (osi <= 0.49)])) if ((tawss >= 0.1) & (osi <= 0.49)).any() else None,
        rrt_p90=float(np.percentile(rrt[(tawss >= 0.1) & (osi <= 0.49)], 90)) if ((tawss >= 0.1) & (osi <= 0.49)).any() else None,
        lsa_pct=float((tawss < 0.5).mean() * 100),
        ecap=float(np.median(ecap_v)) if len(ecap_v) else None,
        ecap_p90=float(np.percentile(ecap_v, 90)) if len(ecap_v) else None,
        high_ecap_pct=float((ecap_v > 0.5).mean() * 100) if len(ecap_v) else None,
        transwss=float(trans.mean()), transwss_p90=float(np.percentile(trans, 90)),
        wssg=float(wssg.mean()), wssg_p90=float(np.percentile(wssg, 90)),
    )
    for name, lo, hi in ZONES:
        k = (coords[:, 2] >= lo) & (coords[:, 2] <= hi)
        if k.sum() > 100:
            out[name] = dict(
                tawss=float(tawss[k].mean()), osi=float(osi[k].mean()),
                lsa_pct=float((tawss[k] < 0.5).mean() * 100),
                ecap=float(np.median(ecap[k][valid[k]])) if valid[k].sum() else None,
                transwss=float(trans[k].mean()), wssg=float(wssg[k].mean()))
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", default="")
    ap.add_argument("--config", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out_dir = args.out or OUT
    os.makedirs(out_dir, exist_ok=True)
    cases = TAGS
    if args.config:
        cfg = json.load(open(args.config, encoding="utf-8"))
        cases = [(c["tag"], c.get("label", c["tag"]), c.get("key", c["tag"])) for c in cfg.get("cases", [])]
        zcfg = cfg.get("zones") or {}
        zs = []
        for name, z in zcfg.items():
            if "z_min_m" in z and "z_max_m" in z:
                zs.append((name, float(z["z_min_m"]), float(z["z_max_m"])))
        if zs:
            ZONES[:] = zs
    elif args.tags:
        wanted = {t.strip() for t in args.tags.split(",") if t.strip()}
        cases = [c for c in TAGS if c[0] in wanted]
    res = {}
    for tag, label, key in cases:
        m = metrics(tag)
        if m is None:
            print("skip", tag, flush=True)
            continue
        m.update(label=label, key=key)
        res[tag] = m
        print("OK", tag, "n=%d frames=%d" % (m["n"], m["frames"]), flush=True)
    json.dump(res, open(os.path.join(out_dir, "BL_WSS_METRICS.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    cols = ["tag", "label", "key", "n", "frames", "wss_mean", "wss_p99", "tawss", "tawss_p99",
            "osi", "osi_p90", "rrt_mean", "rrt_median", "rrt_p90", "lsa_pct", "ecap", "ecap_p90",
            "high_ecap_pct", "transwss", "transwss_p90", "wssg", "wssg_p90"]
    rows = []
    for tag, m in res.items():
        r = dict(tag=tag, **{k: m.get(k) for k in cols[1:] if k != "label"})
        r["label"] = m["label"]
        rows.append(r)
    pd.DataFrame(rows)[cols].to_csv(os.path.join(out_dir, "bl_wss_metrics.csv"), index=False)
    L = ["# BL WSS-derived metrics（25 HEMO frames）", "",
         "> TAWSS/OSI/RRT/LSA/ECAP/transWSS/WSSG 均从 BL 场重算；Zone I 0.20–0.30 m，Zone III 0.03–0.10 m。", "",
         "| case | TAWSS | OSI | RRT | LSA% | ECAP | transWSS | WSSG | Zone TAWSS |",
         "|---|---|---|---|---|---|---|---|---|"]
    for tag, m in res.items():
        z = m.get("ZoneIII") if ("iii" in m["key"] or m["key"] == "comp_iii") else m.get("ZoneI")
        z = z or {}
        L.append("| %s | %.3f | %.3f | %.1f | %.1f | %.3f | %.3f | %.1f | %.3f |" % (
            m["label"], m["tawss"], m["osi"], m["rrt_median"] or 0, m["lsa_pct"],
            m["ecap"] or 0, m["transwss"], m["wssg"], z.get("tawss", 0)))
    open(os.path.join(out_dir, "BL_WSS_METRICS.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("BL_WSS_METRICS written", len(res), "cases")


if __name__ == "__main__":
    main()
