#!/usr/bin/env python3
"""The interactive Slack bot. @mention it in a channel or DM it.

    pip install slack_bolt anthropic
    python3 slack/bot.py                   keep it running (a spare Mac, a small server, Railway, Render)

Socket Mode: no public URL, no web server to host. Needs SLACK_BOT_TOKEN (xoxb-...)
and SLACK_APP_TOKEN (xapp-..., connections:write). See slack/SLACK-SETUP.md.

Commands (anything else goes to the chat brain, if ANTHROPIC_API_KEY is set):
  brief                    today's brief
  pacing                   where the month lands
  leaks                    funnel stages ranked by dollars lost
  whatif cr=0.03 aov=90 sessions=1800 goal=60000 [save=name]
  scenarios                saved what-ifs side by side
  health                   every data source, with fixes
  explain <metric>         where a number comes from
  backlog                  CRO tests ranked by ICE x dollars leaking
  gaps                     what it could not answer, most frequent first
  refresh                  pull every source now and rebuild
"""
import datetime as dt, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import catalog, connectors, cro, gaps, revops  # noqa: E402
from post_brief import to_mrkdwn  # noqa: E402

connectors.load_env()
DATA = os.environ.get("REVOPS_DATA", "daily.csv")


def snapshot(refresh=False):
    if refresh:
        connectors.sync(DATA, int(os.environ.get("REVOPS_DAYS", "60")))
    rows, _ = revops.load(DATA)
    goal = revops._state("config.json", {}).get("goal")
    if not goal:
        raise RuntimeError("No goal yet. Run: python3 scripts/revops.py train --goal 50000")
    return rows, goal, revops.build(rows, goal, dt.date.today())


def money(v):
    return revops.money(v)


def handle(text, user):
    text = re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()
    cmd, _, rest = text.partition(" ")
    cmd = cmd.lower()
    try:
        if cmd in ("brief", "refresh", ""):
            return to_mrkdwn(snapshot(refresh=(cmd == "refresh"))[2]["brief"])
        rows, goal, s = snapshot()
        if cmd == "pacing":
            p = s["pacing"]
            if p.get("status") != "live":
                return f"Pacing is unavailable: {p.get('detail')}"
            return (f"*{money(p['revenue_mtd'])}* of {money(p['goal'])}, {'ahead of pace 👍' if p['ahead'] else 'behind pace'}, {p['days_left']} days left. "
                    f"Landing about *{money(p['projected_month_end'])}* ({p['run_rate_basis']}, confidence {p['confidence']}). "
                    f"Need {money(p['needed_per_day'])}/day.")
        if cmd == "leaks":
            def leak_line(l):
                if l["status"] != "live":
                    return f"- {l['stage']}: unavailable ({l.get('detail')})"
                rates = f"({revops.pctf(l['current_rate'])} vs baseline {revops.pctf(l['baseline_rate'])})"
                if l["dollars"] > 0:
                    return f"- {l['stage']}: *{money(l['dollars'])} lost* last week {rates}"
                return f"- {l['stage']}: {money(-l['dollars'])} ahead of baseline {rates}"
            lines = [leak_line(l) for l in s["leaks"]]
            return "*Funnel leaks, biggest first:*\n" + "\n".join(lines) if lines else "No funnel data to measure leaks yet."
        if cmd == "whatif":
            kv = dict(re.findall(r"(\w+)=([\w.]+)", rest))
            f = lambda k: float(kv[k]) if k in kv else None
            r = revops.whatif(rows, dt.date.today(), f("goal") or goal, f("cr"), f("aov"), f("sessions"))
            if r.get("status") != "scenario":
                return f"Can't run that what-if: {r.get('detail')}"
            if kv.get("save"):
                revops.save_scenario(kv["save"], r)
            w = r["waterfall"]
            lv = r["levers"]
            return (f"*SCENARIO, not an actual.* Conversion {revops.pctf(lv['conversion_rate']['value'], 2)} ({lv['conversion_rate']['source']}), "
                    f"AOV {money(lv['aov']['value'])} ({lv['aov']['source']}), {lv['sessions_per_day']['value']:,.0f} sessions/day ({lv['sessions_per_day']['source']}).\n"
                    f"Month-end *{money(w['projected_month_end'])}* vs live projection {money(r['baseline_projection'])}; "
                    f"{'surplus' if w['surplus_or_gap'] >= 0 else 'short'} {money(abs(w['surplus_or_gap']))}. "
                    f"Time to goal: {r['time_to_goal_days']} days." + (f" Saved as '{kv['save']}'." if kv.get("save") else ""))
        if cmd == "scenarios":
            sc = s["scenarios"]
            return "\n".join(f"- *{n}*: month-end {money(v['waterfall']['projected_month_end'])} (saved {v['saved'][:10]})" for n, v in sc.items()) or "No saved scenarios yet."
        if cmd == "health":
            h = connectors.probe_all()
            on = [r for r in h["rows"] if r["state"] != "off"]
            off = [r["name"] for r in h["rows"] if r["state"] == "off"]
            out = [h["summary"]] + [f"- {r['name']}: {r['state']}" + (f" - fix: {r['fix']}" if r.get("fix") and r["state"] != "connected" else "") for r in on]
            if off:
                nxt = next(r for r in h["rows"] if r["state"] == "off")
                out.append(f"Not connected: {', '.join(off)}. Next best: *{nxt['name']}* - {nxt['fix']}")
            return "\n".join(out)
        if cmd == "explain":
            e = catalog.explain(rest.strip().replace(" ", "_").lower())
            return json.dumps(e, indent=1) if "error" in e else f"*{e['label']}* ({e['kind']}: {e['kind_means']})\nSource: {e['source']}\nCalc: {e['calc']}\nBasis: {e['basis']}"
        if cmd == "backlog":
            items = cro.ranked(cro._load(), s)
            return "\n".join(f"{i + 1}. *{e['name']}* ({e['stage']}) score {e['score']}" for i, e in enumerate(items[:10])) or "Backlog is empty. Add tests with scripts/cro.py add."
        if cmd == "gaps":
            open_ = sorted((g for g in gaps.load().values() if g["status"] == "open"), key=lambda g: -g["count"])
            return "\n".join(f"- {g['subject']} (x{g['count']}): {g['missing']}" for g in open_[:10]) or "No open gaps."
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return "I only know the commands (try `brief`, `pacing`, `leaks`, `whatif cr=0.03`). Set ANTHROPIC_API_KEY to ask me anything."
        import ask
        return to_mrkdwn(ask.ask(text, s, user)["answer"])
    except Exception as e:   # the bot never crashes on a bad question; it says what broke
        return f"Something broke answering that: {e}"


def main():
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    @app.event("app_mention")
    def on_mention(event, say):
        say(text=handle(event.get("text"), event.get("user")), thread_ts=event.get("thread_ts") or event.get("ts"))

    @app.event("message")
    def on_dm(event, say):
        if event.get("channel_type") == "im" and not event.get("bot_id") and not event.get("subtype"):
            say(text=handle(event.get("text"), event.get("user")))

    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
