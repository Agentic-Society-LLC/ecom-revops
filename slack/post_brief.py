#!/usr/bin/env python3
"""The scheduled job: pull every source, build the snapshot, post the daily brief to
Slack, and alert the owner ONCE per outage. Standard library only.

    python3 slack/post_brief.py            run it from the folder that holds your .env

Environment: SLACK_BOT_TOKEN, SLACK_CHANNEL (channel id, e.g. C0123...), SLACK_OWNER_ID
(your Slack member id, for outage DMs), plus the connector variables. Schedule it with
cron, launchd, or the GitHub Actions workflow in .github/workflows/daily-brief.yml.

What it never does: post a fabricated number, re-alarm an unchanged situation, or DM
you twice about the same outage on the same day.
"""
import datetime as dt, json, os, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import connectors, revops  # noqa: E402


def slack(method, payload):
    req = urllib.request.Request(f"https://slack.com/api/{method}", data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
                                          "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read())
    if not body.get("ok"):
        raise RuntimeError(f"Slack {method} failed: {body.get('error')}")
    return body


def to_mrkdwn(md):
    """Markdown -> Slack mrkdwn: **bold** -> *bold*, headings -> bold lines."""
    out = []
    for line in md.splitlines():
        if line.startswith("# "):
            line = "*" + line[2:] + "*"
        out.append(line.replace("**", "*"))
    return "\n".join(out)


def main():
    connectors.load_env()
    for k in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL"):
        if not os.environ.get(k):
            sys.exit(f"missing {k} - see slack/SLACK-SETUP.md")
    data = os.environ.get("REVOPS_DATA", "daily.csv")
    status = connectors.sync(data, int(os.environ.get("REVOPS_DAYS", "60")))
    rows, _ = revops.load(data)
    goal = revops._state("config.json", {}).get("goal")
    if not goal:
        sys.exit("No goal yet. Run: python3 scripts/revops.py train --goal 50000")
    snap = revops.build(rows, goal, dt.date.today(), persist=True)
    revops.write_dashboard(snap, os.environ.get("REVOPS_DASHBOARD", "dashboard.html"))
    slack("chat.postMessage", {"channel": os.environ["SLACK_CHANNEL"], "text": to_mrkdwn(snap["brief"]),
                               "unfurl_links": False})
    owner = os.environ.get("SLACK_OWNER_ID")
    alerts = [r for r in status["rows"] if r.get("alert")]
    if owner and alerts:
        dm = slack("conversations.open", {"users": owner})["channel"]["id"]
        lines = [f"<@{owner}> a data source needs you:"] + [
            f"- *{r['name']}* is {r['state']} ({r['fail_class']}). Fix: {r['fix']}" +
            (" Serving last good data meanwhile." if r.get("source") == "last-good" else " No data from it until fixed.")
            for r in alerts]
        slack("chat.postMessage", {"channel": dm, "text": "\n".join(lines)})
    print("posted brief" + (f", alerted on {len(alerts)} source(s)" if alerts else ""))


if __name__ == "__main__":
    main()
