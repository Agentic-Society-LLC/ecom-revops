#!/usr/bin/env python3
"""Connector health and self-healing. Standard library only.

Ported from the self-healing connector layer of the Agentic Society RevOps agent.

THE CONTRACT for every connector registered in connectors.py:
  probe()                 a CHEAP read. Never throws, never writes. Returns a ProbeResult.
  pull(start, end)        the real data pull, {date: {column: value}}.
  heal(fail_class, n)     ONE self-heal attempt for that failure class, or None when the
                          connector has no self-heal for it (it is then alert-only for that
                          class - a legitimate choice, but explicit, never an omission).

THE RULES:
  - A failure is CLASSIFIED before anything is done about it. Transient classes
    (network, server, rate-limit, stale-cache) retry with backoff, max 3 attempts.
    Intervention classes (auth, not-found, missing-env, upstream-rejected) never retry:
    retrying a bad key three times only delays the alert.
  - A source that is down serves its LAST GOOD pull, stamped stale with its as-of.
    Never a fabricated 0, never presented as live.
  - One outage = one alert per source, per failure class, per day.
  - The summary counts EVERY source. It never says "all live" while a row says otherwise.
"""
import datetime as dt, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jsonio  # noqa: E402

STATE_DIR = os.environ.get("REVOPS_STATE_DIR", ".revops")
MAX_HEAL_ATTEMPTS = 3
SELF_HEALABLE = {"network", "server", "rate-limit", "stale-cache"}


def classify_failure(http_status=None, network_error=None, missing_env=None, upstream_status=None):
    if missing_env:
        return "missing-env"
    if network_error:
        return "network"
    if http_status in (401, 403):
        return "auth"
    if http_status == 404:
        return "not-found"
    if http_status == 429 or upstream_status == 429:
        return "rate-limit"
    if isinstance(http_status, int) and http_status >= 500:
        return "server"
    if isinstance(http_status, int) and 400 <= http_status < 500:
        return "upstream-rejected"   # a malformed request or bad id: retrying cannot fix it
    return None


def state_for_class(fail_class):
    if fail_class is None:
        return "connected"
    if fail_class in SELF_HEALABLE:
        return "degraded"
    return "failed"


FIX_STEPS = {
    "missing-env": "set the connector's environment variables (see README, Connectors)",
    "auth": "the key was rejected: create a new read-only key in the source and update .env",
    "not-found": "the store, location or account id is wrong: check it in .env",
    "upstream-rejected": "the source rejected the request itself: check the API version and ids in .env",
    "bad-data": "the file or feed is not in a readable shape (see the detail); fix the export and run sync again",
    "network": "network blip: retried automatically",
    "server": "the source is having an outage: retried automatically, last good data served meanwhile",
    "rate-limit": "rate limited: backed off and retried automatically",
    "stale-cache": "served last good data: retried automatically",
}


def backoff_seconds(attempt):
    return min(0.4 * (2 ** attempt), 5.0)


class ProbeResult(dict):
    """{ok, http_status, network_error, missing_env, detail}"""


