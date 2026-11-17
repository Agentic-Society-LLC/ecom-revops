"""The numbers contract and the self-healing rules, asserted. Run: python3 -m unittest discover tests"""
import atexit, datetime as dt, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
_STATE = tempfile.TemporaryDirectory()          # every test's state lives here and is removed at exit
atexit.register(_STATE.cleanup)
os.environ["REVOPS_STATE_DIR"] = _STATE.name

import revops, health, cro, gaps, grounding, catalog  # noqa: E402

D = dt.date(2026, 11, 17)


def day(i):
    return D - dt.timedelta(days=i)


def rows(n=42, **over):
    r = {}
    for i in range(1, n + 1):
        r[day(i)] = {f: None for f in revops.FIELDS}
        r[day(i)].update(sessions=1000.0, added_to_cart=80.0, reached_checkout=46.0, orders=30.0, revenue=2400.0)
    for k, v in over.items():
        r[day(1)][k] = v
    return r


class Numbers(unittest.TestCase):
    def test_blank_is_unavailable_and_zero_is_real(self):
        self.assertIsNone(revops._num(""))
        self.assertEqual(revops._num("0"), 0.0)
        r = rows(revenue=None)
        t = revops.window_total(r, "revenue", day(7), day(1))
        self.assertIsNone(t["value"])
        self.assertIn("1 of 7 days", t["detail"])

    def test_zero_baseline_has_no_percent(self):
        c = revops.compare({"value": 500.0}, {"value": 0.0}, "Revenue")
        self.assertEqual(c["status"], "no-baseline")
        self.assertIsNone(c["pct"])
        self.assertEqual(c["delta"], 500.0)

    def test_missing_side_is_unknown_not_flat(self):
        c = revops.compare({"value": None}, {"value": 10.0}, "Orders")
        self.assertEqual(c["status"], "unavailable")
        self.assertIsNone(c["direction"])

    def test_rate_needs_a_sample(self):
        r = revops.rate({"value": 2.0}, {"value": 10.0})
        self.assertIsNone(r["value"])
        self.assertIn("minimum sample", r["detail"])

    def test_leak_in_dollars(self):
        r = rows()
        for i in range(1, 8):
            r[day(i)]["reached_checkout"] = 32.0      # cart to checkout 40% vs 57.5% baseline
            r[day(i)]["orders"] = 20.87               # keep checkout to order constant
            r[day(i)]["revenue"] = 20.87 * 80
        snap = revops.build(r, 100000, D)
        top = [l for l in snap["leaks"] if l["status"] == "live"][0]
        self.assertEqual(top["stage"], "Cart to checkout")
        self.assertGreater(top["dollars"], 0)

    def test_whatif_with_no_overrides_reproduces_live(self):
        r = rows()
        w = revops.whatif(r, D, 100000)
        self.assertAlmostEqual(w["waterfall"]["projected_month_end"], w["baseline_projection"], places=2)

    def test_scenario_never_changes_actuals(self):
        r = rows()
        before = revops.pacing(r, 100000, D)["projected_month_end"]
        revops.whatif(r, D, 100000, cr=0.2)
        self.assertEqual(revops.pacing(r, 100000, D)["projected_month_end"], before)

    def test_precedence_scenario_measured_trained_default(self):
        self.assertEqual(revops.resolve("cr", 0.03, 0.05)["source"], "scenario")
        self.assertEqual(revops.resolve("cr", 0.03)["source"], "measured")
        revops.train({"cr": 0.04}, "test")
        self.assertEqual(revops.resolve("cr", None)["source"], "trained")
        self.assertEqual(revops.resolve("aov", None)["source"], "default")

    def test_training_refuses_out_of_range(self):
        out = revops.train({"cr": 7.0}, "test")
        self.assertTrue(out["refused"])

    def test_same_situation_does_not_re_alarm(self):
        a = [{"type": "wow", "key": "Conversion rate", "direction": "down", "pct": -0.2}]
        b = [{"type": "wow", "key": "Conversion rate", "direction": "down", "pct": -0.35}]
        self.assertEqual(revops.fingerprint(a), revops.fingerprint(b))

    def test_stall_is_flagged(self):
        r = rows()
        for i in range(1, 8):
            r[day(i)]["orders"] = 0.0
            r[day(i)]["revenue"] = 0.0
        f = revops.funnel(r, day(7), day(1))
        self.assertTrue(revops.stalled_stages(f))


