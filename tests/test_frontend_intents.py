"""Runs the browser-side test files under Node so one command covers the suite.

The Jarvis intent parser and the frontend modules are plain ES modules; Node
can import and exercise them directly. Skipped, loudly, where Node is absent.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed — frontend tests not run")
class FrontendTests(unittest.TestCase):
    def run_node(self, script):
        res = subprocess.run([NODE, str(ROOT / "tests" / script)], cwd=ROOT,
                             capture_output=True, timeout=60)
        out = (res.stdout + res.stderr).decode("utf-8", "replace")
        self.assertEqual(res.returncode, 0, out)
        return out

    def test_jarvis_intent_parser(self):
        self.assertIn("intent checks passed", self.run_node("intents.test.mjs"))

    def test_jarvis_voice_streaming_and_echo(self):
        self.assertIn("voice checks passed", self.run_node("voice.test.mjs"))

    def test_every_frontend_module_parses(self):
        """node --check on ESM needs --input-type=module; a plain --check passes broken files."""
        bad = []
        for f in sorted((ROOT / "app" / "q" / "js").rglob("*.js")):
            res = subprocess.run([NODE, "--input-type=module", "--check"], input=f.read_bytes(),
                                 capture_output=True, timeout=60)
            if res.returncode:
                bad.append(f"{f.relative_to(ROOT)}: {res.stderr.decode('utf-8', 'replace')[:300]}")
        self.assertEqual(bad, [], "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
