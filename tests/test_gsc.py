"""Tests for the GSC adapter and sync logic (no network — Google calls mocked)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency
import gsc


class GSCAdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig = (gsc.CREDS_PATH, gsc.TOKEN_PATH)
        gsc.CREDS_PATH = tmp / "creds.json"
        gsc.TOKEN_PATH = tmp / "token.json"

    def tearDown(self):
        gsc.CREDS_PATH, gsc.TOKEN_PATH = self._orig
        self._tmp.cleanup()

    def test_status_unconfigured(self):
        st = gsc.status()
        self.assertFalse(st["configured"])
        self.assertFalse(st["connected"])

    def test_save_credentials_and_auth_url(self):
        with self.assertRaises(ValueError):
            gsc.save_credentials("", "")
        gsc.save_credentials("id123", "sec456")
        self.assertTrue(gsc.status()["configured"])
        url = gsc.auth_url("127.0.0.1", 51764)
        self.assertIn("client_id=id123", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("state=", url)

    def test_state_consumed_once(self):
        s = gsc.make_state()
        self.assertTrue(gsc.consume_state(s))
        self.assertFalse(gsc.consume_state(s))
        self.assertFalse(gsc.consume_state("bogus"))

    def test_api_requires_connection(self):
        with self.assertRaises(gsc.GSCError):
            gsc.list_sites()

    def test_top_queries_shapes_rows(self):
        rows = [{"keys": ["best crm"], "clicks": 12, "impressions": 340, "ctr": 0.035, "position": 7.3}]
        with mock.patch.object(gsc, "query", return_value=rows):
            out = gsc.top_queries("sc-domain:example.com")
        self.assertEqual(out[0], {"keyword": "best crm", "clicks": 12, "impressions": 340,
                                  "ctr": 3.5, "position": 7.3})

    def test_performance_equal_windows_and_daily_series(self):
        from datetime import date
        with mock.patch.object(gsc, "query", side_effect=[
            [{"clicks": 20, "impressions": 100, "ctr": .2, "position": 4}],
            [{"clicks": 10, "impressions": 80, "ctr": .125, "position": 7}],
            [{"keys": ["2026-09-10"], "clicks": 5, "impressions": 30},
             {"keys": ["2026-09-09"], "clicks": 3, "impressions": 20}],
        ]) as query:
            result = gsc.performance("sc-domain:example.com", 28, include_series=True)
        for call in query.call_args_list[:2]:
            payload = call.args[1]
            self.assertEqual((date.fromisoformat(payload['endDate']) - date.fromisoformat(payload['startDate'])).days + 1, 28)
        current, previous = [c.args[1] for c in query.call_args_list[:2]]
        self.assertEqual((date.fromisoformat(current['startDate']) - date.fromisoformat(previous['endDate'])).days, 1)
        self.assertEqual(result['daily'][0]['date'], '2026-09-09')
        self.assertEqual(result['current']['clicks'], 20)
        self.assertEqual(result['previous']['clicks'], 10)

    def test_performance_rejects_unbounded_ranges(self):
        for days in (0, -1, 365, True, '28'):
            with self.assertRaises(ValueError):
                gsc.performance('sc-domain:example.com', days)


class GSCSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        agency.AGENCY_DB = Path(cls._tmp.name) / "agency-test.db"
        agency._initialized = False
        agency.init_db()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_sync_requires_mapping(self):
        c = agency.client_create({"name": "Unmapped"})
        with self.assertRaises(ValueError):
            agency.gsc_sync_client(c["id"])
        agency.client_delete(c["id"])

    def test_map_requires_existing_client(self):
        with self.assertRaises(KeyError):
            agency.gsc_map_client("nope", "sc-domain:example.com")

    def test_sync_updates_and_imports_verified(self):
        c = agency.client_create({"name": "Mapped Co"})
        agency.gsc_map_client(c["id"], "sc-domain:example.com")
        tracked = agency.keyword_create({"keyword": "best crm", "provenance": "estimated",
                                         "client_id": c["id"], "position": 15})
        fake_rows = [
            {"keyword": "best crm", "clicks": 12, "impressions": 340, "ctr": 3.5, "position": 7.3},
            {"keyword": "crm for agencies", "clicks": 4, "impressions": 90, "ctr": 4.4, "position": 12.0},
            {"keyword": "tiny query", "clicks": 0, "impressions": 3, "ctr": 0.0, "position": 40.0},
        ]
        with mock.patch.object(gsc, "top_queries", return_value=fake_rows):
            res = agency.gsc_sync_client(c["id"])
        self.assertEqual(res["updated"], 1)
        self.assertEqual(res["imported"], 1)  # tiny query skipped (<10 impressions)
        kws = {k["keyword"]: k for k in agency.keywords_list(c["id"])}
        self.assertEqual(kws["best crm"]["provenance"], "verified")
        self.assertEqual(kws["best crm"]["position"], 7)
        self.assertEqual(kws["best crm"]["prev_position"], 15)
        self.assertEqual(kws["crm for agencies"]["source"], "gsc")
        self.assertNotIn("tiny query", kws)
        agency.client_delete(c["id"])


if __name__ == "__main__":
    unittest.main()
