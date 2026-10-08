"""Autopilot, monthly client reports, Search Console auto-mapping and the
billing memory that sends builds to ChatGPT when Claude has no credit."""

import json
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency
import autopilot
import providers


class _Db(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = agency.AGENCY_DB
        agency.AGENCY_DB = Path(self._tmp.name) / "autopilot-test.db"
        agency._initialized = False
        agency.init_db()

    def tearDown(self):
        agency.AGENCY_DB = self._orig
        agency._initialized = False
        self._tmp.cleanup()

    def site(self, name="Northgate Plumbing", url="https://www.northgate.example/", client=None):
        pid = agency.project_create({"name": name, "client_id": client})["id"]
        with agency._conn() as c:
            c.execute("UPDATE projects SET url=? WHERE id=?", (url, pid))
        return pid

    def client(self, name="Northgate"):
        return agency.client_create({"name": name})["id"]


class AutopilotSettingsTests(_Db):
    def test_off_by_default(self):
        s = autopilot.settings(self.site())
        self.assertFalse(s["enabled"])
        self.assertEqual(s["next_at"], "")

    def test_needs_a_live_address(self):
        pid = self.site(url="")
        with self.assertRaises(ValueError):
            autopilot.set_autopilot(pid, True)
        self.assertFalse(autopilot.set_autopilot(pid, False)["enabled"])

    def test_only_known_intervals(self):
        with self.assertRaises(ValueError):
            autopilot.set_autopilot(self.site(), True, 3)

    def test_unknown_project(self):
        with self.assertRaises(KeyError):
            autopilot.settings("nope")


class DueCampaignTests(_Db):
    def test_new_enabled_site_is_due_now(self):
        pid = self.site()
        autopilot.set_autopilot(pid, True, 7)
        self.assertEqual(autopilot.due_campaigns(), [pid])

    def test_disabled_site_never_runs(self):
        autopilot.set_autopilot(self.site(), False)
        self.assertEqual(autopilot.due_campaigns(), [])

    def test_waits_for_the_interval(self):
        pid = self.site()
        autopilot.set_autopilot(pid, True, 7)
        last = datetime.now(timezone.utc) - timedelta(days=3)
        with agency._conn() as c:
            c.execute("UPDATE autopilot SET last_run_at=? WHERE project_id=?", (last.isoformat(), pid))
        self.assertEqual(autopilot.due_campaigns(), [])
        self.assertEqual(autopilot.due_campaigns(last + timedelta(days=7)), [pid])

    def test_a_site_with_a_live_run_is_not_doubled(self):
        pid = self.site()
        autopilot.set_autopilot(pid, True)
        with agency._conn() as c:
            c.execute("INSERT INTO pipeline_runs (id, project_id, playbook, state, stage_index, error, created_at, updated_at) "
                      "VALUES (?,?,?,?,0,'',?,?)", (uuid.uuid4().hex, pid, "seo_campaign", "awaiting_approval",
                                                    agency.now_iso(), agency.now_iso()))
        self.assertEqual(autopilot.due_campaigns(), [])

    def test_run_records_the_outcome(self):
        pid = self.site()
        autopilot.set_autopilot(pid, True)
        fake = mock.Mock()
        fake.start_run.side_effect = RuntimeError("provider credits exhausted")
        with mock.patch.dict(sys.modules, {"runner": fake}):
            out = autopilot.run_campaigns()
        fake.start_run.assert_called_once_with(pid, "seo_campaign")
        self.assertIn("could not start", out[0]["note"])
        s = autopilot.settings(pid)
        self.assertIn("provider credits exhausted", s["last_note"])
        self.assertTrue(s["last_run_at"])


class ReportTests(_Db):
    def setUp(self):
        super().setUp()
        self._reports = autopilot.REPORTS_DIR
        autopilot.REPORTS_DIR = Path(self._tmp.name) / "reports"

    def tearDown(self):
        autopilot.REPORTS_DIR = self._reports
        super().tearDown()

    def fake_pdf(self, html, out):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"%PDF-1.4 test")
        return out

    def test_once_per_client_per_month(self):
        cid = self.client()
        self.site(client=cid)
        self.client("No Website Yet")
        now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        with mock.patch.object(autopilot, "render_pdf", side_effect=self.fake_pdf) as pdf:
            first = autopilot.run_reports(now)
            second = autopilot.run_reports(now + timedelta(hours=5))
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["period"], "2026-10")
        self.assertEqual(second, [])
        self.assertEqual(pdf.call_count, 1)
        listed = {c["name"]: c["reports"] for c in autopilot.reports()}
        self.assertEqual(len(listed["Northgate"]), 1)
        self.assertEqual(listed["No Website Yet"], [])
        self.assertEqual(autopilot.report_file(first[0]["id"]).read_bytes()[:4], b"%PDF")

    def test_a_failed_report_is_reported_not_hidden(self):
        cid = self.client()
        self.site(client=cid)
        with mock.patch.object(autopilot, "render_pdf", side_effect=RuntimeError("Chrome not found")):
            out = autopilot.run_reports()
        self.assertIn("Chrome not found", out[0]["error"])

    def test_report_file_stays_inside_the_reports_folder(self):
        cid = self.client()
        outside = Path(self._tmp.name) / "secret.pdf"
        outside.write_bytes(b"x")
        with agency._conn() as c:
            c.execute("INSERT INTO client_reports (id, client_id, period, path, created_at) VALUES (?,?,?,?,?)",
                      ("r1", cid, "2026-09", str(outside), agency.now_iso()))
        with self.assertRaises(KeyError):
            autopilot.report_file("r1")
        with self.assertRaises(KeyError):
            autopilot.report_file("missing")


