#!/usr/bin/env python3
"""Write a realistic, clearly fake daily.csv so you can try everything before connecting a store.

    python3 example/make_sample.py --end 2026-11-16 --out example/daily.csv

The last 7 days have a deliberate checkout problem (cart-to-checkout drops), so the
brief has something real to find. One day of ad spend is left blank on purpose to
show that blank means unavailable, not zero.
"""
import argparse, csv, datetime as dt, random

ap = argparse.ArgumentParser()
ap.add_argument("--end", default=str(dt.date.today() - dt.timedelta(days=1)))
ap.add_argument("--days", type=int, default=75)
ap.add_argument("--out", default="daily.csv")
a = ap.parse_args()
end = dt.date.fromisoformat(a.end)
rng = random.Random(7)
with open(a.out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["date", "sessions", "added_to_cart", "reached_checkout", "orders", "revenue", "ad_spend"])
    for i in range(a.days):
        d = end - dt.timedelta(days=a.days - 1 - i)
        weekend = d.weekday() >= 5
        sessions = int(rng.gauss(1500 if not weekend else 1800, 120))
        cart = int(sessions * rng.uniform(0.075, 0.085))
        broken = (end - d).days < 7                        # the last 7 days: checkout friction
        checkout = int(cart * (rng.uniform(0.40, 0.46) if broken else rng.uniform(0.55, 0.60)))
        orders = int(checkout * rng.uniform(0.62, 0.68))
        revenue = round(orders * rng.gauss(82, 6), 2)
        spend = "" if (end - d).days == 20 else round(rng.gauss(420, 40), 2)
        w.writerow([d, sessions, cart, checkout, orders, revenue, spend])
print("wrote", a.out)
