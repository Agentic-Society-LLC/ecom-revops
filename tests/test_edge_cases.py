"""Edge cases found by an independent review, each reproduced and locked in."""
import atexit, datetime as dt, json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
_STATE = tempfile.TemporaryDirectory()
atexit.register(_STATE.cleanup)
os.environ["REVOPS_STATE_DIR"] = _STATE.name

import revops, health, cro, grounding, connectors  # noqa: E402

D = dt.date(2026, 11, 17)


def revenue_only(n=40):
    return {D - dt.timedelta(days=i): {**{f: None for f in revops.FIELDS}, "revenue": 3000.0} for i in range(1, n + 1)}


class GuessesNeverBecomeFacts(unittest.TestCase):
    def test_brief_does_not_claim_stages_are_fine_when_unmeasured(self):
        b = revops.build(revenue_only(), 100000, D)["brief"]
        self.assertNotIn("no stage is below its baseline", b)
        self.assertIn("not measurable yet", b)

    def test_size_of_prize_comes_from_measured_revenue(self):
        p = revops.pacing(revenue_only(), 100000, D)
        lever = revops.biggest_lever(p)
        self.assertAlmostEqual(lever["gain"], p["run_rate_daily"] * p["days_left"] * 0.10)

    def test_whatif_reproduces_live_even_with_no_funnel(self):
        w = revops.whatif(revenue_only(), D, 100000)
        self.assertAlmostEqual(w["waterfall"]["projected_month_end"], w["baseline_projection"], places=2)
        self.assertEqual(w["held_from_actuals"], [])
        self.assertEqual(set(w["assumed"]), {"conversion_rate", "aov", "sessions_per_day"})

    def test_whatif_override_scales_the_live_run_rate(self):
        r = revenue_only()
        p = revops.pacing(r, 100000, D)
        w = revops.whatif(r, D, 100000, cr=p["conversion_rate"]["value"] * 1.5)
        rest = w["waterfall"]["projected_rest_of_month"]
        self.assertAlmostEqual(rest, p["run_rate_daily"] * 1.5 * p["days_left"], places=2)


class Headlines(unittest.TestCase):
    def test_unchanged_headline_stays_quiet_every_day(self):
        rows, _ = revops.load(os.path.join(ROOT, "example", "daily.csv"))
        today = max(rows) + dt.timedelta(days=1)
        heads = [revops.build(rows, 120000, today, persist=True)["brief"].split("\n")[2] for _ in range(3)]
        self.assertTrue(heads[0].startswith("**Biggest move:** Revenue") or "move" in heads[0])
        self.assertIn("nothing new", heads[1])
        self.assertIn("nothing new", heads[2])


class BadData(unittest.TestCase):
    def test_bad_csv_fails_one_source_not_the_sync(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.csv")
            with open(p, "w") as f:
                f.write("day_of,sessions\n2026-11-01,100\n")
            os.environ["FUNNEL_CSV"] = p
            try:
                status = connectors.sync(os.path.join(d, "out.csv"), 10, sleep=lambda s: None)
            finally:
                os.environ.pop("FUNNEL_CSV")
            row = next(r for r in status["rows"] if r["name"] == "funnel")
            self.assertEqual(row["fail_class"], "bad-data")
            self.assertEqual(row["state"], "failed")
            self.assertTrue(os.path.exists(os.path.join(d, "out.csv")))

    def test_ambiguous_slash_dates_are_refused_not_guessed(self):
        with self.assertRaises(revops.DataFormatError):
            revops.parse_date("01/02/2026")
        os.environ["REVOPS_DATE_FORMAT"] = "%m/%d/%Y"
        try:
            self.assertEqual(revops.parse_date("11/16/2026"), dt.date(2026, 11, 16))
        finally:
            os.environ.pop("REVOPS_DATE_FORMAT")
        self.assertEqual(revops.parse_date("Nov 16, 2026"), dt.date(2026, 11, 16))

    def test_ab_check_rejects_impossible_counts(self):
        self.assertEqual(cro.check(100, 120, 100, 5)["verdict"], "unavailable")


class Grounding(unittest.TestCase):
    snap = {"pacing": {"goal": 120000, "revenue_mtd": 52377.38}, "wow": []}

    def test_millions_are_checked(self):
        self.assertFalse(grounding.check("you land around $4.2M", self.snap)["grounded"])

    def test_dates_and_years_are_not_amounts(self):
        self.assertTrue(grounding.check("Data through 2026-11-16: $52,377 so far in 2026", self.snap)["grounded"])


class Truncation(unittest.TestCase):
    def test_ghl_more_than_cap_is_an_error_not_zeros(self):
        page = {"opportunities": [{"createdAt": "2026-11-10T10:00:00Z", "status": "open"}] * 100,
                "meta": {"startAfter": 1, "startAfterId": "x"}}
        orig = connectors.http_json
        connectors.http_json = lambda url, h=None: (page, {})
        os.environ.update(GHL_TOKEN="t", GHL_LOCATION_ID="l", GHL_MAX_PAGES="3")
        try:
            with self.assertRaises(health.ConnectorError) as e:
                connectors.GoHighLevel().pull(dt.date(2026, 11, 1), dt.date(2026, 11, 16))
            self.assertTrue(e.exception.bad_data)
        finally:
            connectors.http_json = orig
            for k in ("GHL_TOKEN", "GHL_LOCATION_ID", "GHL_MAX_PAGES"):
                os.environ.pop(k)


if __name__ == "__main__":
    unittest.main()
