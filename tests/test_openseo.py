"""OpenSEO, run and managed by the dashboard.

No process is started here: the manager's decisions (environment, status,
restart on a key change) are tested with the process layer mocked. The patch
is checked against the real checkout when one is installed.
"""

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openseo


class EnvTests(unittest.TestCase):
    def test_only_an_allowlist_reaches_openseo(self):
        """Its build copies the whole environment into dist/*/.dev.vars."""
        fake = {"PATH": "C:\\bin", "SYSTEMROOT": "C:\\Windows", "ANTHROPIC_API_KEY": "sk-ant-secret",
                "OPENAI_API_KEY": "sk-secret", "CLAUDE_CODE_MESSAGING_TOKEN": "tok", "GITHUB_TOKEN": "ghp"}
        with mock.patch.dict(openseo.os.environ, fake, clear=True), \
             mock.patch.object(openseo, "_dataforseo_key", return_value=""):
            env = openseo._env()
        self.assertEqual(env["PATH"], "C:\\bin")
        for secret in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_MESSAGING_TOKEN", "GITHUB_TOKEN"):
            self.assertNotIn(secret, env)
        self.assertEqual(env["AUTH_MODE"], "local_noauth")
        self.assertEqual(env["OPENSEO_TELEMETRY_DISABLED"], "1")
        self.assertNotIn("DATAFORSEO_API_KEY", env)

    def test_the_dashboard_key_is_passed_in_openseo_format(self):
        with mock.patch("dataforseo.credentials", return_value={"login": "me@x.com", "password": "pw"}):
            key = openseo._dataforseo_key()
            env = openseo._env()
        self.assertEqual(base64.b64decode(key).decode(), "me@x.com:pw")
        self.assertEqual(env["DATAFORSEO_API_KEY"], key)

    def test_only_this_dashboard_may_embed_it(self):
        with mock.patch.dict(openseo.os.environ, {"MISSION_CONTROL_PORT": "51764"}), \
             mock.patch.object(openseo, "_dataforseo_key", return_value=""):
            env = openseo._env()
        self.assertEqual(env["OPENSEO_FRAME_ANCESTORS"], "http://127.0.0.1:51764 http://localhost:51764")


class StatusTests(unittest.TestCase):
    def setUp(self):
        openseo._state.update(phase="stopped", error="", proc=None, since=0.0)
        self.addCleanup(openseo._state.update, phase="stopped", error="", proc=None, since=0.0)

    def test_answering_is_running_whoever_started_it(self):
        with mock.patch.object(openseo, "health", return_value={"status": "ok", "checks": {
                "dataforseo": {"status": "warn", "message": "Not set"}}}):
            st = openseo.status()
        self.assertTrue(st["running"])
        self.assertEqual(st["phase"], "running")
        self.assertEqual(st["checks"]["dataforseo"]["status"], "warn")

    def test_a_crash_outside_the_dashboard_shows_as_stopped(self):
        openseo._state["phase"] = "running"
        with mock.patch.object(openseo, "health", return_value=None):
            self.assertEqual(openseo.status()["phase"], "stopped")

    def test_a_failure_keeps_its_reason_and_log(self):
        openseo._state.update(phase="failed", error="building failed (exit 1)")
        with mock.patch.object(openseo, "health", return_value=None), \
             mock.patch.object(openseo, "_log_tail", return_value=["error TS2304"]):
            st = openseo.status()
        self.assertEqual((st["phase"], st["error"], st["log"]), ("failed", "building failed (exit 1)", ["error TS2304"]))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(openseo, "PIDFILE", Path(self.tmp.name) / "server.json"),
                  mock.patch.object(openseo, "installed", return_value=True),
                  mock.patch.object(openseo, "_spawn")):
            p.start()
            self.addCleanup(p.stop)
        openseo._state.update(phase="stopped", error="", proc=None, since=0.0)
        self.addCleanup(openseo._state.update, phase="stopped", error="", proc=None, since=0.0)

    def test_an_instance_already_running_with_the_same_key_is_reused(self):
        openseo.PIDFILE.write_text(json.dumps({"pid": 42, "key": "k1"}), encoding="utf-8")
        with mock.patch.object(openseo, "health", return_value={"status": "ok"}), \
             mock.patch.object(openseo, "_key_fingerprint", return_value="k1"), \
             mock.patch.object(openseo, "_kill_tree") as kill:
            openseo.start()
        kill.assert_not_called()
        openseo._spawn.assert_not_called()

    def test_a_running_instance_with_an_old_key_is_restarted(self):
        openseo.PIDFILE.write_text(json.dumps({"pid": 42, "key": "old"}), encoding="utf-8")
        with mock.patch.object(openseo, "health", return_value={"status": "ok"}), \
             mock.patch.object(openseo, "_key_fingerprint", return_value="new"), \
             mock.patch.object(openseo, "_kill_tree") as kill, mock.patch.object(openseo.time, "sleep"):
            openseo.start()
        kill.assert_called_once_with(42)
        openseo._spawn.assert_called_once()

    def test_start_needs_an_install(self):
        with mock.patch.object(openseo, "installed", return_value=False):
            with self.assertRaisesRegex(ValueError, "not installed"):
                openseo.start()

    def test_stop_kills_the_recorded_process(self):
        openseo.PIDFILE.write_text(json.dumps({"pid": 77}), encoding="utf-8")
        with mock.patch.object(openseo, "_kill_tree") as kill, mock.patch.object(openseo, "health", return_value=None):
            st = openseo.stop()
        kill.assert_called_once_with(77)
        self.assertFalse(openseo.PIDFILE.exists())
        self.assertEqual(st["phase"], "stopped")


