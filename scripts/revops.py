#!/usr/bin/env python3
"""ecom-revops engine. Standard library only, Python 3.8+.

    python3 revops.py train     --goal 50000 [--aov 85] [--cr 0.025] [--sessions-per-day 1500] [--by NAME]
    python3 revops.py brief     [--data daily.csv] [--post-state]      the daily brief (markdown)
    python3 revops.py pacing    [--data daily.csv]
    python3 revops.py funnel    [--data daily.csv]
    python3 revops.py wow       [--data daily.csv]
    python3 revops.py whatif    [--cr 0.03] [--aov 90] [--sessions-per-day 1800] [--goal 60000] [--horizon-days 90] [--save NAME]
    python3 revops.py scenarios                                       list saved scenarios side by side
    python3 revops.py snapshot  [--data daily.csv]                    everything, as JSON (bot + dashboard read this)
    python3 revops.py dashboard [--data daily.csv] [--out dashboard.html]
    (every command takes --today YYYY-MM-DD to replay a past day)

Ported from the Agentic Society RevOps agent. THE NUMBERS CONTRACT:
  1. Blank = UNAVAILABLE. 0 = a REAL zero. Never confused, never zero-filled.
  2. A window total exists only when every day in it is present.
  3. No percent against a real-zero baseline; no "flat" when a side is missing.
  4. A rate needs MIN_SAMPLE entrants, else unavailable with the count.
  5. Every rate says where it came from: scenario > measured > trained > starting default,
     in that precedence, and trained values carry who set them and when.
  6. Scenarios never overwrite actuals.
"""
import argparse, csv, datetime as dt, hashlib, io, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import jsonio  # noqa: E402
STATE_DIR = os.environ.get("REVOPS_STATE_DIR", ".revops")
MIN_SAMPLE = 30
ANOMALY_PCT = 0.15          # a funnel RATE moving more than this week over week is flagged
WOW_ALARM_PCT = 0.30        # a VOLUME (revenue/orders/sessions) swing that raises an alarm
MAX_SERIES = 8              # weeks of week-over-week history retained for the trend view
LEVER_LIFT = 0.10           # "which lever, lifted 10%, adds the most revenue from here?"

COLUMNS = {
    "date": ["date", "day", "Day", "Date"],
    "sessions": ["sessions", "Sessions", "Online store sessions"],
    "added_to_cart": ["added_to_cart", "Sessions with cart additions", "Added to cart", "add_to_carts"],
    "reached_checkout": ["reached_checkout", "Sessions that reached checkout", "Reached checkout", "begin_checkout"],
    "orders": ["orders", "Orders", "Sessions that completed checkout", "Sessions converted", "purchases"],
    "revenue": ["revenue", "Net sales", "Total sales", "net_sales", "purchase_revenue"],
    "ad_spend": ["ad_spend", "Ad spend", "spend", "Amount spent (USD)"],
    "cash_collected": ["cash_collected"],
    "new_opportunities": ["new_opportunities"],
    "deals_won": ["deals_won"],
    "won_value": ["won_value"],
}
FIELDS = [k for k in COLUMNS if k != "date"]
STAGES = [("Session to cart", "sessions", "added_to_cart"),
          ("Cart to checkout", "added_to_cart", "reached_checkout"),
          ("Checkout to order", "reached_checkout", "orders")]

# Starting defaults, used ONLY until a rate is measured or trained, and always labelled.
DEFAULTS = {"cr": 0.02, "aov": 75.0, "sessions_per_day": 1000.0}
TRAIN_RANGES = {"goal": (1, 1e9), "aov": (1, 1e6), "cr": (0.0001, 0.5), "sessions_per_day": (1, 1e8)}


