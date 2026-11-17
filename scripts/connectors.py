#!/usr/bin/env python3
"""Connector plugins: pull live data into daily.csv, with health probes and self-healing.
Standard library only.

    python3 connectors.py sync   [--days 60] [--out daily.csv]   pull every configured source
    python3 connectors.py health                                  probe every source, no pull

Every connector turns itself on only when its environment variables are set (in your
shell or a .env file next to where you run it). A day a source did not cover stays
BLANK, which the engine reads as unavailable - never as 0.

  shopify      orders + net revenue   SHOPIFY_STORE, SHOPIFY_TOKEN, SHOPIFY_API_VERSION
  funnel       sessions, cart, checkout   FUNNEL_CSV = path or URL of Shopify Analytics >
               "Conversion over time" by day (or a GA4 export / published Google Sheet)
  stripe       cash collected          STRIPE_KEY (restricted, read-only)
  meta_ads     ad spend                META_TOKEN, META_AD_ACCOUNT (digits only), META_API_VERSION
  gohighlevel  leads + deals won       GHL_TOKEN (private integration token), GHL_LOCATION_ID
  csv_url      ANY other source        EXTRA_CSV_URL = a CSV with a date column and any of the
               engine's column names (a published Google Sheet, a Zapier or Make export,
               a warehouse query). This is the "extract anything" connector.

ADD A CONNECTOR: subclass Connector, set name/env/setup_hint, implement probe() and
pull(), optionally heal(), and add it to REGISTRY. That is the whole contract (see
health.py for the rules it must follow).
"""
import argparse, csv, datetime as dt, io, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import health, jsonio  # noqa: E402
from health import ConnectorError  # noqa: E402


def http_get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        raise ConnectorError(f"{url.split('?')[0]} -> HTTP {e.code}", http_status=e.code)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ConnectorError(f"{url.split('?')[0]} -> {e}", network_error=str(e))


def http_json(url, headers=None):
    body, headers = http_get(url, headers)
    try:
        return json.loads(body or b"{}"), headers
    except ValueError:
        raise ConnectorError(f"{url.split('?')[0]} -> not JSON", http_status=502)


