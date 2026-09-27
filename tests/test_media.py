"""Image & Video: the Open Generative AI catalogue driven through Muapi.ai.

No network. Muapi's submit / poll / upload endpoints and file downloads are
mocked; the catalogue is the real synced file. What matters: only catalogued
models with their declared inputs can be called, the submit-then-poll
lifecycle ends in a local file or a readable error, the catalogue exclusions
held, and search metadata is written by Claude or ChatGPT only.
"""

import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agency
import media
import providers

ROOT = Path(__file__).resolve().parent.parent


def pick(kind, pred=lambda m: True):
    return next(m for m in media.catalog()["models"] if m["kind"] == kind and pred(m))


class CatalogTests(unittest.TestCase):
    def test_catalogue_is_attributed_and_populated(self):
        c = media.catalog()
        self.assertEqual(c["license"], "MIT")
        self.assertIn("Open-Generative-AI", c["source"])
        for kind in media.KINDS:
            self.assertGreater(c["counts"][kind], 20, kind)
        self.assertTrue((ROOT / "data" / "media_models.LICENSE").is_file())

    def test_exclusions_held(self):
        text = json.dumps(media.catalog()["models"]).lower()
        self.assertNotIn("spicy", text)
        self.assertNotRegex(text, r"face[-_ ]?swap")
        for m in media.catalog()["models"]:
            for spec in m["inputs"].values():
                for opt in spec.get("enum") or []:
                    self.assertFalse(isinstance(opt, str) and re.search(r"\bkiss|kissing|sexy\b", opt, re.I),
                                     f"{m['id']} still offers {opt}")

    def test_unknown_models_cannot_be_called(self):
        with self.assertRaises(ValueError):
            media.model("not-a-model")

    def test_key_file_is_gitignored(self):
        self.assertIn(".muapi_credentials.json", (ROOT / ".gitignore").read_text(encoding="utf-8"))