# =================================================================== loading
def _num(raw):
    if raw is None:
        return None
    s = str(raw).strip().replace("$", "").replace(",", "")
    if s == "" or s.lower() in ("na", "n/a", "null", "none", "-"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def load_text(text):
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    colmap = {}
    for field, names in COLUMNS.items():
        for n in names:
            if n in header:
                colmap[field] = n
                break
    if "date" not in colmap:
        raise DataFormatError("no date column found; expected one of: " + ", ".join(COLUMNS["date"]))
    rows = {}
    for r in reader:
        raw = str(r.get(colmap["date"]) or "").strip()
        if not raw:
            continue
        rows[parse_date(raw)] = {f: (_num(r.get(colmap[f])) if f in colmap else None) for f in FIELDS}
    return rows, sorted(colmap)


class DataFormatError(ValueError):
    """A source file is readable but not in a shape the engine can trust."""


UNAMBIGUOUS = ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y")


def parse_date(raw):
    """ISO and month-name dates are read directly. Slash dates like 11/12/2026 are
    AMBIGUOUS (November 12 or 11 December), so they need REVOPS_DATE_FORMAT set
    (%m/%d/%Y or %d/%m/%Y) - a guess here would silently shift every day."""
    raw = raw.strip()
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        pass
    fmt = os.environ.get("REVOPS_DATE_FORMAT")
    for f in ((fmt,) if fmt else ()) + UNAMBIGUOUS:
        try:
            return dt.datetime.strptime(raw, f).date()
        except ValueError:
            continue
    raise DataFormatError(f"cannot read the date '{raw}'. Use YYYY-MM-DD, or set REVOPS_DATE_FORMAT "
                          "(for example %m/%d/%Y for 11/16/2026) in .env")


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return load_text(f.read())


# =================================================================== state
def _state(name, default):
    v = jsonio.read(os.path.join(STATE_DIR, name), None)
    return default if v is None else v


def _save(name, obj):
    jsonio.write(os.path.join(STATE_DIR, name), obj, indent=2)


def train(values, by):
    """Store owner-supplied numbers with per-field provenance. Out-of-range values are
    refused and named, never silently clamped."""
    cfg = _state("config.json", {})
    stamped, dropped = [], []
    for k, v in values.items():
        if v is None:
            continue
        lo, hi = TRAIN_RANGES[k]
        if not (lo <= v <= hi):
            dropped.append(f"{k}={v} (allowed {lo} to {hi})")
            continue
        cfg[k] = v
        cfg[f"trainedAt_{k}"] = dt.datetime.now().isoformat(timespec="seconds")
        cfg[f"trainedBy_{k}"] = by
        stamped.append(k)
    _save("config.json", cfg)
    return {"saved": stamped, "refused": dropped, "config": cfg}


def resolve(key, measured, scenario=None):
    """THE precedence: scenario > measured > trained > starting default. Returns
    {value, source, note} so every number can say where it came from."""
    cfg = _state("config.json", {})
    if scenario is not None:
        return {"value": scenario, "source": "scenario", "note": "what-if override"}
    if measured is not None:
        return {"value": measured, "source": "measured", "note": "from your data"}
    if cfg.get(key) is not None:
        at = cfg.get(f"trainedAt_{key}", "")[:10]
        age = (dt.date.today() - dt.date.fromisoformat(at)).days if at else None
        return {"value": cfg[key], "source": "trained",
                "note": f"trained by {cfg.get(f'trainedBy_{key}') or 'unknown'}" + (f", {age} days ago" if age is not None else "")}
    if key in DEFAULTS:
        return {"value": DEFAULTS[key], "source": "default", "note": "starting estimate, not your data"}
    return {"value": None, "source": "unavailable", "note": "not measured and not trained"}


# =================================================================== windows
def window_total(rows, field, start, end):
    days = (end - start).days + 1
    vals, missing = [], 0
    for i in range(days):
        v = rows.get(start + dt.timedelta(i), {}).get(field)
        if v is None:
            missing += 1
        else:
            vals.append(v)
    if missing:
        return {"value": None, "status": "unavailable",
                "detail": f"{missing} of {days} days have no {field} (blank, not zero)"}
    return {"value": sum(vals), "status": "live", "days": days}


def rate(num, den):
    if num["value"] is None or den["value"] is None:
        return {"value": None, "status": "unavailable", "detail": "one side is unavailable"}
    if den["value"] < MIN_SAMPLE:
        return {"value": None, "status": "unavailable",
                "detail": f"only {int(den['value'])} entrants, below the minimum sample of {MIN_SAMPLE}"}
    return {"value": num["value"] / den["value"], "status": "live", "n": int(den["value"]), "k": int(num["value"])}


def compare(cur, pri, label):
    c, p = cur["value"], pri["value"]
    if c is None or p is None:
        which = "both weeks" if c is None and p is None else ("this week" if c is None else "last week")
        return {"label": label, "current": None, "prior": None, "delta": None, "pct": None, "direction": None,
                "status": "unavailable", "detail": f"no real value for {which}; direction unknown, not flat"}
    d = c - p
    direction = "up" if d > 0 else ("down" if d < 0 else "flat")
    if p == 0:
        return {"label": label, "current": c, "prior": p, "delta": d, "pct": None, "direction": direction,
                "status": "no-baseline", "detail": "last week was a real 0: absolute change only, no percent"}
    return {"label": label, "current": c, "prior": p, "delta": d, "pct": d / p, "direction": direction, "status": "ok"}


def weeks(today):
    end = today - dt.timedelta(days=1)
    return (end - dt.timedelta(days=6), end), (end - dt.timedelta(days=13), end - dt.timedelta(days=7))


def month_bounds(today):
    first = today.replace(day=1)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    return first, last


# =================================================================== funnel
def funnel(rows, start, end):
    t = {f: window_total(rows, f, start, end) for f in FIELDS}
    stages = [{"stage": label, "entrants": a, "passers": b, **rate(t[b], t[a])} for label, a, b in STAGES]
    aov = t["revenue"]["value"] / t["orders"]["value"] if (t["revenue"]["value"] is not None and t["orders"]["value"]) else None
    return {"window": f"{start}..{end}", "totals": t, "stages": stages,
            "conversion_rate": rate(t["orders"], t["sessions"]), "aov": aov}


def leaks_in_dollars(cur, base):
    """Per stage: revenue lost because the stage converted below its own baseline,
    holding every other stage at its current rate."""
    out = []
    aov = cur["aov"] or base["aov"]
    if not aov:
        return out
    for i, (label, a, _b) in enumerate(STAGES):
        cs, bs = cur["stages"][i], base["stages"][i]
        if cs["value"] is None or bs["value"] is None:
            out.append({"stage": label, "dollars": None, "status": "unavailable",
                        "detail": cs.get("detail") or bs.get("detail")})
            continue
        downstream = 1.0
        for j in range(i + 1, len(STAGES)):
            if cur["stages"][j]["value"] is None:
                downstream = None
                break
            downstream *= cur["stages"][j]["value"]
        if downstream is None:
            out.append({"stage": label, "dollars": None, "status": "unavailable", "detail": "a later stage is unavailable"})
            continue
        lost = cur["totals"][a]["value"] * (bs["value"] - cs["value"]) * downstream * aov
        out.append({"stage": label, "current_rate": cs["value"], "baseline_rate": bs["value"], "dollars": lost, "status": "live"})
    return sorted(out, key=lambda x: -(x["dollars"] if x["dollars"] is not None else -1e18))


def stalled_stages(f):
    """A hard leak: real volume enters a stage and zero comes out. Distinct from the
    always-present biggest leak. An unavailable stage is skipped, never flagged."""
    out = []
    for label, a, b in STAGES:
        up, down = f["totals"][a]["value"], f["totals"][b]["value"]
        if up is not None and up > 0 and down == 0:
            out.append({"type": "stall", "key": b, "detail": f"{int(up)} {a.replace('_', ' ')} but 0 {b.replace('_', ' ')}"})
    return out


# =================================================================== pacing
def pacing(rows, goal, today):
    first, last = month_bounds(today)
    through = today - dt.timedelta(days=1)
    if through < first:
        return {"status": "unavailable", "detail": "no complete day in this month yet"}
    elapsed, remaining = (through - first).days + 1, (last - through).days
    dim = (last - first).days + 1
    rev = window_total(rows, "revenue", first, through)
    if rev["value"] is None:
        return {"status": "unavailable", "detail": rev["detail"], "window": f"{first}..{through}"}
    t14 = window_total(rows, "revenue", through - dt.timedelta(days=13), through)
    if t14["value"] is not None:
        daily, basis = t14["value"] / 14, "trailing 14-day average"
    else:
        daily, basis = rev["value"] / elapsed, "month-to-date average (14-day history incomplete)"
    projected = rev["value"] + daily * remaining
    gap = max(0.0, goal - rev["value"])
    w_start = max(first, through - dt.timedelta(days=13)) if t14["value"] is None else through - dt.timedelta(days=13)
    f14 = funnel(rows, w_start, through)          # the SAME window as the run rate, so the levers reproduce it
    cr = resolve("cr", f14["conversion_rate"]["value"])
    aov = resolve("aov", f14["aov"])
    s_tot = f14["totals"]["sessions"]["value"]
    spd = resolve("sessions_per_day", s_tot / ((through - w_start).days + 1) if s_tot is not None else None)
    orders_needed = math.ceil(gap / aov["value"]) if aov["value"] else None
    sessions_needed = math.ceil(orders_needed / cr["value"]) if (orders_needed is not None and cr["value"]) else None
    measured_inputs = sum(1 for x in (cr, aov) if x["source"] == "measured")
    conf = "high" if (elapsed >= 14 and t14["value"] is not None and measured_inputs == 2) else (
        "medium" if elapsed >= 7 else "low")
    ahead = (rev["value"] / goal) >= (elapsed / dim) if goal > 0 else None   # cash share vs time share
    return {"status": "live", "as_of": str(through), "window": f"{first}..{through}", "goal": goal,
            "revenue_mtd": rev["value"], "gap": gap, "days_elapsed": elapsed, "days_left": remaining,
            "ahead": ahead, "run_rate_daily": daily, "run_rate_basis": basis,
            "projected_month_end": projected, "projected_vs_goal": projected - goal,
            "needed_per_day": (gap / remaining) if remaining else None,
            "orders_needed": orders_needed, "sessions_needed": sessions_needed,
            "aov": aov, "conversion_rate": cr, "sessions_per_day": spd, "lever_window": f"{w_start}..{through}",
            "confidence": conf}


def next_7_days(rows, today):
    """An ESTIMATE of the next 7 days from the trailing 7 complete days. Labelled."""
    (cs, ce), _ = weeks(today)
    t = window_total(rows, "revenue", cs, ce)
    if t["value"] is None:
        return {"value": None, "status": "unavailable", "detail": t["detail"]}
    return {"value": t["value"], "status": "estimate", "basis": f"same as the last 7 days ({cs}..{ce})",
            "window": f"{today}..{today + dt.timedelta(days=6)}"}


def biggest_lever(p):
    """The size of the prize: what a 10% lift on any lever adds by month-end. In
    e-commerce revenue = sessions x conversion x AOV, so a 10% lift on any one is worth
    the same 10% of the projected rest of month; the funnel leak picks WHICH lever.
    Built from the live run rate (measured revenue), never from starting guesses."""
    if p.get("status") != "live" or not p.get("days_left"):
        return None
    rest = p["run_rate_daily"] * p["days_left"]
    return {"gain": rest * LEVER_LIFT, "base": rest, "lift": LEVER_LIFT, "basis": p["run_rate_basis"],
            "note": "a 10% lift on traffic, conversion or AOV is worth the same; the leak picks which"}


# =================================================================== forecast self-calibration
def calibrate(p, today):
    """Carry the last forecast forward and compare. Never fabricates a history."""
    hist = _state("forecasts.json", [])
    month = today.strftime("%Y-%m")
    prior = [h for h in hist if h["month"] == month and h["date"] < str(today)]
    out = None
    if prior and p.get("status") == "live":
        last = prior[-1]
        drift = p["projected_month_end"] - last["projected"]
        out = {"last_forecast": last["projected"], "made_on": last["date"], "now": p["projected_month_end"],
               "drift": drift, "verdict": "tightening" if abs(drift) < 0.05 * max(1, last["projected"]) else "moving"}
    return out, hist


def record_forecast(p, today, hist):
    if p.get("status") != "live":
        return
    hist = [h for h in hist if not (h["month"] == today.strftime("%Y-%m") and h["date"] == str(today))]
    hist.append({"month": today.strftime("%Y-%m"), "date": str(today), "projected": p["projected_month_end"]})
    _save("forecasts.json", hist[-120:])


# =================================================================== week over week
def wow(rows, today):
    (cs, ce), (ps, pe) = weeks(today)
    out = [compare(window_total(rows, f, cs, ce), window_total(rows, f, ps, pe), label)
           for f, label in (("revenue", "Revenue"), ("orders", "Orders"), ("sessions", "Sessions"))]
    cf, pf = funnel(rows, cs, ce), funnel(rows, ps, pe)
    for i, (label, _a, _b) in enumerate(STAGES):
        out.append(compare(cf["stages"][i], pf["stages"][i], label + " rate"))
    out.append(compare(cf["conversion_rate"], pf["conversion_rate"], "Conversion rate"))
    return {"this_week": f"{cs}..{ce}", "last_week": f"{ps}..{pe}", "rows": out, "funnel_now": cf, "funnel_prior": pf}


def wow_series(w, today):
    """Retain up to MAX_SERIES weekly readings for the trend view. A convenience only:
    a delta always comes from two real window reads, never from this series."""
    series = [s for s in _state("wow-series.json", []) if s["week"] != w["this_week"]]
    rev = next(r for r in w["rows"] if r["label"] == "Revenue")
    series.append({"week": w["this_week"], "revenue": rev["current"], "written": str(today)})
    series = sorted(series, key=lambda s: s["week"])[-MAX_SERIES:]
    _save("wow-series.json", series)
    return series


def anomalies(w):
    out = []
    for r in w["rows"]:
        if r["status"] != "ok":
            continue
        is_rate = "rate" in r["label"].lower()
        if abs(r["pct"]) >= (ANOMALY_PCT if is_rate else WOW_ALARM_PCT):
            out.append({"type": "wow", "key": r["label"], "direction": r["direction"], "pct": r["pct"],
                        "detail": f"{r['label']} {r['direction']} {abs(r['pct']) * 100:.0f}% week over week"})
    # One cause, one line: when a funnel STAGE moved the same way as overall conversion,
    # the stage is the cause - fold the conversion flag into it instead of listing both.
    conv = next((a for a in out if a["key"] == "Conversion rate"), None)
    stage = max((a for a in out if a["key"].endswith(" rate") and a["key"] != "Conversion rate"
                 and conv and a["direction"] == conv["direction"]), key=lambda a: abs(a["pct"]), default=None)
    if conv and stage:
        out.remove(conv)
        stage["detail"] += f" (this is what moved overall conversion {conv['direction']} {abs(conv['pct']) * 100:.0f}%)"
    return sorted(out, key=lambda a: -abs(a["pct"])) + stalled_stages(w["funnel_now"])


def fingerprint(items):
    """The anomaly SITUATION, not the numbers: same kinds + keys + directions on two
    mornings = same fingerprint = no re-alarm. Magnitude is excluded on purpose."""
    sig = sorted(f"{a['type']}:{a['key']}:{a.get('direction', '')}" for a in items)
    return hashlib.sha1("|".join(sig).encode()).hexdigest()[:12] if sig else ""


# =================================================================== what-if
def whatif(rows, today, goal=None, cr=None, aov=None, sessions_per_day=None, horizon_days=None):
    """A scenario as RATIOS on the live run rate: daily = live daily x (new cr / base cr)
    x (new aov / base aov) x (new sessions / base sessions). With no overrides every ratio
    is 1, so it reproduces the live projection exactly, whatever is or is not measured.
    Each lever says where its base came from; a base that is only a starting guess is
    named in `assumed`, so an override against a guess is never passed off as fact."""
    goal = goal or _state("config.json", {}).get("goal") or 0
    p = pacing(rows, goal, today)
    if p.get("status") != "live":
        return {"status": "unavailable", "detail": p.get("detail")}
    base = {"conversion_rate": p["conversion_rate"], "aov": p["aov"], "sessions_per_day": p["sessions_per_day"]}
    new = {"conversion_rate": cr, "aov": aov, "sessions_per_day": sessions_per_day}
    ratio, levers = 1.0, {}
    for k, b in base.items():
        if new[k] is not None:
            if not b["value"]:
                return {"status": "unavailable", "detail": f"no base value for {k} to scale from; train it first"}
            ratio *= new[k] / b["value"]
            levers[k] = {"value": new[k], "source": "scenario", "note": f"was {b['value']:.4g} ({b['source']})"}
        else:
            levers[k] = b
    daily = p["run_rate_daily"] * ratio
    month_end = p["revenue_mtd"] + daily * p["days_left"]
    horizon = horizon_days or p["days_left"]
    remaining_gap = max(0.0, goal - p["revenue_mtd"])
    cr_v, spd_v = levers["conversion_rate"]["value"], levers["sessions_per_day"]["value"]
    return {
        "status": "scenario", "label": "SCENARIO - not an actual. Your live numbers are unchanged.",
        "levers": levers,
        "held_from_actuals": [k for k in base if new[k] is None and base[k]["source"] == "measured"],
        "assumed": [k for k in base if base[k]["source"] != "measured"],   # bases that are trained or a starting guess
        "waterfall": {"revenue_so_far": p["revenue_mtd"], "projected_rest_of_month": daily * p["days_left"],
                      "projected_month_end": month_end, "goal": goal, "surplus_or_gap": month_end - goal,
                      "projected_orders_per_day": (daily / levers["aov"]["value"]) if levers["aov"]["value"] else None,
                      "daily_revenue": daily, f"revenue_over_{horizon}_days": daily * horizon},
        "time_to_goal_days": (math.ceil(remaining_gap / daily) if daily > 0 else None) if remaining_gap else 0,
        "baseline_projection": p["projected_month_end"],
    }


def save_scenario(name, result):
    sc = _state("scenarios.json", {})
    sc[name] = {"saved": dt.datetime.now().isoformat(timespec="seconds"), **result}
    _save("scenarios.json", sc)


# =================================================================== brief
def money(v):
    return "unavailable" if v is None else f"${v:,.0f}"


def pctf(v, digits=1):
    return "unavailable" if v is None else f"{v * 100:.{digits}f}%"


def build(rows, goal, today, persist=False):
    """Everything, computed once. The brief, the bot and the dashboard all read this."""
    p = pacing(rows, goal, today)
    w = wow(rows, today)
    (cs, ce), _ = weeks(today)
    base = funnel(rows, cs - dt.timedelta(days=28), cs - dt.timedelta(days=1))
    leaks = leaks_in_dollars(w["funnel_now"], base)
    lever = biggest_lever(p)
    anom = anomalies(w)
    fp = fingerprint(anom)
    prev = _state("brief-state.json", {})
    calib, hist = calibrate(p, today)
    health = _state("health.json", None)
    snap = {
        "generated_for": str(today), "goal": goal, "pacing": p, "next_7_days": next_7_days(rows, today),
        "this_week": w["this_week"], "last_week": w["last_week"], "wow": w["rows"],
        "funnel": {"stages": w["funnel_now"]["stages"], "baseline_stages": base["stages"], "baseline_window": base["window"],
                   "conversion_rate": w["funnel_now"]["conversion_rate"], "aov": w["funnel_now"]["aov"],
                   "totals": {k: v["value"] for k, v in w["funnel_now"]["totals"].items()}},
        "leaks": leaks, "lever": lever, "anomalies": anom, "fingerprint": fp,
        "fresh_alarm": bool(fp and fp != prev.get("fingerprint")),
        "calibration": calib, "health": health, "crm": crm_week(rows, today),
        "daily": [{"date": str(d), **{k: rows[d].get(k) for k in ("revenue", "orders", "sessions")}} for d in sorted(rows)[-60:]],
        "scenarios": _state("scenarios.json", {}),
        "backlog": _backlog(leaks),
        "series": _state("wow-series.json", []),
    }
    snap["brief"] = compose(snap, prev)
    if persist:
        snap["series"] = wow_series(w, today)
        record_forecast(p, today, hist)
        _save("brief-state.json", {"fingerprint": fp, "headline": snap.get("headline_raw", ""), "posted": str(today)})
        _save("snapshot.json", snap)
    return snap


def _backlog(leaks):
    """The CRO backlog ranked by ICE x dollars leaking, so the brief can name the next test."""
    import cro
    return cro.ranked(cro._load(), {"leaks": leaks})


def compose(s, prev):
    p, lines = s["pacing"], [f"# Revenue brief - data through {s['pacing'].get('as_of', s['this_week'].split('..')[1])}", ""]
    # 1. the biggest move (and do not repeat yesterday's headline)
    movers = sorted([r for r in s["wow"] if r["status"] == "ok"], key=lambda r: -abs(r["pct"]))
    if movers:
        m = movers[0]
        fmt = (lambda v: pctf(v, 2)) if "rate" in m["label"].lower() else (money if m["label"] == "Revenue" else (lambda v: f"{v:,.0f}"))
        head = (f"**Biggest move:** {m['label']} {m['direction']} {abs(m['pct']) * 100:.0f}% week over week "
                f"({fmt(m['prior'])} to {fmt(m['current'])}).")
        s["headline_raw"] = head          # the REAL headline is what gets remembered, never the placeholder
        if head == prev.get("headline"):
            head = "**Biggest move:** nothing new since the last brief; the same move is still in place."
        lines.append(head)
    else:
        lines.append("**Biggest move:** nothing comparable this week (see unavailable below).")
    # 2. pacing
    lines.append("")
    if p.get("status") == "live":
        thumb = " 👍" if p["ahead"] else ""
        verdict = "ahead of pace" if p["ahead"] else "behind pace"
        lines.append(f"**Pacing:** {money(p['revenue_mtd'])} of {money(p['goal'])}, {verdict}{thumb}, "
                     f"{p['days_left']} days left. At the {p['run_rate_basis']} you land about "
                     f"{money(p['projected_month_end'])} ({money(abs(p['projected_vs_goal']))} "
                     f"{'over' if p['projected_vs_goal'] >= 0 else 'short'}; confidence {p['confidence']}).")
        if p["gap"] > 0 and p["needed_per_day"]:
            bits = []
            if p["orders_needed"] is not None:
                bits.append(f"{p['orders_needed']:,} orders at {money(p['aov']['value'])} AOV ({label(p['aov'])})")
            if p["sessions_needed"] is not None:
                bits.append(f"about {p['sessions_needed']:,} sessions at {pctf(p['conversion_rate']['value'], 2)} "
                            f"conversion ({label(p['conversion_rate'])})")
            lines.append(f"To hit goal: {money(p['needed_per_day'])}/day" + (f" - {'; '.join(bits)}." if bits else "."))
        c = s.get("calibration")
        if c:
            lines.append(f"Forecast check: on {c['made_on']} this projected {money(c['last_forecast'])}; now "
                         f"{money(c['now'])} ({'+' if c['drift'] >= 0 else '-'}{money(abs(c['drift']))}, {c['verdict']}).")
    else:
        lines.append(f"**Pacing:** unavailable - {p.get('detail')}.")
    # 3. the lever
    lines.append("")
    live_leaks = [l for l in s["leaks"] if l["status"] == "live" and l["dollars"] and l["dollars"] > 0]
    if live_leaks:
        l = live_leaks[0]
        lines.append(f"**The lever:** {l['stage']} slipped to {pctf(l['current_rate'])} from its 4-week baseline of "
                     f"{pctf(l['baseline_rate'])}. Back at baseline, last week makes about {money(l['dollars'])} more.")
        tests = [e for e in s.get("backlog", []) if e.get("stage") == l["stage"]]
        if tests:
            lines.append(f"Next test: **{tests[0]['name']}** (top of your backlog for this stage). Size it first: "
                         f"`cro.py size --baseline {l['baseline_rate']:.3f} --lift 0.10`.")
        else:
            lines.append(f"Your test backlog has nothing for {l['stage']} yet - pick from CRO-PLAYBOOK.md and add it with `cro.py add`.")
    else:
        measured_stages = [l for l in s["leaks"] if l["status"] == "live"]
        if measured_stages:
            lines.append("**The lever:** every measured funnel stage is at or above its own 4-week baseline.")
        else:
            lines.append("**The lever:** the funnel is not measurable yet (sessions, add to cart and checkout are missing "
                         "or below the minimum sample), so the leak cannot be found. Connect the funnel export.")
        if s["lever"]:
            lines.append(f"Size of the prize: a 10% lift on traffic, conversion or order value adds about "
                         f"{money(s['lever']['gain'])} by month-end ({s['lever']['basis']}).")
    # 4. flags, only when the situation is new
    if s["anomalies"] and s["fresh_alarm"]:
        lines += ["", "**Flags:**"] + [f"- {a['detail']}" for a in s["anomalies"]]
    elif s["anomalies"]:
        lines += ["", f"_{len(s['anomalies'])} flag(s) unchanged since the last brief - not repeated._"]
    # 5. the missing connection that matters most
    h = s.get("health")
    if h:
        bad = [r for r in h["rows"] if r["state"] in ("failed", "degraded")]
        if bad:
            lines += ["", "**Data:** " + "; ".join(f"{r['name']} {r['state']} - {r.get('fix') or r.get('detail')}" for r in bad)]
        nb = (h.get("completeness") or {}).get("next_best")
        if nb:
            lines += ["", f"**Next best connection:** {nb['name']} - {nb['why']}."]
    if s["crm"]:
        lines += ["", "**CRM (last 7 days):** " + "; ".join(s["crm"])]
    unavailable = [r for r in s["wow"] if r["status"] == "unavailable"]
    if unavailable:
        by_reason = {}
        for r in unavailable:
            by_reason.setdefault(r["detail"], []).append(r["label"])
        lines += ["", "**Unavailable (not zero):** " + "; ".join(f"{', '.join(v)} - {k}" for k, v in by_reason.items())]
    through = p.get("as_of") or s["this_week"].split("..")[1]
    lines += ["", f"_Measured from your data through {through}. Anything trained or estimated says so._"]
    return "\n".join(lines)


def label(r):
    """How a resolved value is named to a human."""
    return {"default": "starting guess, not your data", "trained": r.get("note", "trained")}.get(r["source"], r["source"])


def crm_week(rows, today):
    (cs, ce), _ = weeks(today)
    out = []
    for f, label, fmt in (("cash_collected", "cash collected (Stripe)", money), ("ad_spend", "ad spend", money),
                          ("new_opportunities", "new opportunities", lambda v: f"{v:,.0f}"),
                          ("deals_won", "deals won", lambda v: f"{v:,.0f}"), ("won_value", "won value", money)):
        if any(r.get(f) is not None for r in rows.values()):
            t = window_total(rows, f, cs, ce)
            out.append(f"{label} {fmt(t['value']) if t['value'] is not None else 'unavailable'}")
    rev, spend = window_total(rows, "revenue", cs, ce), window_total(rows, "ad_spend", cs, ce)
    if rev["value"] is not None and spend["value"]:
        out.append(f"revenue per ad dollar {rev['value'] / spend['value']:.2f}")
    return out


def write_dashboard(snap, out):
    import catalog
    html = open(os.path.join(HERE, "..", "dashboard", "template.html"), encoding="utf-8").read()
    data = json.dumps({**snap, "catalog": catalog.CATALOG}, default=str).replace("</", "<\\/")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html.replace("/*__SNAPSHOT__*/null", data))
    return out


