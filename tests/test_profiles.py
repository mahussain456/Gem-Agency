"""Tests for profiles.py, the dashboard's view of The Agency.

The key guarantee: the dashboard's Install button writes byte-for-byte what
agency/scripts/convert.sh writes, so re-running the script after a UI install
reports "unchanged" and neither path can drift from the other.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import profiles

BASH = shutil.which("bash")
CONVERT = profiles.AGENCY_DIR / "scripts" / "convert.sh"


class ParseTests(unittest.TestCase):
    def test_lists_every_profile_with_sections(self):
        data = profiles.list_profiles(profiles.destinations(Path(tempfile.gettempdir()) / "no-home",
                                                            Path(tempfile.gettempdir()) / "no-proj"))
        names = [a["name"] for a in data["agents"]]
        self.assertEqual(len(names), len(profiles.profile_paths()))
        self.assertIn("solidity-engineer", names)
        fe = next(a for a in data["agents"] if a["name"] == "frontend-developer")
        self.assertEqual(fe["division"], "engineering")
        self.assertEqual(fe["voice"], {"directness": 8, "depth": 4, "risk": 5})
        self.assertIn("react", fe["skills"])
        self.assertTrue(fe["done"] and fe["never"] and fe["how"])
        self.assertNotIn("**", " ".join(fe["handoffs"]))
        self.assertFalse(any(k.startswith("_") for k in fe), "internal fields must not reach the API")
        self.assertEqual({d["id"]: d["count"] for d in data["divisions"]}["design"], 2)

    def test_rejects_path_tricks_in_names(self):
        for bad in ["../secrets", "Frontend", "a/b", ""]:
            with self.assertRaises(ValueError):
                profiles.get(bad)
        with self.assertRaises(KeyError):
            profiles.get("nobody-here")


@unittest.skipUnless(BASH, "bash not available")
class ParityTests(unittest.TestCase):
    def test_render_matches_convert_sh_byte_for_byte(self):
        for path in profiles.profile_paths():
            p = profiles.parse(path)
            for target in ("claude", "cursor", "prompt"):
                r = subprocess.run([BASH, str(CONVERT), target, str(path)], capture_output=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(profiles.render(p, target), r.stdout.decode("utf-8"),
                                 f"{path.name} → {target} differs from convert.sh")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dests = profiles.destinations(self.tmp / "home", self.tmp / "proj")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def status(self, name, where):
        return profiles.status_of(profiles.get(name), where, self.dests)

    def test_install_status_lifecycle(self):
        self.assertEqual(self.status("deal-closer", "claude"), "missing")
        r = profiles.install("claude", ["deal-closer"], dests=self.dests)
        self.assertEqual(r["changed"], ["deal-closer"])
        self.assertEqual(self.status("deal-closer", "claude"), "synced")

        again = profiles.install("claude", ["deal-closer"], dests=self.dests)
        self.assertEqual(again["unchanged"], ["deal-closer"])

        f = self.dests["claude"] / "deal-closer.md"
        f.write_text(f.read_text(encoding="utf-8") + "\nlocal tweak\n", encoding="utf-8")
        self.assertEqual(self.status("deal-closer", "claude"), "outdated")

        profiles.install("claude", ["deal-closer"], uninstall=True, dests=self.dests)
        self.assertFalse(f.exists())

    def test_foreign_files_are_protected(self):
        f = self.dests["cursor"] / "ui-designer.mdc"
        f.parent.mkdir(parents=True)
        f.write_text("my own rule\n", encoding="utf-8")
        self.assertEqual(self.status("ui-designer", "cursor"), "foreign")

        r = profiles.install("cursor", ["ui-designer"], dests=self.dests)
        self.assertEqual(r["skipped"], ["ui-designer"])
        profiles.install("cursor", ["ui-designer"], uninstall=True, dests=self.dests)
        self.assertEqual(f.read_text(encoding="utf-8"), "my own rule\n")

        profiles.install("cursor", ["ui-designer"], force=True, dests=self.dests)
        self.assertEqual(self.status("ui-designer", "cursor"), "synced")

    def test_install_all_and_reject_unknown_destination(self):
        r = profiles.install("claude_project", dests=self.dests)
        self.assertEqual(len(r["changed"]), len(profiles.profile_paths()))
        with self.assertRaises(ValueError):
            profiles.install("vscode", dests=self.dests)


@unittest.skipUnless(BASH, "bash not available")
class ScriptAgreesWithUiInstallTests(unittest.TestCase):
    def test_script_sees_ui_install_as_unchanged(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            dests = profiles.destinations(tmp, tmp)
            profiles.install("claude", dests=dests)
            r = subprocess.run([BASH, str(profiles.AGENCY_DIR / "scripts" / "install.sh"), "claude",
                                "--dir", str(dests["claude"])], capture_output=True, text=True)
            self.assertIn(f"0 installed or updated, {len(profiles.profile_paths())} unchanged", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
