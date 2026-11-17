#!/usr/bin/env python3
"""Grounding check: does every material number in an answer trace back to the snapshot?

Ported from the RevOps agent's answer-quality eval. Deterministic on purpose - an
LLM judging an LLM's numbers is the failure this exists to catch.

    python3 grounding.py --snapshot .revops/snapshot.json --answer "You are at $52,377..."

A number is GROUNDED when it equals (within rounding) a leaf of the snapshot, a
percent form of one, or a simple derivation the agent is allowed to make (a sum,
a difference, a ratio of two leaves). Small numbers (under 100, not percents) such
as "7 days" or "3 levers" are not checked.
"""
import argparse, itertools, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jsonio  # noqa: E402

MATERIAL_MIN = 100
NUM = re.compile(r"(?<![\w.])\$?-?\d[\d,]*(?:\.\d+)?\s*(%|[kK]\b|[mM]\b|mn\b|[bB]\b|bn\b)?")
DATES = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\b")       # dates and clock times are not amounts
MULT = {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "mn": 1e6, "b": 1e9, "B": 1e9, "bn": 1e9}


def extract(text):
    out = []
    for m in NUM.finditer(DATES.sub(" ", text or "")):
        raw = m.group(0)
        s = raw.replace("$", "").replace(",", "").strip()
        unit = m.group(1)
        if unit:
            s = s[: -len(unit)].strip()
        try:
            v = float(s)
        except ValueError:
            continue
        if unit in MULT:
            v *= MULT[unit]
        if not unit and "$" not in raw and "," not in raw and 1900 <= v <= 2100 and float(v).is_integer():
            continue                                  # a bare year ("in 2026") is not an amount
        is_pct = unit == "%"
        if is_pct or abs(v) >= MATERIAL_MIN:
            out.append({"raw": raw.strip(), "value": v, "percent": is_pct})
    return out


def leaves(obj, acc=None):
    acc = [] if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("brief", "daily", "series"):     # prose and long series are not sources of truth for answers
                continue
            leaves(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            leaves(v, acc)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        acc.append(float(obj))
    return acc


def headline(snapshot):
    """The few figures an answer may legitimately combine (a gap, a difference, a share).
    Derivations are allowed ONLY between these - allowing them between every leaf makes
    almost any number 'grounded', which is the failure this check exists to catch."""
    p = snapshot.get("pacing") or {}
    keys = ("goal", "revenue_mtd", "gap", "projected_month_end", "run_rate_daily", "needed_per_day")
    vals = [p.get(k) for k in keys]
    for r in snapshot.get("wow") or []:
        vals += [r.get("current"), r.get("prior")]
    return [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]


def allowed(snapshot):
    base = set(leaves(snapshot))
    vals = set(base)
    vals |= {v * 100 for v in base if abs(v) <= 1.5}           # rates quoted as percents
    for a, b in itertools.combinations(headline(snapshot), 2):  # one-step derivations, headline figures only
        vals.add(abs(a - b))
        if b and abs(a) <= abs(b):
            vals.add(a / b * 100)
        if a and abs(b) <= abs(a):
            vals.add(b / a * 100)
    return vals


def grounded(v, vals, percent):
    for a in vals:
        tol = 0.06 if percent else max(1.0, abs(a) * 0.002)   # rounding only: $52,377 for 52377.38, 41.7% for 0.4167
        if abs(abs(v) - abs(a)) <= tol:     # "down 27%" quotes a stored -0.27 by size, not sign
            return True
    return False


def check(answer, snapshot):
    vals = allowed(snapshot)
    nums = extract(answer)
    bad = [n["raw"] for n in nums if not grounded(n["value"], vals, n["percent"])]
    return {"numbers": len(nums), "ungrounded": bad, "grounded": not bad}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--answer", required=True)
    a = ap.parse_args()
    print(json.dumps(check(a.answer, jsonio.read(a.snapshot)), indent=2))