class SelfHeal(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(health.classify_failure(http_status=401), "auth")
        self.assertEqual(health.classify_failure(http_status=429), "rate-limit")
        self.assertEqual(health.classify_failure(http_status=503), "server")
        self.assertEqual(health.classify_failure(http_status=400), "upstream-rejected")
        self.assertEqual(health.state_for_class("server"), "degraded")
        self.assertEqual(health.state_for_class("auth"), "failed")

    def _conn(self, errors, data=None):
        class C:
            setup_hint = ""
            calls = 0

            def configured(self):
                return True

            def heal(self, fc, n):
                return None

            def pull(self, s, e):
                C.calls += 1
                if errors:
                    raise errors.pop(0)
                return data or {day(1): {"orders": 1.0}}
        return C()

    def test_transient_failure_heals(self):
        c = self._conn([health.ConnectorError("blip", http_status=503)])
        r = health.run_with_heal("t1", c, day(2), day(1), sleep=lambda s: None)
        self.assertEqual(r["state"], "connected")
        self.assertEqual(r["attempts"], 2)

    def test_auth_failure_does_not_retry(self):
        c = self._conn([health.ConnectorError("bad key", http_status=401)] * 3)
        r = health.run_with_heal("t2", c, day(2), day(1), sleep=lambda s: None)
        self.assertEqual(r["state"], "failed")
        self.assertEqual(type(c).calls, 1)

    def test_down_source_serves_last_good_labelled_stale(self):
        ok = self._conn([])
        health.run_with_heal("t3", ok, day(2), day(1), sleep=lambda s: None)
        bad = self._conn([health.ConnectorError("down", http_status=503)] * 5)
        r = health.run_with_heal("t3", bad, day(2), day(1), sleep=lambda s: None)
        self.assertEqual(r["source"], "last-good")
        self.assertIn("stale", r["detail"])

    def test_one_alert_per_outage_per_day(self):
        self.assertTrue(health.should_alert("t4", "auth"))
        self.assertFalse(health.should_alert("t4", "auth"))
        self.assertTrue(health.should_alert("t4", "server"))

    def test_summary_never_says_all_live_when_one_is_not(self):
        rows_ = [{"state": "connected"}, {"state": "degraded"}]
        self.assertNotIn("All", health.summary_text(health.counts(rows_)))


class CRO(unittest.TestCase):
    def test_no_peeking(self):
        r = cro.check(1000, 20, 1000, 40, planned_per_arm=10000)
        self.assertEqual(r["verdict"], "keep running")

    def test_real_winner(self):
        r = cro.check(20000, 500, 20000, 620, planned_per_arm=20000)
        self.assertEqual(r["verdict"], "B wins")

    def test_sample_size_sane(self):
        n = cro.sample_size(0.025, 0.10)
        self.assertTrue(55000 < n < 75000)


class Honesty(unittest.TestCase):
    def test_invented_number_is_caught(self):
        snap = {"pacing": {"goal": 120000, "revenue_mtd": 52377.38}, "wow": []}
        self.assertFalse(grounding.check("You need $9,999 more", snap)["grounded"])
        self.assertTrue(grounding.check("You're at $52,377 of $120,000", snap)["grounded"])
        self.assertTrue(grounding.check("$67,623 to go", snap)["grounded"])

    def test_gap_dedupes(self):
        k1, _ = gaps.add("cant_answer", "Refund rate by product!")
        k2, g = gaps.add("cant_answer", "refund rate by product")
        self.assertEqual(k1, k2)
        self.assertEqual(g["count"], 2)

    def test_every_snapshot_metric_is_in_the_catalog(self):
        for k in ("revenue", "orders", "sessions", "conversion_rate", "aov", "leak_dollars", "projected_month_end"):
            self.assertIn(k, catalog.CATALOG)


if __name__ == "__main__":
    unittest.main()
