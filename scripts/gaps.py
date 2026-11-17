#!/usr/bin/env python3
"""The capability-gap log: every question the agent could not answer, every source
that was down, every ungrounded answer. Deduped by a stable key, so a recurring gap
is ONE entry with a count, not a pile. This file is the agent's backlog.

    python3 gaps.py list
    python3 gaps.py add --type cant_answer --subject "refund rate by product" --missing "no refunds column"
    python3 gaps.py close --key KEY --note "added Shopify refunds to the connector"
"""
import argparse, datetime as dt, hashlib, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jsonio  # noqa: E402

STATE_DIR = os.environ.get("REVOPS_STATE_DIR", ".revops")
TYPES = {"cant_answer", "source_down", "ungrounded_answer"}


def _path():
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, "gaps.json")


def load():
    return jsonio.read(_path(), {}) or {}


def key(gap_type, subject):
    norm = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", subject.lower())).strip()
    return hashlib.sha1(f"{gap_type}:{norm}".encode()).hexdigest()[:10]


def add(gap_type, subject, missing=""):
    if gap_type not in TYPES:
        raise ValueError(f"type must be one of {sorted(TYPES)}")
    gaps, k = load(), key(gap_type, subject)
    now = dt.datetime.now().isoformat(timespec="seconds")
    g = gaps.get(k)
    if g and g.get("status") == "open":
        g["count"] += 1
        g["last_seen"] = now
    else:
        gaps[k] = {"type": gap_type, "subject": subject[:300], "missing": missing[:2000], "count": 1,
                   "first_seen": now, "last_seen": now, "status": "open"}
    jsonio.write(_path(), gaps, indent=2)
    return k, gaps[k]


def close(k, note):
    gaps = load()
    if k in gaps:
        gaps[k].update({"status": "closed", "closed": dt.datetime.now().isoformat(timespec="seconds"), "note": note})
        jsonio.write(_path(), gaps, indent=2)
    return gaps.get(k)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a_ = sub.add_parser("add"); a_.add_argument("--type", required=True); a_.add_argument("--subject", required=True); a_.add_argument("--missing", default="")
    c_ = sub.add_parser("close"); c_.add_argument("--key", required=True); c_.add_argument("--note", default="")
    a = ap.parse_args()
    if a.cmd == "list":
        open_ = {k: v for k, v in load().items() if v["status"] == "open"}
        print(json.dumps(dict(sorted(open_.items(), key=lambda kv: -kv[1]["count"])), indent=2))
    elif a.cmd == "add":
        print(json.dumps(dict(zip(("key", "gap"), add(a.type, a.subject, a.missing))), indent=2))
    else:
        print(json.dumps(close(a.key, a.note), indent=2))
