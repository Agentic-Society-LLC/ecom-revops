# What came over from the Agentic Society RevOps agent, and where it lives

This kit is a port of the RevOps agent Agentic Society runs on its own business, moved from
high-ticket (leads, calls, deals) to e-commerce (sessions, carts, orders) and stripped of our
own data, systems and wiring. Every behaviour below is real code here, not a description.

| RevOps capability | What it does | Where it lives here | Proved by |
|---|---|---|---|
| Three-state numbers | live (0 is real) / unavailable / not loaded - never a fabricated 0 | `revops._num`, `window_total` | `test_blank_is_unavailable_and_zero_is_real` |
| Pacing to the monthly goal | month to date vs goal, projection, ahead or behind, needed per day | `revops.pacing` | dashboard Revenue tab |
| Orders / sessions to goal | the e-commerce version of "appointments to goal" | `revops.pacing` | brief line "To hit goal" |
| Measured vs trained precedence | scenario > measured > trained (who, when) > default, always labelled | `revops.resolve`, `revops.train` | `test_precedence_scenario_measured_trained_default` |
| Training with provenance and limits | per-field who and when; out-of-range values refused by name | `revops.train` | `test_training_refuses_out_of_range` |
| Honest rate measurement | cohort-only denominators, minimum sample, unavailable below it | `revops.rate` | `test_rate_needs_a_sample` |
| Week over week contract | real windows; zero baseline = no percent; missing side = unknown, not flat | `revops.compare`, `wow` | `test_zero_baseline_has_no_percent`, `test_missing_side_is_unknown_not_flat` |
| Retained weekly series | up to 8 weeks for the trend view, never the source of a delta | `revops.wow_series` | dashboard Week over week tab |
| Biggest move first | the brief opens on what changed most | `revops.compose` | the brief |
| Anomaly flags | funnel rates beyond 15%, volume swings beyond 30%, stalled stages | `revops.anomalies`, `stalled_stages` | `test_stall_is_flagged` |
| Quiet by default (dedupe) | the anomaly situation is fingerprinted; an unchanged situation does not re-alarm | `revops.fingerprint`, `brief-state.json` | `test_same_situation_does_not_re_alarm` |
| Predictive pacing with confidence | one projection, confidence from data depth and measured inputs | `revops.pacing` | brief "confidence" |
| Forecast self-calibration | compares today's projection with the last one this month | `revops.calibrate` | brief "Forecast check" |
| Biggest leak / constraint | each stage priced in dollars against its own baseline | `revops.leaks_in_dollars` | `test_leak_in_dollars` |
| Size of the prize | what a 10% lift on a lever is worth by month-end | `revops.biggest_lever` | brief "The lever" |
| Next 7 days estimate | labelled estimate from the last 7 days | `revops.next_7_days` | dashboard |
| What-if with goal and horizon | levers, waterfall, time to goal, never touches actuals | `revops.whatif` | `test_whatif_with_no_overrides_reproduces_live`, `test_scenario_never_changes_actuals` |
| Save and compare scenarios | named scenarios from the CLI, bot and dashboard | `revops.save_scenario`, dashboard What-if tab | dashboard |
| Connector contract | every source: probe, pull, optional heal; unregistered = visible, not silent | `connectors.Connector`, `REGISTRY` | `connectors.py health` |
| Failure classification | network / server / rate-limit / stale vs auth / not-found / missing-env / rejected | `health.classify_failure` | `test_classification` |
| Self-healing | transient failures retried with backoff (max 3); intervention failures never retried | `health.run_with_heal` | `test_transient_failure_heals`, `test_auth_failure_does_not_retry` |
| Last-good fallback | a down source serves its last good pull, labelled stale with its as-of | `health.save_last_good`, `load_last_good` | `test_down_source_serves_last_good_labelled_stale` |
| One alert per outage | per source, per class, per day | `health.should_alert` | `test_one_alert_per_outage_per_day` |
| Honest connection summary | counts every source; never "all live" while a row is not | `health.summary_text` | `test_summary_never_says_all_live_when_one_is_not` |
| Highest-value missing connection | setup completeness and the next best source, every brief | `health.setup_completeness` | sync output, brief |
| Extract-anything connector | any CSV URL (Google Sheet, Zapier, Make, warehouse) | `connectors.CsvUrl` | `.env.example` |
| Deals to work | late-stage open CRM deals, biggest first | `connectors.GoHighLevel.open_deals` | `connectors.py deals` |
| Metric catalog / dictionary | every number's source, calc, basis and kind | `catalog.py` | `test_every_snapshot_metric_is_in_the_catalog`, dashboard tab |
| Grounded chat brain | answers only from the snapshot; untraceable numbers flagged and logged | `ask.py`, `grounding.py` | `test_invented_number_is_caught` |
| Model routing + prompt caching | Haiku for lookups, Opus for analysis; cached stable prefix | `ask.route`, `ask.ask` | `cache_read_tokens` in the result |
| Conversation memory | last few Q&A per person, context only; the snapshot wins | `ask.recall`, `remember` | `.revops/memory.json` |
| Never claim an action | the chat cannot act and is told so; the bot only runs listed commands | `ask.system_prompt`, `slack/bot.py` | - |
| Capability-gap log | can't-answer, source-down and ungrounded answers, deduped with counts | `gaps.py` | `test_gap_dedupes` |
| Daily digest to Slack | scheduled brief + outage DM to the owner | `slack/post_brief.py` | `slack/SLACK-SETUP.md` |
| Summon in Slack | @mention or DM with commands or plain English | `slack/bot.py` | `slack/SLACK-SETUP.md` |
| The dashboard | Revenue, Funnel and leaks, Week over week, What-if, Data sources, Metric dictionary | `dashboard/template.html` | `revops.py dashboard` |

## New for CRO (not in the original)

| Capability | Where |
|---|---|
| Test sizing (95% confidence, 80% power) | `cro.py size` |
| No-peeking significance check | `cro.py check --planned` |
| Backlog ranked by ICE x dollars leaking | `cro.py backlog` |
| Stage-by-stage test ideas | `CRO-PLAYBOOK.md` |
| Revenue per ad dollar | `revops.crm_week` (with Meta Ads connected) |

## Deliberately not included

Our own infrastructure: the hosted dashboard app, the multi-agent fleet and its handoffs, our
ticket boards and review gate, and our own data. Those are how we run it; this is what it does.