class GscAutoMapTests(_Db):
    def test_domain_normalising(self):
        for raw in ("https://www.Site.com/x", "sc-domain:site.com", "site.com:443", "http://site.com"):
            self.assertEqual(agency._site_domain(raw), "site.com", raw)

    def test_maps_by_domain_and_prefers_domain_properties(self):
        a, b = self.client("Northgate"), self.client("Nobody")
        self.site(client=a, url="https://www.northgate.example/services")
        self.site("Other", client=b, url="https://other.example/")
        out = agency.gsc_auto_map(["https://northgate.example/", "sc-domain:northgate.example"])
        self.assertEqual(out["mapped"], [{"client": "Northgate", "site_url": "sc-domain:northgate.example"}])
        self.assertEqual(out["unmatched"], ["Nobody"])

    def test_existing_mappings_are_left_alone(self):
        a = self.client()
        self.site(client=a)
        agency.gsc_map_client(a, "https://chosen.example/")
        self.assertEqual(agency.gsc_auto_map(["sc-domain:northgate.example"])["mapped"], [])


class BillingMemoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.file = Path(self._tmp.name) / "provider_health.json"
        self._p = mock.patch.object(providers, "_health_file", return_value=self.file)
        self._p.start()
        self._persist = mock.patch.object(providers, "_PERSIST", True)
        self._persist.start()
        self._health = dict(providers._HEALTH)
        providers._HEALTH.clear()

    def tearDown(self):
        providers._HEALTH.clear()
        providers._HEALTH.update(self._health)
        self._persist.stop()
        self._p.stop()
        self._tmp.cleanup()

    def test_only_the_server_writes_the_file(self):
        with mock.patch.object(providers, "_PERSIST", False):
            providers.record("claude", "credit balance too low")
        self.assertFalse(self.file.exists())

    def test_out_of_credit_is_remembered_for_hours(self):
        providers.record("claude", "Your credit balance is too low to access the Anthropic API.")
        self.assertEqual(providers._HEALTH["claude"]["ttl"], providers.BILLING_TTL)
        with mock.patch.object(providers.time, "time", return_value=time.time() + 3600):
            self.assertTrue(providers.failing("claude"))

    def test_ordinary_errors_clear_quickly(self):
        providers.record("chatgpt", "timed out")
        with mock.patch.object(providers.time, "time", return_value=time.time() + providers.FAILURE_TTL + 1):
            self.assertEqual(providers.failing("chatgpt"), "")
        self.assertFalse(self.file.exists())

    def test_billing_survives_a_restart_and_clears_on_success(self):
        providers.record("claude", "Error code: 400 - credit balance too low")
        saved = json.loads(self.file.read_text())
        self.assertIn("claude", saved)
        providers._HEALTH.clear()
        providers._load_billing()
        self.assertTrue(providers.failing("claude"))
        providers.record("claude")
        self.assertEqual(providers.failing("claude"), "")
        self.assertEqual(json.loads(self.file.read_text()), {})


if __name__ == "__main__":
    unittest.main()
