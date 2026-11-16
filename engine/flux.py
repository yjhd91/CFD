"""flux_check.py -- 通量账：读 syst case，用 /report/fluxes/mass-flow 报告各 BC 质量流量，检查 Σout−Σin < 1%。

用法: python flux_check.py <tag> [<tag> ...]
输出: tools/wkcase/flux_<tag>.log（原始）, docs/validation/FLUX_ACCOUNT.md/json（汇总）
"""
import json
import os
import re
import subprocess
import sys

WS = os.environ.get("CFD_WORKSPACE", os.getcwd())
WK = os.environ.get("CFD_WK", os.path.join(WS, "work", "wkcase"))
GEOM = os.environ.get("CFD_GEOM", os.path.join(WS, "meshes"))
FB = os.environ.get("CFD_FLUENT_BATCH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fluent_batch.ps1"))
OUTD = os.environ.get("CFD_RESULTS", os.path.join(WS, "results"))


def zone_names(tag):
    import h5py
    p = os.path.join(GEOM, "mesh_%s_bc.msh.h5" % tag)
    f = h5py.File(p, "r")
    g = f["meshes/1/faces/zoneTopology"]
    types = g["zoneType"][:]
    names = g["name"][0].decode("latin-1").split(";")
    f.close()
    inl, outs = [], []
    for t, nm in zip(types, names):
        t = int(t)
        if t == 10:
            inl.append(nm)
        elif t == 5:
            outs.append(nm)
    return inl, outs


def run_journal(name, lines, cores=4, timeout=2400):
    jp = os.path.join(WK, name + ".jou")
    lp = os.path.join(WK, name + ".log")
    open(jp, "w", encoding="ascii").write("\n".join(lines) + "\n")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", FB,
                        "-Journal", jp, "-Cores", str(cores), "-WorkDir", WK,
                        "-LogPath", lp, "-TimeoutSec", str(timeout)],
                       capture_output=True, text=True, timeout=timeout + 300)
    return r.returncode, lp


def parse_table(logpath):
    raw = open(logpath, "rb").read()
    for enc in ("gbk", "utf-16", "latin-1"):
        try:
            t = raw.decode(enc)
            if "\x00" not in t:
                break
        except Exception:
            continue
    lines = t.splitlines()
    rows = {}
    started = False
    for l in lines:
        if "Mass Flow Rate" in l and "Zone" in l:
            started = True
            continue
        if started:
            m = re.match(r"\s*([A-Za-z][\w:\-]*)\s+([-+\d.eE]+)\s*$", l)
            if m:
                try:
                    rows[m.group(1)] = float(m.group(2))
                except ValueError:
                    pass
            elif re.match(r"\s*-----", l):
                continue
            elif rows and not l.strip():
                continue
    if not rows:
        # 回退：直接匹配 "name value"
        for l in lines:
            m = re.match(r"\s*([A-Za-z][\w:\-]*)\s+([-+\d.eE]+)\s*$", l)
            if m:
                try:
                    rows[m.group(1)] = float(m.group(2))
                except ValueError:
                    pass
    return rows


def main():
    tags = sys.argv[1:]
    res = {}
    for tag in tags:
        case = os.path.join(WK, "wk_%s_syst.cas.gz" % tag)
        if not os.path.exists(case):
            print("skip (no syst case)", tag)
            continue
        inl, outs = zone_names(tag)
        names = inl + outs
        name = "flux_" + tag
        L = ['/file/read-case-data "%s"' % case.replace("\\", "/"),
             '/report/fluxes/mass-flow', 'no'] + names + ['()', 'no', 'exit']
        rc, lp = run_journal(name, L)
        rows = parse_table(lp)
        q_in = sum(rows.get(k, 0.0) for k in inl)
        q_out = -sum(rows.get(k, 0.0) for k in outs)  # Fluent 表中出流为负
        net = rows.get("Net")
        if net is None:
            net = sum(rows.get(k, 0.0) for k in inl + outs)
        pct = abs(net) / abs(q_in) * 100 if q_in else float("nan")
        res[tag] = dict(q_in=q_in, q_out=q_out, net=net, net_pct=pct, outlets=outs, rc=rc,
                        rows={k: rows.get(k) for k in names})
        print("%-12s q_in=%.4e q_out=%.4e net=%.3e (%.4f%%)" % (tag, q_in, q_out, net, pct), flush=True)
    os.makedirs(OUTD, exist_ok=True)
    json.dump(res, open(os.path.join(OUTD, "FLUX_ACCOUNT.json"), "w"), indent=1)
    lines = ["# 通量账（收缩期末）", "", "| 算例 | ΣQin | ΣQout | net | net% | 判定 |", "|---|---|---|---|---|---|"]
    for tag, r in res.items():
        ok = "OK" if r["net_pct"] < 1.0 else "FAIL"
        lines.append("| %s | %.4e | %.4e | %.3e | %.4f%% | %s |" % (
            tag, r["q_in"], r["q_out"], r["net"], r["net_pct"], ok))
    open(os.path.join(OUTD, "FLUX_ACCOUNT.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
