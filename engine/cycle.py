"""cycle_drift.py -- 从 [WK] 日志解析每周期均值流量，评估 2 周期是否收敛（cycle1 vs cycle2 偏差）。

用法: python cycle_drift.py <tag> [<tag> ...]
输出: docs/validation/CYCLE_DRIFT.md/json
"""
import json
import os
import re
import sys

import numpy as np

WS = os.environ.get("CFD_WORKSPACE", os.getcwd())
WK = os.environ.get("CFD_WK", os.path.join(WS, "work", "wkcase"))
OUTD = os.environ.get("CFD_RESULTS", os.path.join(WS, "results"))
T = float(os.environ.get("CFD_PERIOD_S", "0.5"))
LINE = re.compile(r"\[WK\]\s+t=([\d.]+)\s+Qa=([-\d.eE+]+)\s+Qr=([-\d.eE+]+)\s+Qi=([-\d.eE+]+)")


def parse(path):
    raw = open(path, "rb").read()
    for enc in ("gbk", "utf-16", "latin-1"):
        try:
            s = raw.decode(enc)
            if "\x00" not in s:
                break
        except Exception:
            continue
    rows = []
    for line in s.splitlines():
        m = LINE.search(line)
        if m:
            rows.append([float(m.group(i)) for i in range(1, 5)])
    return np.asarray(rows) if rows else np.zeros((0, 4))


def main():
    tags = sys.argv[1:]
    res = {}
    lines = ["# 周期收敛检查（WK 分流，cycle1 vs cycle2）", "",
             "| 算例 | cycle1 arch/renal/iliac | cycle2 | 相对偏差 | 判定 |", "|---|---|---|---|---|"]
    for tag in tags:
        p = os.path.join(WK, "wk_%s.log" % tag)
        if not os.path.exists(p):
            continue
        a = parse(p)
        if len(a) == 0:
            continue
        out = {}
        for k in range(1, 6):
            sel = (a[:, 0] > (k - 1) * T - 1e-9) & (a[:, 0] <= k * T + 1e-9)
            if sel.sum() > 5:
                out[k] = a[sel, 1:4].mean(axis=0)
        if len(out) < 2:
            lines.append("| %s | 不足 2 周期 | - | - | - |" % tag)
            continue
        ks = sorted(out)
        last_two = (ks[-2], ks[-1])
        c1, c2 = out[last_two[0]], out[last_two[1]]
        s1, s2 = c1.sum(), c2.sum()
        r1, r2 = c1 / s1, c2 / s2
        dev = np.abs(r1 - r2).max() * 100
        ok = "OK" if dev < 5 else ("偏高" if dev < 10 else "未收敛")
        res[tag] = dict(last_two_cycles=[last_two[0], last_two[1]], cycle1=c1.tolist(), cycle2=c2.tolist(),
                        share1=r1.tolist(), share2=r2.tolist(), max_dev_pp=float(dev), verdict=ok)
        lines.append("| %s | %.1f/%.1f/%.1f%% | %.1f/%.1f/%.1f%% | %.2f pp | %s |" % (
            tag, r1[0] * 100, r1[1] * 100, r1[2] * 100, r2[0] * 100, r2[1] * 100, r2[2] * 100, dev, ok))
    os.makedirs(OUTD, exist_ok=True)
    json.dump(res, open(os.path.join(OUTD, "CYCLE_DRIFT.json"), "w", encoding="utf-8"), indent=1)
    open(os.path.join(OUTD, "CYCLE_DRIFT.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