def local_day(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()


def empty_days(start, end, cols):
    return {start + dt.timedelta(i): {c: 0.0 for c in cols} for i in range((end - start).days + 1)}


class Connector:
    name, env, setup_hint = "", (), ""

    def configured(self):
        return all(os.environ.get(k) for k in self.env)

    def probe(self):
        if not self.configured():
            return {"ok": False, "missing_env": [k for k in self.env if not os.environ.get(k)]}
        try:
            self._probe()
            return {"ok": True}
        except ConnectorError as e:
            return {"ok": False, "detail": str(e), "http_status": e.http_status, "network_error": e.network_error}

    def heal(self, fail_class, attempt):
        return None   # alert-only by default; retries with backoff are done by health.run_with_heal


# ------------------------------------------------------------------ Shopify
class Shopify(Connector):
    name, env = "shopify", ("SHOPIFY_STORE", "SHOPIFY_TOKEN", "SHOPIFY_API_VERSION")
    setup_hint = ("Shopify admin > Settings > Apps > Develop apps > create app > Admin API scope "
                  "read_orders > install > copy the Admin API access token. Set SHOPIFY_API_VERSION "
                  "to a current version from shopify.dev/docs/api/usage/versioning.")

    def _base(self):
        return f"https://{os.environ['SHOPIFY_STORE']}/admin/api/{os.environ['SHOPIFY_API_VERSION']}"

    def _h(self):
        return {"X-Shopify-Access-Token": os.environ["SHOPIFY_TOKEN"]}

    def _probe(self):
        http_json(self._base() + "/shop.json", self._h())

    def pull(self, start, end):
        q = urllib.parse.urlencode({"status": "any", "limit": 250, "created_at_min": f"{start}T00:00:00",
                                    "created_at_max": f"{end}T23:59:59",
                                    "fields": "created_at,current_total_price,total_price,cancelled_at,test"})
        url, days = f"{self._base()}/orders.json?{q}", empty_days(start, end, ("orders", "revenue"))
        while url:
            body, headers = http_json(url, self._h())
            for o in body.get("orders", []):
                if o.get("test") or o.get("cancelled_at"):
                    continue
                d = local_day(o["created_at"])
                if d in days:
                    days[d]["orders"] += 1
                    days[d]["revenue"] += float(o.get("current_total_price") or o.get("total_price") or 0)
            link = headers.get("Link") or headers.get("link") or ""
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            url = m.group(1) if m else None
        return days


# ------------------------------------------------------------------ funnel export
class FunnelCSV(Connector):
    name, env = "funnel", ("FUNNEL_CSV",)
    setup_hint = ("Shopify admin > Analytics > Reports > 'Conversion over time', group by day, "
                  "export CSV, set FUNNEL_CSV to its path. Or publish a Google Sheet as CSV and use its URL.")
    cols = ("sessions", "added_to_cart", "reached_checkout")

    def _read(self):
        src = os.environ["FUNNEL_CSV"]
        if src.startswith("http"):
            return http_get(src)[0].decode("utf-8-sig")
        if not os.path.exists(src):
            raise ConnectorError(f"{src} not found", http_status=404,
                                 fix="FUNNEL_CSV points at a file that is not there: export it again or fix the path in .env")
        return jsonio.text(src)

    def _probe(self):
        self._read()

    def pull(self, start, end):
        import revops
        rows, _ = revops.load_text(self._read())
        return {d: {k: v[k] for k in self.cols if v.get(k) is not None}
                for d, v in rows.items() if start <= d <= end}


class CsvUrl(FunnelCSV):
    name, env = "csv_url", ("EXTRA_CSV_URL",)
    setup_hint = "Any CSV with a date column (published Google Sheet, Zapier, Make, a warehouse export)."

    def _read(self):
        return http_get(os.environ["EXTRA_CSV_URL"])[0].decode("utf-8-sig")

    def pull(self, start, end):
        import revops
        rows, _ = revops.load_text(self._read())
        return {d: {k: v for k, v in vals.items() if v is not None} for d, vals in rows.items() if start <= d <= end}


# ------------------------------------------------------------------ Stripe
class Stripe(Connector):
    name, env = "stripe", ("STRIPE_KEY",)
    setup_hint = ("Stripe dashboard > Developers > API keys > Create restricted key with Read on "
                  "Balance transactions only. Never paste it into chat.")

    def _h(self):
        return {"Authorization": f"Bearer {os.environ['STRIPE_KEY']}"}

    def _probe(self):
        http_json("https://api.stripe.com/v1/balance_transactions?limit=1", self._h())

    def pull(self, start, end):
        gte = int(dt.datetime.combine(start, dt.time()).timestamp())
        lte = int(dt.datetime.combine(end, dt.time(23, 59, 59)).timestamp())
        days, after = empty_days(start, end, ("cash_collected",)), None
        while True:
            q = {"created[gte]": gte, "created[lte]": lte, "limit": 100}
            if after:
                q["starting_after"] = after
            body, _ = http_json("https://api.stripe.com/v1/balance_transactions?" + urllib.parse.urlencode(q), self._h())
            for t in body.get("data", []):
                if t.get("type") in ("charge", "payment"):
                    d = dt.datetime.fromtimestamp(t["created"]).date()
                    if d in days:
                        days[d]["cash_collected"] += t["amount"] / 100.0
            if not body.get("has_more") or not body.get("data"):
                return days
            after = body["data"][-1]["id"]


# ------------------------------------------------------------------ Meta Ads
class MetaAds(Connector):
    name, env = "meta_ads", ("META_TOKEN", "META_AD_ACCOUNT", "META_API_VERSION")
    setup_hint = ("Meta Business Settings > System users > generate a token with ads_read for the ad "
                  "account. META_AD_ACCOUNT is the number only. Set META_API_VERSION to a current Graph API version.")

    def _url(self, path, **q):
        q["access_token"] = os.environ["META_TOKEN"]
        return f"https://graph.facebook.com/{os.environ['META_API_VERSION']}/act_{os.environ['META_AD_ACCOUNT']}{path}?" + urllib.parse.urlencode(q)

    def _probe(self):
        http_json(self._url("", fields="name"))

    def pull(self, start, end):
        days = empty_days(start, end, ("ad_spend",))
        url = self._url("/insights", fields="spend", time_increment=1, level="account",
                        time_range=json.dumps({"since": str(start), "until": str(end)}))
        while url:
            body, _ = http_json(url)
            for r in body.get("data", []):
                d = dt.date.fromisoformat(r["date_start"])
                if d in days:
                    days[d]["ad_spend"] = float(r.get("spend") or 0)
            url = (body.get("paging") or {}).get("next")
        return days


# ------------------------------------------------------------------ GoHighLevel
GHL_BASE = "https://services.leadconnectorhq.com"


class GoHighLevel(Connector):
    name, env = "gohighlevel", ("GHL_TOKEN", "GHL_LOCATION_ID")
    setup_hint = ("GoHighLevel > Settings > Private Integrations > create one with opportunities.readonly "
                  "and contacts.readonly > copy the token. GHL_LOCATION_ID is in Settings > Business Profile.")

    def _h(self):
        return {"Authorization": f"Bearer {os.environ['GHL_TOKEN']}", "Version": "2021-07-28",
                "Accept": "application/json"}

    def _probe(self):
        http_json(f"{GHL_BASE}/opportunities/pipelines?locationId={os.environ['GHL_LOCATION_ID']}", self._h())

    def pull(self, start, end):
        loc = os.environ["GHL_LOCATION_ID"]
        days = empty_days(start, end, ("new_opportunities", "deals_won", "won_value"))
        url = f"{GHL_BASE}/opportunities/search?location_id={loc}&limit=100"
        max_pages = int(os.environ.get("GHL_MAX_PAGES", "200"))   # read at call time so .env applies; 200 x 100 = 20,000
        for page in range(max_pages + 1):
            if page == max_pages:   # a partial count dressed as complete would be a fabricated number
                raise ConnectorError(f"more than {max_pages * 100:,} opportunities; counts would be partial",
                                     bad_data=True, fix="the account is larger than the connector reads in one pass; "
                                     "raise GHL_MAX_PAGES in .env or filter the pipeline")
            body, _ = http_json(url, self._h())
            batch = body.get("opportunities", [])
            for o in batch:
                if o.get("createdAt"):
                    d = local_day(o["createdAt"])
                    if d in days:
                        days[d]["new_opportunities"] += 1
                if o.get("status") == "won" and o.get("lastStatusChangeAt"):
                    d = local_day(o["lastStatusChangeAt"])
                    if d in days:
                        days[d]["deals_won"] += 1
                        days[d]["won_value"] += float(o.get("monetaryValue") or 0)
            meta = body.get("meta") or {}
            if len(batch) < 100 or not meta.get("startAfter"):
                break
            url = (f"{GHL_BASE}/opportunities/search?location_id={loc}&limit=100"
                   f"&startAfter={meta['startAfter']}&startAfterId={meta['startAfterId']}")
        return days

    def open_deals(self, top=5):
        """Late-stage open opportunities, biggest first - the 'deals to work' list."""
        loc = os.environ["GHL_LOCATION_ID"]
        pipes, _ = http_json(f"{GHL_BASE}/opportunities/pipelines?locationId={loc}", self._h())
        stage_names = {s["id"]: s["name"] for p in pipes.get("pipelines", []) for s in p.get("stages", [])}
        body, _ = http_json(f"{GHL_BASE}/opportunities/search?location_id={loc}&limit=100", self._h())
        deals = [{"name": o.get("name"), "value": float(o.get("monetaryValue") or 0),
                  "stage": stage_names.get(o.get("pipelineStageId"), "unknown"),
                  "last_change": o.get("lastStageChangeAt") or o.get("updatedAt")}
                 for o in body.get("opportunities", []) if (o.get("status") or "open") == "open"]
        return sorted(deals, key=lambda d: -d["value"])[:top]


REGISTRY = [Shopify(), FunnelCSV(), Stripe(), MetaAds(), GoHighLevel(), CsvUrl()]


# ------------------------------------------------------------------ sync
def load_env(path=".env"):
    if os.path.exists(path):
        for line in jsonio.text(path).splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = re.split(r"\s+#", v, maxsplit=1)[0].strip().strip('"').strip("'")   # drop inline comments
                if v:
                    os.environ.setdefault(k.strip(), v)


def sync(out="daily.csv", days_back=60, sleep=time.sleep):
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days_back - 1)
    merged = {start + dt.timedelta(i): {} for i in range(days_back)}
    rows = []
    for c in REGISTRY:
        r = health.run_with_heal(c.name, c, start, end, sleep=sleep)
        if r.get("data"):
            for d, vals in r["data"].items():
                if d in merged:
                    merged[d].update(vals)
        r.pop("data", None)
        rows.append(r)
    cols = ["date"] + sorted({k for v in merged.values() for k in v})
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for d in sorted(merged):
            w.writerow([d.isoformat()] + ["" if merged[d].get(c) is None else round(merged[d][c], 2) for c in cols[1:]])
    present = {r["name"] for r in rows if r.get("source")}
    status = {"as_of": dt.datetime.now().isoformat(timespec="seconds"), "rows": rows,
              "counts": health.counts(rows), "summary": health.summary_text(health.counts(rows)),
              "completeness": health.setup_completeness(present)}
    jsonio.write(os.path.join(health.STATE_DIR, "health.json"), status, indent=2)
    return status


