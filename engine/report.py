"""Generate a patient case report from BL metrics + case master."""
import argparse
import glob
import json
import os

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--master", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    cfg = json.load(open(args.config, encoding="utf-8"))
    met = json.load(open(args.metrics, encoding="utf-8"))
    df = pd.read_csv(args.master)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    L = ["# %s REBOA CFD 病例报告（BL v5）" % cfg.get("patient_id", ""), "",
         "> 自动生成：BL 5 层/0.02 mm；WSS 类来自 25 帧 BL 场；Q/P 复用。", "",
         "## 1. 病例与分组", ""]
    for c in cfg.get("cases", []):
        L.append("- `%s`：%s，zone %s，%s" % (c["tag"], c.get("label", ""), c.get("zone", ""), c.get("kind", "")))
    L += ["", "## 2. BL 全指标", "",
          "| case | TAWSS | OSI | RRT | LSA% | ECAP | transWSS | WSSG |",
          "|---|---|---|---|---|---|---|---|"]
    for _, r in df.iterrows():
        L.append("| %s | %.3f | %.3f | %.2f | %.1f | %.3f | %.3f | %.1f |" % (
            r["label"], r["tawss"], r["osi"], r["rrt_median"], r["lsa_pct"],
            r["ecap_median"], r["transwss"], r["wssg"]))
    yp = os.path.join(os.path.dirname(os.path.abspath(args.metrics)), "YPLUS_ESTIMATE.md")
    if os.path.exists(yp):
        L += ["", "## 3. y+ 审计", "", open(yp, encoding="utf-8").read()]
    L += ["", "## 4. 限制", "",
          "- Movement 为准稳态累计灌注 + 时间加权静态 WSS 暴露，不是动态网格 WSS。",
          "- Path B/TierB 只做 Q/P，不产生球囊壁 WSS。",
          "- 完全阻断为近端域近似，远端 WSS 解析为 0。"]
    open(args.out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("patient report written", args.out)


if __name__ == "__main__":
    main()