def _path(*parts):
    p = os.path.join(STATE_DIR, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def save_last_good(name, data):
    jsonio.write(_path("last-good", f"{name}.json"), {"as_of": dt.datetime.now().isoformat(timespec="seconds"),
                                                     "data": {str(k): v for k, v in data.items()}})


def load_last_good(name):
    try:
        with open(_path("last-good", f"{name}.json")) as f:
            rec = json.load(f)
        return rec["as_of"], {dt.date.fromisoformat(k): v for k, v in rec["data"].items()}
    except (OSError, ValueError, KeyError):
        return None, None


def alert_key(name, fail_class, today=None):
    return f"conn-health:{name}:{fail_class}:{(today or dt.date.today()).isoformat()}"


def should_alert(name, fail_class):
    """True once per source + class + day. Records that it alerted."""
    p = _path("alerts.json")
    sent = jsonio.read(p, {}) or {}
    key = alert_key(name, fail_class)
    if key in sent:
        return False
    sent[key] = dt.datetime.now().isoformat(timespec="seconds")
    cutoff = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    sent = {k: v for k, v in sent.items() if k.rsplit(":", 1)[-1] >= cutoff}
    jsonio.write(p, sent)
    return True


def run_with_heal(name, connector, start, end, sleep=time.sleep):
    """Pull with classification, bounded self-heal and last-good fallback.
    Returns a health row: {name, state, fail_class, data, source, as_of, attempts, detail, fix}."""
    if not connector.configured():
        return {"name": name, "state": "off", "fail_class": None, "data": None, "source": None,
                "detail": "not configured (no credentials set)", "fix": connector.setup_hint}
    attempts, last = 0, None
    while True:
        try:
            data = connector.pull(start, end)
            save_last_good(name, data)
            return {"name": name, "state": "connected", "fail_class": None, "data": data, "source": "live",
                    "as_of": dt.datetime.now().isoformat(timespec="seconds"), "attempts": attempts + 1,
                    "detail": f"{len(data)} days pulled" + (" after self-heal" if attempts else "")}
        except Exception as e:   # anything a connector throws is classified, never allowed to kill the sync
            if not isinstance(e, ConnectorError):
                e = ConnectorError(f"{type(e).__name__}: {e}", bad_data=True,
                                   fix=f"the source sent data in a shape it cannot read: {e}")
            last = e
            fc = "bad-data" if e.bad_data else classify_failure(e.http_status, e.network_error, e.missing_env, e.upstream_status)
            healable = fc in SELF_HEALABLE and attempts < MAX_HEAL_ATTEMPTS - 1
            heal_note = connector.heal(fc, attempts) if healable else None
            if not healable:
                break
            sleep(backoff_seconds(attempts))
            attempts += 1
    fc = "bad-data" if last.bad_data else (classify_failure(last.http_status, last.network_error, last.missing_env, last.upstream_status) or "server")
    as_of, cached = load_last_good(name)
    row = {"name": name, "state": state_for_class(fc), "fail_class": fc, "attempts": attempts + 1,
           "detail": str(last), "fix": last.fix or FIX_STEPS.get(fc, "check the source")}
    if cached:
        row.update({"data": cached, "source": "last-good", "as_of": as_of,
                    "detail": f"{last} - serving last good data from {as_of} (stale, not live)"})
    else:
        row.update({"data": None, "source": None, "as_of": None})
    row["alert"] = should_alert(name, fc)
    return row


class ConnectorError(Exception):
    def __init__(self, msg, http_status=None, network_error=None, missing_env=None, upstream_status=None, fix=None,
                 bad_data=False):
        super().__init__(msg)
        self.bad_data = bad_data
        self.fix = fix   # a connector-specific fix step, when the generic one would mislead
        self.http_status, self.network_error = http_status, network_error
        self.missing_env, self.upstream_status = missing_env, upstream_status


# ------------------------------------------------------------- the summary
def counts(rows):
    rows = [r for r in rows if r["state"] != "off"]
    by = lambda s: sum(1 for r in rows if r["state"] == s)
    return {"total": len(rows), "connected": by("connected"), "degraded": by("degraded"),
            "failed": by("failed"), "unknown": by("unknown")}


def summary_text(c):
    if not c["total"]:
        return "No data sources connected yet. Running on the CSV export only."
    if c["connected"] == c["total"]:
        return "The 1 connected source is live." if c["total"] == 1 else f"All {c['total']} connected sources are live."
    parts = [f"{c[k]} {k}" for k in ("failed", "degraded", "unknown") if c[k]]
    return f"{c['connected']} of {c['total']} live - " + ", ".join(parts) + ". See the rows below."


# Which connection removes the most forecast error, in order. Used by the brief to
# name the ONE highest-value missing connection.
CONNECTION_VALUE = [
    ("shopify", "orders and revenue become live instead of a manual export"),
    ("funnel", "sessions, cart and checkout rates become measurable - the whole CRO view depends on it"),
    ("stripe", "cash actually collected, net of failed payments, instead of order totals"),
    ("meta_ads", "ad spend, so you can see revenue per ad dollar"),
    ("gohighlevel", "leads and deals for any high-ticket or wholesale side"),
]


def setup_completeness(present):
    """present: set of connection names with live or last-good data."""
    have = [n for n, _ in CONNECTION_VALUE if n in present]
    missing = [(n, why) for n, why in CONNECTION_VALUE if n not in present]
    return {"score": round(100 * len(have) / len(CONNECTION_VALUE)), "connected": have,
            "next_best": ({"name": missing[0][0], "why": missing[0][1]} if missing else None)}
