#!/usr/bin/env python3
"""The metric catalog: every number this agent can show, with its source, how it is
calculated, its basis and what KIND of number it is. The agent answers "where does
this number come from?" from here, never from a guess.

    python3 catalog.py                 the whole dictionary
    python3 catalog.py explain aov     one metric

RULE: any change that adds, removes, renames or re-sources a metric updates this file
in the same change. A number with no catalog entry is a provenance bug.
"""
import json, sys

KINDS = {
    "measured": "read directly from a connected source",
    "derived": "calculated from measured numbers (the formula is shown)",
    "trained": "a number you told it, stamped with who and when",
    "estimate": "a projection from recent data, labelled as such",
    "scenario": "a what-if override; never mixed into actuals",
}

CATALOG = {
    "revenue": {"label": "Revenue", "kind": "measured", "source": "Shopify orders (net of cancelled and test orders), or your export",
                "calc": "sum of order totals per day", "basis": "order created date, your store's timezone"},
    "orders": {"label": "Orders", "kind": "measured", "source": "Shopify orders or export", "calc": "count of non-test, non-cancelled orders",
               "basis": "order created date"},
    "sessions": {"label": "Sessions", "kind": "measured", "source": "Shopify Analytics 'Conversion over time' export, GA4, or a published sheet",
                 "calc": "as reported by the analytics tool", "basis": "do not mix GA4 and Shopify sessions in one series"},
    "added_to_cart": {"label": "Sessions with add to cart", "kind": "measured", "source": "funnel export", "calc": "as reported", "basis": "sessions, not items"},
    "reached_checkout": {"label": "Sessions reaching checkout", "kind": "measured", "source": "funnel export", "calc": "as reported", "basis": "sessions"},
    "conversion_rate": {"label": "Conversion rate", "kind": "derived", "source": "orders and sessions", "calc": "orders / sessions, same window",
                        "basis": f"unavailable below 30 sessions"},
    "aov": {"label": "Average order value", "kind": "derived", "source": "revenue and orders", "calc": "revenue / orders, same window", "basis": "net of cancellations"},
    "stage_rates": {"label": "Funnel stage rates", "kind": "derived", "source": "funnel export",
                    "calc": "cart / sessions, checkout / cart, orders / checkout", "basis": "unavailable below 30 entrants per stage"},
    "leak_dollars": {"label": "Leak in dollars", "kind": "derived", "source": "this week's funnel vs the prior 4 weeks",
                     "calc": "entrants x (baseline rate - current rate) x later stages' current rates x AOV",
                     "basis": "revenue the stage would have made at its own 4-week baseline"},
    "projected_month_end": {"label": "Projected month-end", "kind": "estimate", "source": "revenue",
                            "calc": "month-to-date + trailing 14-day daily average x days left", "basis": "confidence high only with 14+ days and measured rates"},
    "next_7_days": {"label": "Next 7 days", "kind": "estimate", "source": "revenue", "calc": "the last 7 complete days repeated", "basis": "an estimate, not a booking"},
    "orders_needed": {"label": "Orders to goal", "kind": "derived", "source": "goal, revenue, AOV", "calc": "ceil((goal - revenue MTD) / AOV)",
                      "basis": "AOV is measured when possible, else trained, else the starting default, and says which"},
    "sessions_needed": {"label": "Sessions to goal", "kind": "derived", "source": "orders needed, conversion rate", "calc": "ceil(orders needed / conversion rate)", "basis": "as above"},
    "goal": {"label": "Monthly goal", "kind": "trained", "source": "you, via 'train'", "calc": "-", "basis": "stamped with who set it and when"},
    "cash_collected": {"label": "Cash collected", "kind": "measured", "source": "Stripe balance transactions (charges and payments)",
                       "calc": "sum per day", "basis": "settled cash, not order value; differs from revenue by refunds and timing"},
    "ad_spend": {"label": "Ad spend", "kind": "measured", "source": "Meta Ads insights or export", "calc": "spend per day", "basis": "account level"},
    "revenue_per_ad_dollar": {"label": "Revenue per ad dollar", "kind": "derived", "source": "revenue, ad spend", "calc": "revenue / ad spend, last 7 days",
                              "basis": "blended, not attributed; all revenue over all spend"},
    "new_opportunities": {"label": "New opportunities", "kind": "measured", "source": "GoHighLevel", "calc": "opportunities created per day", "basis": "createdAt"},
    "deals_won": {"label": "Deals won", "kind": "measured", "source": "GoHighLevel", "calc": "won opportunities per day", "basis": "lastStatusChangeAt"},
    "won_value": {"label": "Won value", "kind": "measured", "source": "GoHighLevel", "calc": "sum of monetaryValue on won opportunities", "basis": "lastStatusChangeAt"},
}


def explain(key):
    m = CATALOG.get(key)
    if not m:
        close = [k for k in CATALOG if key.lower() in k or key.lower() in CATALOG[k]["label"].lower()]
        if len(close) == 1:
            return explain(close[0])
        return {"error": f"no metric '{key}'", "did_you_mean": close or sorted(CATALOG)}
    return {"key": key, **m, "kind_means": KINDS[m["kind"]]}


def digest():
    """One line per metric, for the agent's cached system prompt."""
    return "\n".join(f"- {k} ({m['kind']}): {m['label']}. Source: {m['source']}. Calc: {m['calc']}. Basis: {m['basis']}."
                     for k, m in CATALOG.items())


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "explain":
        print(json.dumps(explain(sys.argv[2]), indent=2))
    else:
        print(json.dumps({"kinds": KINDS, "metrics": CATALOG}, indent=2))
