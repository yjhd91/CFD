"""Build a patient-level BL master table from patient.json + BL_WSS_METRICS.json."""
import argparse
import json
import os

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    cfg = json.load(open(args.config, encoding="utf-8"))
    met = json.load(open(args.metrics, encoding="utf-8"))
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    rows = []
    for c in cfg.get("cases", []):
        tag = c["tag"]
        m = met.get(tag)
        if not m:
            print("skip (no metrics)", tag)
            continue
        rows.append(dict(
            patient_id=cfg.get("patient_id", ""), tag=tag,
            label=c.get("label", tag), key=c.get("key", tag), zone=c.get("zone", ""),
            kind=c.get("kind", ""), gap_mm=c.get("gap_mm", ""),
            tawss=m["tawss"], osi=m["osi"], rrt_median=m["rrt_median"],
            rrt_p90=m["rrt_p90"], lsa_pct=m["lsa_pct"], ecap_median=m["ecap"],
            ecap_p90=m["ecap_p90"], high_ecap_pct=m["high_ecap_pct"],
            transwss=m["transwss"], transwss_p90=m["transwss_p90"],
            wssg=m["wssg"], wssg_p90=m["wssg_p90"],
            wss_mean=m["wss_mean"], wss_p99=m["wss_p99"],
            bl_status="BL-on", wss_method="BL 5 layers 0.02 mm",
        ))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(out, "CASE_MASTER_RESULTS.csv"), index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(os.path.join(out, "CASE_MASTER_RESULTS.xlsx"), engine="openpyxl") as w:
        df.to_excel(w, sheet_name="BL_master", index=False)
    L = ["# %s 病例主表（BL v5）" % cfg.get("patient_id", ""), "",
         "| case | zone | gap | TAWSS | OSI | RRT | LSA% | ECAP | transWSS | WSSG |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in df.iterrows():
        L.append("| %s | %s | %s | %.3f | %.3f | %.2f | %.1f | %.3f | %.3f | %.1f |" % (
            r["label"], r["zone"], r["gap_mm"], r["tawss"], r["osi"], r["rrt_median"],
            r["lsa_pct"], r["ecap_median"], r["transwss"], r["wssg"]))
    open(os.path.join(out, "CASE_MASTER_RESULTS.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("patient master written", len(df), "rows", out)


if __name__ == "__main__":
    main()
