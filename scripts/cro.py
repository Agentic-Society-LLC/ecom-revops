#!/usr/bin/env python3
"""CRO tools: test sizing, an honest significance check, and the experiment backlog.
Standard library only.

    python3 cro.py size     --baseline 0.025 --lift 0.10 [--daily-sessions 1500]
    python3 cro.py check    --a 12000 --a-conv 300 --b 12050 --b-conv 352 --planned 24000
    python3 cro.py add      --name "Express checkout above the fold" --stage "Cart to checkout" --impact 8 --confidence 6 --ease 7 [--hypothesis "..."]
    python3 cro.py backlog  [--snapshot .revops/snapshot.json]
    python3 cro.py result   --name "..." --outcome won|lost|flat [--note "..."]

THE RULES (why this file exists - most CRO "wins" are noise):
  - Size the test BEFORE it starts. No sample size, no test.
  - No peeking verdicts: until each arm reaches the planned sample, the answer is
    "keep running", however good it looks.
  - A result is only "won" at 95% confidence (two-sided). Anything else is "no
    difference detected", which is a real result, not a failure.
  - The backlog is ranked by ICE score weighted by the dollars the stage is leaking,
    so the next test always goes where the money is.
"""
import argparse, datetime as dt, json, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jsonio  # noqa: E402

STATE_DIR = os.environ.get("REVOPS_STATE_DIR", ".revops")
Z_ALPHA = 1.959964   # two-sided 95%
Z_BETA = 0.841621    # 80% power


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def sample_size(baseline, rel_lift):
    """Visitors needed PER ARM to detect a relative lift with 95% confidence, 80% power."""
    if not (0 < baseline < 1) or rel_lift <= 0:
        raise ValueError("baseline must be between 0 and 1 and lift above 0")
    p1, p2 = baseline, baseline * (1 + rel_lift)
    if p2 >= 1:
        raise ValueError("that lift takes the rate above 100%")
    pbar = (p1 + p2) / 2
    n = (Z_ALPHA * math.sqrt(2 * pbar * (1 - pbar)) + Z_BETA * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / (p2 - p1) ** 2
    return math.ceil(n)


def check(a_n, a_k, b_n, b_k, planned_per_arm=None):
    """Two-proportion z-test with a no-peeking gate."""
    if min(a_n, b_n) <= 0:
        return {"verdict": "unavailable", "detail": "an arm has no visitors"}
    if a_k < 0 or b_k < 0 or a_k > a_n or b_k > b_n:
        return {"verdict": "unavailable",
                "detail": "conversions must be between 0 and the visitors in that arm - check the numbers you entered"}
    pa, pb = a_k / a_n, b_k / b_n
    pool = (a_k + b_k) / (a_n + b_n)
    se = math.sqrt(pool * (1 - pool) * (1 / a_n + 1 / b_n)) if 0 < pool < 1 else 0
    z = (pb - pa) / se if se else 0.0
    p_value = 2 * (1 - norm_cdf(abs(z)))
    se_d = math.sqrt(pa * (1 - pa) / a_n + pb * (1 - pb) / b_n)
    ci = (pb - pa - Z_ALPHA * se_d, pb - pa + Z_ALPHA * se_d)
    out = {"rate_a": pa, "rate_b": pb, "relative_lift": (pb - pa) / pa if pa else None,
           "p_value": p_value, "diff_95ci": ci}
    if planned_per_arm and min(a_n, b_n) < planned_per_arm:
        out.update({"verdict": "keep running",
                    "detail": f"{min(a_n, b_n):,} of {planned_per_arm:,} planned visitors per arm. "
                              "Calling it now is peeking and will hand you false winners."})
        return out
    if not planned_per_arm:
        out["warning"] = "no planned sample size given; this verdict is only valid if the size was fixed in advance"
    if p_value < 0.05:
        out.update({"verdict": "B wins" if pb > pa else "A wins", "detail": "significant at 95% confidence"})
    else:
        out.update({"verdict": "no difference detected", "detail": "not significant at 95%; ship the cheaper version or test a bigger change"})
    return out


# ------------------------------------------------------------------ backlog
def _load():
    try:
        return jsonio.read(os.path.join(STATE_DIR, "experiments.json"), [])
    except Exception:
        return []


def _save(items):
    jsonio.write(os.path.join(STATE_DIR, "experiments.json"), items, indent=2)


def ranked(items, snapshot=None):
    leak = {}
    if snapshot:
        for l in snapshot.get("leaks", []):
            if l.get("dollars"):
                leak[l["stage"]] = max(0.0, l["dollars"])
    top = max(leak.values()) if leak else 0
    out = []
    for e in items:
        if e.get("outcome"):
            continue
        ice = (e["impact"] + e["confidence"] + e["ease"]) / 3
        weight = 1 + (leak.get(e.get("stage"), 0) / top if top else 0)   # up to 2x where the money leaks
        out.append({**e, "ice": round(ice, 1), "leak_weight": round(weight, 2), "score": round(ice * weight, 1),
                    "stage_leak_dollars": leak.get(e.get("stage"))})
    return sorted(out, key=lambda e: -e["score"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("size"); s.add_argument("--baseline", type=float, required=True); s.add_argument("--lift", type=float, required=True); s.add_argument("--daily-sessions", type=float)
    c = sub.add_parser("check")
    for k in ("a", "a-conv", "b", "b-conv"):
        c.add_argument("--" + k, type=int, required=True)
    c.add_argument("--planned", type=int, help="the per-arm sample size fixed BEFORE the test")
    d = sub.add_parser("add")
    d.add_argument("--name", required=True); d.add_argument("--stage", required=True); d.add_argument("--hypothesis", default="")
    for k in ("impact", "confidence", "ease"):
        d.add_argument("--" + k, type=int, required=True, choices=range(1, 11))
    b = sub.add_parser("backlog"); b.add_argument("--snapshot", default=os.path.join(STATE_DIR, "snapshot.json"))
    r = sub.add_parser("result"); r.add_argument("--name", required=True); r.add_argument("--outcome", required=True, choices=["won", "lost", "flat"]); r.add_argument("--note", default="")
    a = ap.parse_args()
    if a.cmd == "size":
        n = sample_size(a.baseline, a.lift)
        out = {"per_arm": n, "total": 2 * n, "baseline": a.baseline, "detects_relative_lift": a.lift,
               "confidence": "95%", "power": "80%"}
        if a.daily_sessions:
            out["days_at_your_traffic"] = math.ceil(2 * n / a.daily_sessions)
            out["note"] = "run full weeks (7, 14, 21 days) so weekday and weekend shoppers are both in"
        print(json.dumps(out, indent=2))
    elif a.cmd == "check":
        print(json.dumps(check(a.a, a.a_conv, a.b, a.b_conv, a.planned), indent=2))
    elif a.cmd == "add":
        items = [e for e in _load() if e["name"] != a.name]
        items.append({"name": a.name, "stage": a.stage, "hypothesis": a.hypothesis, "impact": a.impact,
                      "confidence": a.confidence, "ease": a.ease, "added": str(dt.date.today())})
        _save(items)
        print(f"added: {a.name}")
    elif a.cmd == "backlog":
        snap = jsonio.read(a.snapshot) if os.path.exists(a.snapshot) else None
        print(json.dumps(ranked(_load(), snap), indent=2))
    else:
        items = _load()
        for e in items:
            if e["name"] == a.name:
                e.update({"outcome": a.outcome, "note": a.note, "decided": str(dt.date.today())})
        _save(items)
        print(f"recorded: {a.name} -> {a.outcome}")


if __name__ == "__main__":
    main()
