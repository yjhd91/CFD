"""Validate a patient BL package: metrics count, flux, cycle drift, HEMO frames."""
import argparse
import glob
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--metrics", required=True)
    args = ap.parse_args()
    cfg = json.load(open(args.config, encoding="utf-8"))
    met = json.load(open(args.metrics, encoding="utf-8"))
    cases = cfg.get("cases", [])
    tags = [c["tag"] for c in cases]
    out = {"patient_id": cfg.get("patient_id"), "checks": {}, "pass": True}
    out["checks"]["metrics_count"] = {"expected": len(tags), "observed": len(met),
                                      "pass": len(met) == len(tags)}
    base = os.path.dirname(os.path.abspath(args.config))
    flux_p = os.path.join(base, "results", "FLUX_ACCOUNT.json")
    cycle_p = os.path.join(base, "results", "CYCLE_DRIFT.json")
    if os.path.exists(flux_p):
        f = json.load(open(flux_p, encoding="utf-8"))
        mx = max((v.get("net_pct", 0) for v in f.values()), default=0)
        out["checks"]["flux"] = {"max_pct": mx, "n": len(f), "pass": mx < 1.0 and len(f) >= len(tags)}
    if os.path.exists(cycle_p):
        c = json.load(open(cycle_p, encoding="utf-8"))
        mx = max((v.get("max_dev_pp", 0) for v in c.values()), default=0)
        out["checks"]["cycle"] = {"max_pp": mx, "n": len(c), "pass": mx < 5.0 and len(c) >= len(tags)}
    hemo_root = cfg.get("paths", {}).get("hemo") or os.environ.get("CFD_HEMO") or os.path.join(os.path.dirname(os.path.abspath(args.config)), "work", "hemo")
    frames = {}
    for t in tags:
        n = len(glob.glob(os.path.join(hemo_root, t, "frames", "*.txt")))
        frames[t] = n
    out["checks"]["frames"] = {"min": min(frames.values()) if frames else 0,
                               "n_ge_25": sum(1 for v in frames.values() if v >= 25),
                               "pass": bool(frames) and min(frames.values()) >= 25}
    for v in out["checks"].values():
        if not v.get("pass", False):
            out["pass"] = False
    outp = os.path.join(base, "validation", "validate.json")
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    json.dump(out, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    sys.exit(0 if out["pass"] else 1)


if __name__ == "__main__":
    main()