class PatchTests(unittest.TestCase):
    def test_patch_only_widens_frame_ancestors_to_loopback(self):
        text = openseo.PATCH.read_text(encoding="utf-8")
        self.assertIn("frame-ancestors 'self'", text)
        self.assertIn(r"(127\.0\.0\.1|localhost)", text)
        self.assertEqual([l for l in text.splitlines() if l.startswith("+++")], ["+++ b/src/server.ts"])

    @unittest.skipUnless((openseo.OPENSEO_DIR / ".git").is_dir() and shutil.which("git"), "OpenSEO is not installed")
    def test_patch_matches_the_installed_checkout(self):
        r = subprocess.run(["git", "-C", str(openseo.OPENSEO_DIR), "apply", "--reverse", "--check", str(openseo.PATCH)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)        # applied, and exactly this patch


class DashboardTests(unittest.TestCase):
    def test_routes_and_integration(self):
        import agency
        for path in ("/api/agency/openseo/install", "/api/agency/openseo/start", "/api/agency/openseo/stop"):
            self.assertIn(path, agency.POST_ROUTES)
        agency._openseo_state_cache["value"] = None
        with mock.patch.object(openseo, "status", return_value={
                "running": True, "phase": "running", "installed": True, "url": openseo.URL,
                "dataforseo": False, "error": ""}):
            st = agency._openseo_integration_state()
        self.assertEqual(st["state"], "running")
        agency._openseo_state_cache["value"] = None

    def test_saving_the_dataforseo_key_restarts_openseo(self):
        import agency
        with mock.patch("dataforseo.save_credentials"), mock.patch("dataforseo.status", return_value={}), \
             mock.patch.object(agency.threading, "Thread") as th:
            agency._dfs_setup({"login": "a", "password": "b"})
        self.assertIs(th.call_args.kwargs["target"], openseo.restart_if_running)

    def test_ahrefs_is_gone(self):
        import agency
        root = Path(agency.__file__).resolve().parent
        for f in ("agency.py", "playbooks.py", "app/q/js/boot.js", "app/q/js/pages/misc.js"):
            self.assertNotIn("ahrefs", (root / f).read_text(encoding="utf-8").lower(), f)


class DevVarsTests(unittest.TestCase):
    def test_preview_variables_are_rewritten_with_the_current_key_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for t in ("server", "open_seo_audit"):
                (root / "dist" / t).mkdir(parents=True)
                (root / "dist" / t / ".dev.vars").write_text("OPENAI_API_KEY='leaked'\n", encoding="utf-8")
            env = {"PATH": r"C:\Program Files (x86)\node", "OPENAI_API_KEY": "sk-secret"}
            with mock.patch.object(openseo, "OPENSEO_DIR", root), \
                 mock.patch.dict(openseo.os.environ, env, clear=True), \
                 mock.patch.object(openseo, "_dataforseo_key", return_value="ZW1haWw6cHc="):
                openseo._write_dev_vars()
            for t in ("server", "open_seo_audit"):
                text = (root / "dist" / t / ".dev.vars").read_text(encoding="utf-8")
                self.assertIn("DATAFORSEO_API_KEY='ZW1haWw6cHc='", text)
                self.assertIn(r"PATH='C:\Program Files (x86)\node'", text)
                self.assertIn("CLOUDFLARE_VITE_BUILD='true'", text)
                self.assertNotIn("OPENAI_API_KEY", text)          # the old, leaked file is replaced