def probe_all():
    rows = []
    for c in REGISTRY:
        p = c.probe()
        if p.get("missing_env") and not any(os.environ.get(k) for k in c.env):
            rows.append({"name": c.name, "state": "off", "detail": "not configured", "fix": c.setup_hint})
            continue
        fc = None if p["ok"] else health.classify_failure(p.get("http_status"), p.get("network_error"), p.get("missing_env"))
        rows.append({"name": c.name, "state": health.state_for_class(fc), "fail_class": fc,
                     "detail": p.get("detail") or ("missing " + ", ".join(p["missing_env"]) if p.get("missing_env") else "ok"),
                     "fix": health.FIX_STEPS.get(fc) if fc else None})
    return {"rows": rows, "summary": health.summary_text(health.counts(rows))}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["sync", "health", "deals"])
    ap.add_argument("--out", default="daily.csv")
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args()
    load_env()
    if a.cmd == "sync":
        s = sync(a.out, a.days)
        for r in s["rows"]:
            if r["state"] == "off":
                print(f"{r['name']:12} off")
            else:
                print(f"{r['name']:12} {r['state']:10} {r.get('detail', '')}" + (f"\n{'':12} FIX: {r['fix']}" if r["state"] != "connected" and r.get("fix") else ""))
        print(s["summary"])
        nb = s["completeness"]["next_best"]
        if nb:
            hint = next((c.setup_hint for c in REGISTRY if c.name == nb["name"]), "")
            print(f"Next best connection: {nb['name']} - {nb['why']}.\n  How: {hint}")
        print(f"wrote {a.out}")
    elif a.cmd == "health":
        print(json.dumps(probe_all(), indent=2))
    else:
        g = GoHighLevel()
        if not g.configured():
            sys.exit("deals needs the GoHighLevel connector (GHL_TOKEN, GHL_LOCATION_ID)")
        print(json.dumps(g.open_deals(), indent=2))


if __name__ == "__main__":
    main()
