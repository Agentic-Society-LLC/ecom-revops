# ecom-revops

**Your AI RevOps and CRO analyst for e-commerce.** From Agentic Society: the revenue agent we
run on our own business, rebuilt for online stores and handed over with the engine, not just
the instructions.

Every morning it tells you **where the month lands, which part of your funnel is leaking, what
that leak costs in dollars, and the one thing to fix first**. Then it helps you test the fix
properly.

- **Pacing to goal:** month to date, where you land, ahead or behind, what you need per day in
  orders and sessions.
- **Leaks in dollars:** product page to cart, cart to checkout, checkout to order, each priced
  against its own 4-week baseline.
- **What-if:** move conversion, AOV, traffic, goal or horizon and see month-end and time to goal.
  Save and compare scenarios. Never touches your real numbers.
- **CRO that does not fool you:** sizes every A/B test before it starts, refuses to call a winner
  early, and ranks your test backlog by the money each stage is leaking.
- **Self-healing data:** Shopify, Stripe, Meta Ads, GoHighLevel, or any CSV. A broken source is
  retried when that can help, falls back to its last good data (labelled stale), and pings you
  once with the exact fix.
- **Slack:** a daily brief in your channel and a bot you can ask anything.
- **A dashboard:** one HTML file, no server.
- **It never invents a number.** Blank is "unavailable", never 0. Every rate says if it is
  measured, trained, an estimate or a scenario. Chat answers are checked number by number
  against your data.

Standard-library Python 3.8+. No paid services required. The chat brain and the Slack bot are
optional extras (`pip install -r requirements.txt`).

## Install

**Claude Code / Codex / Cursor (recommended - it runs the code for you)**
```bash
git clone https://github.com/Agentic-Society-LLC/ecom-revops.git && cd ecom-revops
```
Then open the folder in your AI tool and say **"set up my revenue analyst"**. It reads
`SKILL.md` and walks you through it. For Claude Code, also make it a skill you can call from
anywhere:
```bash
mkdir -p ~/.claude/skills && ln -s "$PWD" ~/.claude/skills/ecom-revops
```

**Claude.ai / Cowork:** download this repo as a zip (Code > Download ZIP) and upload it in
Customize > Skills. Chat-only tools cannot run the code, so it will ask you to paste the output
of the snapshot command from a computer that can.

## Try it in two minutes (fake data, no keys)
```bash
python3 example/make_sample.py --out daily.csv
python3 scripts/revops.py train --goal 120000
python3 scripts/revops.py brief
python3 scripts/revops.py dashboard && open dashboard.html
```
The sample has a checkout problem planted in the last week. The brief finds it and prices it.

## Connect your store
```bash
cp .env.example .env      # fill in only the sources you use
python3 scripts/connectors.py sync
```
Each source's exact setup clicks print when you run it. Every key is one you create yourself,
read-only, in the source's own settings.

| Source | Gives you | Needs |
|---|---|---|
| Shopify | orders, revenue, AOV | an app token with `read_orders` |
| Funnel export | sessions, add to cart, checkout | Shopify Analytics "Conversion over time" by day, or GA4, or a sheet |
| Stripe | cash actually collected | a restricted read-only key |
| Meta Ads | spend, revenue per ad dollar | a system-user token with `ads_read` |
| GoHighLevel | leads, deals won, deals to chase | a private integration token |
| Any CSV URL | anything else | a CSV with a date column |

Add your own: subclass `Connector` in `scripts/connectors.py` (a probe, a pull, optionally a
heal) and add it to `REGISTRY`.

## Slack

Follow **[slack/SLACK-SETUP.md](slack/SLACK-SETUP.md)** - create the app from the included
manifest, copy two tokens, invite the bot. About 10 minutes.

## Everything it can do

| Command | |
|---|---|
| `python3 scripts/revops.py brief` | the daily brief |
| `python3 scripts/revops.py pacing` / `funnel` / `wow` | the pieces, as JSON |
| `python3 scripts/revops.py whatif --cr 0.03 --save "fix checkout"` | a scenario |
| `python3 scripts/revops.py dashboard` | the dashboard |
| `python3 scripts/cro.py size` / `check` / `add` / `backlog` / `result` | CRO testing |
| `python3 scripts/catalog.py explain aov` | where any number comes from |
| `python3 scripts/gaps.py list` | what it could not answer |
| `python3 scripts/connectors.py sync` / `health` / `deals` | data sources |
| `python3 slack/post_brief.py` / `slack/bot.py` | Slack |

`MAGIC-SAUCE.md` maps every behaviour to its code and its test. `CRO-PLAYBOOK.md` has the test
ideas for each leaking stage.

## Tests
```bash
python3 -m unittest discover tests
```

## Privacy

Your data stays on your machine (or your own private GitHub repo if you schedule it there).
Nothing is sent anywhere except the sources you connect, Slack if you set it up, and
Anthropic's API if you turn on the chat brain. `.env` and `.revops/` are git-ignored.

MIT licensed. Built by Agentic Society.
