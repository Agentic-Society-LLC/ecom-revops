# Put your revenue analyst in Slack (about 10 minutes)

You get two things:

- **The daily brief** posted to a channel every morning, and a DM to you when a data source
  breaks (once per outage, with the fix).
- **A bot you can talk to:** @mention it in a channel or DM it. `brief`, `pacing`, `leaks`,
  `whatif cr=0.03`, `health`, `explain aov`, `backlog`, `gaps`, `refresh`, or any question in
  plain English (that part needs an Anthropic API key).

You create the app and every token yourself. Never paste a token into a chat with an AI.

## 1. Create the app from the manifest

1. Go to **api.slack.com/apps** and click **Create New App**.
2. Choose **From a manifest**, pick your workspace, click **Next**.
3. Choose the **YAML** tab, delete what is there, paste the contents of `slack/manifest.yml`
   from this repo, click **Next**, then **Create**.

## 2. Install it and copy the bot token

1. In the app's left menu, open **OAuth & Permissions**.
2. Click **Install to Workspace**, then **Allow**.
3. Copy the **Bot User OAuth Token** (starts with `xoxb-`). Put it in your `.env` as
   `SLACK_BOT_TOKEN=`.

## 3. Create the app-level token (for the chat bot)

The bot uses Socket Mode, so you do not need a website or a public URL.

1. Left menu: **Basic Information**, scroll to **App-Level Tokens**, click
   **Generate Token and Scopes**.
2. Name it `socket`, click **Add Scope**, choose `connections:write`, click **Generate**.
3. Copy the token (starts with `xapp-`) into `.env` as `SLACK_APP_TOKEN=`.

## 4. Pick the channel and tell it who you are

1. In Slack, create or open the channel for the brief (for example `#revenue`).
2. Type `/invite @RevOps` in that channel.
3. Channel name at the top > scroll to the bottom of the About tab > copy the **Channel ID**
   (starts with `C`). Put it in `.env` as `SLACK_CHANNEL=`.
4. Your own member ID (for outage DMs): click your profile picture > **Profile** > the three
   dots > **Copy member ID**. Put it in `.env` as `SLACK_OWNER_ID=`.

## 5. Test the daily brief

```bash
python3 slack/post_brief.py
```

It pulls every connected source, builds the brief and the dashboard, and posts to the channel.
If something is missing it says exactly which variable.

## 6. Start the chat bot

```bash
pip install slack_bolt anthropic
python3 slack/bot.py
```

Leave it running. Then in Slack: `@RevOps pacing`, or DM it `leaks`.

To ask it anything in plain English, add `ANTHROPIC_API_KEY=` to `.env` (create the key at
console.anthropic.com > API Keys). Simple lookups go to Claude Haiku (cheap), real analysis to
Claude Opus, and the rules and metric catalog are prompt-cached so repeat questions cost less.

**Where to keep it running:** a Mac that stays on (run it in a terminal, or as a launchd
service), or a small always-on host such as Railway, Render or Fly.io. It needs your `.env`
values as environment variables there.

## 7. Schedule the morning brief

Pick one:

- **Your Mac:** `crontab -e` and add (8:00 AM in your Mac's timezone):
  `0 8 * * * cd /path/to/ecom-revops && /usr/bin/python3 slack/post_brief.py >> .revops/cron.log 2>&1`
- **GitHub Actions** (runs even when your computer is off): copy
  `deploy/github-actions-daily-brief.yml` into your own **private** copy of this repo under
  `.github/workflows/`, and add your `.env` values as repository secrets. See the comments in
  that file.

## If something does not work

- `not_in_channel`: you skipped `/invite @RevOps` in step 4.
- `invalid_auth`: the `xoxb-` token was copied wrong or the app was reinstalled (copy it again).
- The bot does not answer: is `python3 slack/bot.py` still running, and is the `xapp-` token set?
- `missing_scope`: reinstall the app (OAuth & Permissions > Reinstall) after changing the manifest.