class PayloadTests(unittest.TestCase):
    def test_only_declared_inputs(self):
        m = pick("text-to-image", lambda m: "aspect_ratio" in m["inputs"])
        with self.assertRaises(ValueError) as ctx:
            media.build_payload(m, {"prompt": "x", "nsfw_filter": False})
        self.assertIn("does not take", str(ctx.exception))

    def test_enum_and_prompt_rules(self):
        m = pick("text-to-image", lambda m: (m["inputs"].get("aspect_ratio") or {}).get("enum"))
        ok = m["inputs"]["aspect_ratio"]["enum"][0]
        self.assertEqual(media.build_payload(m, {"prompt": "a roof", "aspect_ratio": ok})["aspect_ratio"], ok)
        with self.assertRaises(ValueError):
            media.build_payload(m, {"prompt": "a roof", "aspect_ratio": "7:13"})
        with self.assertRaises(ValueError):
            media.build_payload(m, {"aspect_ratio": ok})               # no prompt

    def test_numbers_are_coerced_and_bounded(self):
        m = pick("text-to-video", lambda m: (m["inputs"].get("duration") or {}).get("type") in ("int", "integer")
                 and not m["inputs"]["duration"].get("enum") and m["inputs"]["duration"].get("maxValue"))
        spec = m["inputs"]["duration"]
        self.assertIsInstance(media.build_payload(m, {"prompt": "p", "duration": str(spec["minValue"])})["duration"], int)
        with self.assertRaises(ValueError):
            media.build_payload(m, {"prompt": "p", "duration": spec["maxValue"] + 100})

    def test_image_models_need_an_uploaded_image(self):
        m = pick("image-to-video", lambda m: "image_url" in m["inputs"] or "images_list" in m["inputs"])
        field = "image_url" if "image_url" in m["inputs"] else "images_list"
        with self.assertRaises(ValueError):
            media.build_payload(m, {"prompt": "slow push in"})
        with self.assertRaises(ValueError):
            media.build_payload(m, {"prompt": "p", field: "file:///C:/secret.png"})
        out = media.build_payload(m, {"prompt": "p", field: ["https://cdn.muapi.ai/a.png"]})
        self.assertTrue(out[field])

    def test_media_list_respects_max_items(self):
        m = pick("image-to-image", lambda m: (m["inputs"].get("images_list") or {}).get("maxItems"))
        n = m["inputs"]["images_list"]["maxItems"]
        with self.assertRaises(ValueError):
            media.build_payload(m, {"prompt": "p", "images_list": [f"https://x/{i}.png" for i in range(n + 1)]})


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(media, "MEDIA_DIR", Path(self.tmp.name)),
                  mock.patch.object(media, "_key", return_value="k" * 20),
                  mock.patch.object(media, "POLL_EVERY", 0),
                  mock.patch.dict(media._JOBS, {}, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.model = pick("text-to-image", lambda m: set(m["inputs"]) >= {"prompt"})

    def run_job(self, responses, download=True):
        calls = []

        def fake_request(method, path, body=None, headers=None, timeout=60):
            calls.append((method, path, json.loads(body) if body and headers.get("Content-Type") == "application/json" else None))
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        with mock.patch.object(media, "_request", side_effect=fake_request), \
             mock.patch.object(media, "_download", side_effect=lambda j, u, i: f"{self.tmp.name}/out-{i}.png") as dl, \
             mock.patch.object(media.threading, "Thread", side_effect=lambda target, args, **k: NS(start=lambda: target(*args))):
            job = media.generate(self.model["id"], {"prompt": "a sunlit clinic"})
        return job, calls

    def test_submit_poll_download(self):
        job, calls = self.run_job([{"request_id": "r1"}, {"status": "processing"},
                                   {"status": "completed", "outputs": ["https://cdn.muapi.ai/1.png"]}])
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["outputs"], ["https://cdn.muapi.ai/1.png"])
        self.assertTrue(job["files"][0].endswith(".png"))
        self.assertEqual(calls[0][:2], ("POST", f"/api/v1/{self.model['endpoint']}"))
        self.assertEqual(calls[0][2], {"prompt": "a sunlit clinic"})
        self.assertEqual(calls[1][:2], ("GET", "/api/v1/predictions/r1/result"))
        self.assertTrue((Path(self.tmp.name) / "jobs" / f"{job['id']}.json").is_file())

    def test_transient_poll_errors_are_retried(self):
        job, _ = self.run_job([{"request_id": "r1"}, media.MediaError("Muapi 502: bad gateway"),
                               {"status": "succeeded", "url": "https://cdn.muapi.ai/1.png"}])
        self.assertEqual(job["status"], "done")

    def test_failure_says_why_and_mentions_a_refund(self):
        job, _ = self.run_job([{"request_id": "r1"},
                               {"status": "failed", "error": "prompt rejected by provider", "cost": {"refunded": True}}])
        self.assertEqual(job["status"], "failed")
        self.assertIn("prompt rejected by provider", job["error"])
        self.assertIn("refunded", job["error"])

    def test_auth_errors_fail_immediately(self):
        job, _ = self.run_job([media.MediaError("Muapi 401: invalid api key")])
        self.assertEqual(job["status"], "failed")
        self.assertIn("401", job["error"])

    def test_no_generation_without_a_key(self):
        with mock.patch.object(media, "_key", return_value=""):
            with self.assertRaises(ValueError):
                media.generate(self.model["id"], {"prompt": "x"})

    def test_output_urls_are_normalised(self):
        self.assertEqual(media._output_urls({"outputs": [{"url": "https://a/1.mp4"}, "https://a/2.mp4"]}),
                         ["https://a/1.mp4", "https://a/2.mp4"])
        self.assertEqual(media._output_urls({"output": {"url": "https://a/3.png"}}), ["https://a/3.png"])
        self.assertEqual(media._output_urls({"outputs": ["ftp://nope"]}), [])


class UploadTests(unittest.TestCase):
    def test_upload_limits_and_types(self):
        import base64
        with mock.patch.object(media, "_key", return_value="k" * 20):
            with self.assertRaises(ValueError):
                media.upload_reference("a.exe", base64.b64encode(b"MZ").decode())
            with self.assertRaises(ValueError):
                media.upload_reference("a.png", "")
            big = base64.b64encode(b"0" * (media.MAX_REFERENCE_BYTES + 1)).decode()
            with self.assertRaises(ValueError):
                media.upload_reference("a.png", big)
            with mock.patch.object(media, "_request", return_value={"url": "https://cdn.muapi.ai/u.png"}) as req:
                url = media.upload_reference("../../evil name.png", "data:image/png;base64," + base64.b64encode(b"\x89PNG").decode())
        self.assertEqual(url, "https://cdn.muapi.ai/u.png")
        body = req.call_args.args[2]
        self.assertIn(b'filename="evil_name.png"', body)     # path parts and spaces stripped


class EngineChoiceTests(unittest.TestCase):
    def test_alt_text_is_search_work_and_prompt_polish_is_creative(self):
        job = {"id": "abc", "status": "done", "kind": "text-to-image", "prompt": "a sunlit clinic"}
        with mock.patch.object(media, "job", return_value=job), mock.patch.object(media, "_save"), \
             mock.patch.object(providers, "complete",
                               return_value={"text": '{"alt": "Sunlit clinic", "filename": "Sunlit Clinic!", "caption": "c"}',
                                             "provider": "chatgpt", "model": "m"}) as comp:
            meta = media.seo_metadata("abc")
        self.assertEqual(comp.call_args.kwargs["provider"], providers.CLOUD)
        self.assertEqual(meta["filename"], "sunlitclinic")
        with mock.patch.object(providers, "complete", return_value={"text": "better", "provider": "claude"}) as comp:
            media.improve_prompt("clinic", "text-to-image")
        self.assertEqual(comp.call_args.kwargs["provider"], providers.BRAIN)


class ServingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "workspace" / "media" / "j").mkdir(parents=True)
        self.file = self.root / "workspace" / "media" / "j" / "output-1.mp4"
        self.file.write_bytes(bytes(range(256)) * 4)       # 1024 bytes
        p = mock.patch.object(agency, "PROJECT_DIR", self.root)
        p.start()
        self.addCleanup(p.stop)

    def serve(self, path, rng=""):
        h = NS(headers={"Range": rng} if rng else {}, wfile=io.BytesIO(), status=None, hdrs={}, json=None)
        h.send_response = lambda s: setattr(h, "status", s)
        h.send_header = lambda k, v: h.hdrs.__setitem__(k, v)
        h.end_headers = lambda: None
        h.send_json = lambda d, s=200: (setattr(h, "status", s), setattr(h, "json", d))
        agency._send_media(h, str(path))
        return h

    def test_whole_file_and_ranges(self):
        h = self.serve(self.file)
        self.assertEqual((h.status, len(h.wfile.getvalue()), h.hdrs["Content-Type"]), (200, 1024, "video/mp4"))
        h = self.serve(self.file, "bytes=100-199")
        self.assertEqual(h.status, 206)
        self.assertEqual(h.wfile.getvalue(), (bytes(range(256)) * 4)[100:200])
        self.assertEqual(h.hdrs["Content-Range"], "bytes 100-199/1024")
        h = self.serve(self.file, "bytes=-24")
        self.assertEqual(len(h.wfile.getvalue()), 24)
        self.assertEqual(self.serve(self.file, "bytes=5000-").status, 416)

    def test_confined_to_the_media_folder(self):
        outside = self.root / "agency.db"
        outside.write_bytes(b"secret")
        self.assertEqual(self.serve(outside).status, 403)
        self.assertEqual(self.serve(self.root / "workspace" / "media" / ".." / ".." / "agency.db").status, 403)


class EndpointTests(unittest.TestCase):
    def test_registered(self):
        for path in ("/api/agency/media/key", "/api/agency/media/generate", "/api/agency/media/upload",
                     "/api/agency/media/cancel", "/api/agency/media/improve", "/api/agency/media/seo"):
            self.assertIn(path, agency.POST_ROUTES)


if __name__ == "__main__":
    unittest.main()


class ImageFieldTests(unittest.TestCase):
    """~100 image-based models name their start image at model level (imageField)."""

    def test_every_image_based_model_can_receive_its_image(self):
        for m in media.catalog()["models"]:
            if m["kind"].startswith("image-"):
                self.assertTrue(any(media.is_media(k, m) for k in media.effective_inputs(m)),
                                f"{m['id']} has no way to receive its image")

    def test_model_level_image_field_is_accepted(self):
        m = media.model("kling-v2.1-master-i2v")
        self.assertNotIn("image_url", m["inputs"])
        out = media.build_payload(m, {"prompt": "slow push in", "image_url": "https://cdn.muapi.ai/a.png"})
        self.assertEqual(out["image_url"], "https://cdn.muapi.ai/a.png")
        with self.assertRaises(ValueError):
            media.build_payload(m, {"prompt": "slow push in"})


class TransportTests(unittest.TestCase):
    """Muapi's Let's Encrypt Root YE chain fails OpenSSL's Windows-store path."""

    def test_requests_verify_with_the_os_trust_store(self):
        import ssl
        self.assertIsInstance(media._TLS, ssl.SSLContext)
        self.assertEqual(media._TLS.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(media._TLS.check_hostname)
        seen = {}

        def fake(req, timeout=None, context=None):
            seen["context"] = context
            return mock.MagicMock(__enter__=lambda s: io.BytesIO(b'{"url": "https://cdn.muapi.ai/x.png"}'),
                                  __exit__=lambda *a: False)
        with mock.patch.object(media, "_key", return_value="k"), \
             mock.patch.object(media.urllib.request, "urlopen", side_effect=fake):
            media._request("GET", "/api/v1/ping")
        self.assertIs(seen["context"], media._TLS)

    def test_nested_credit_error_is_readable(self):
        inner = json.dumps({"error": {"code": "INSUFFICIENT_CREDITS", "message": "Insufficient credits."}})
        live = ('{"detail":{"error":{"code":"INSUFFICIENT_CREDITS","message":"Insufficient credits. '
                'A credit balance > 0 is required for file uploads."}}}')   # captured from Muapi
        for body in (live, json.dumps({"detail": inner}), json.dumps(inner), inner):
            msg = media._detail(body)
            self.assertIn("no credits", msg)
            self.assertNotIn("{", msg)