# =================================================================== cli
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["train", "brief", "pacing", "funnel", "wow", "whatif", "scenarios", "snapshot", "dashboard"])
    ap.add_argument("--data", default="daily.csv")
    ap.add_argument("--goal", type=float)
    ap.add_argument("--today")
    ap.add_argument("--cr", type=float)
    ap.add_argument("--aov", type=float)
    ap.add_argument("--sessions-per-day", type=float)
    ap.add_argument("--horizon-days", type=int)
    ap.add_argument("--save")
    ap.add_argument("--by", default=os.environ.get("USER", "owner"))
    ap.add_argument("--out", default="dashboard.html")
    ap.add_argument("--post-state", action="store_true", help="record this brief as sent (dedupe + forecast history)")
    a = ap.parse_args(argv)
    if a.cmd == "train":
        print(json.dumps(train({"goal": a.goal, "aov": a.aov, "cr": a.cr, "sessions_per_day": a.sessions_per_day}, a.by), indent=2))
        return
    if a.cmd == "scenarios":
        print(json.dumps(_state("scenarios.json", {}), indent=2))
        return
    try:
        rows, cols = load(a.data)
    except FileNotFoundError:
        sys.exit(f"No data file at {a.data}. Run: python3 scripts/connectors.py sync   (or example/make_sample.py to try it)")
    except DataFormatError as e:
        sys.exit(f"{a.data}: {e}")
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    goal = a.goal or _state("config.json", {}).get("goal")
    if not goal and a.cmd not in ("funnel", "wow"):
        sys.exit("No goal yet. Run: python3 revops.py train --goal 50000")
    if a.cmd == "brief":
        print(build(rows, goal, today, persist=a.post_state)["brief"])
    elif a.cmd == "snapshot":
        print(json.dumps(build(rows, goal, today), indent=2, default=str))
    elif a.cmd == "dashboard":
        print("wrote", write_dashboard(build(rows, goal, today), a.out))
    elif a.cmd == "pacing":
        print(json.dumps(pacing(rows, goal, today), indent=2, default=str))
    elif a.cmd == "funnel":
        (cs, ce), _ = weeks(today)
        print(json.dumps(funnel(rows, cs, ce), indent=2, default=str))
    elif a.cmd == "wow":
        w = wow(rows, today)
        print(json.dumps({k: w[k] for k in ("this_week", "last_week", "rows")}, indent=2, default=str))
    else:
        r = whatif(rows, today, a.goal, a.cr, a.aov, a.sessions_per_day, a.horizon_days)
        if a.save and r.get("status") == "scenario":
            save_scenario(a.save, r)
            r["saved_as"] = a.save
        print(json.dumps(r, indent=2, default=str))


if __name__ == "__main__":
    main()
