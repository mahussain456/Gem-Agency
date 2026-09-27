"""Tests for The Agency: the agent profiles and the scripts that ship them.

The profiles are the source of truth for Claude Code, Cursor and (next) the
Mission Control runner, so a malformed one must fail here, not in an editor.
The shell scripts are exercised for real through bash; the tests skip only
when no bash is on PATH.
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENCY = ROOT / "agency"
SCRIPTS = AGENCY / "scripts"
DIVISIONS = ["engineering", "design", "marketing", "sales", "product"]
BASH = shutil.which("bash")


def profiles():
    return sorted(p for d in DIVISIONS for p in (AGENCY / d).glob("*.md"))


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, f"{path} has no frontmatter"
    return dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)


def run(*args, cwd=None):
    return subprocess.run([BASH, *map(str, args)], cwd=cwd, capture_output=True, text=True)


class ProfileTests(unittest.TestCase):
    def test_every_division_is_staffed(self):
        for d in DIVISIONS:
            self.assertTrue(list((AGENCY / d).glob("*.md")), f"{d} has no agents")

    def test_prompt_roster_exists(self):
        names = {p.stem for p in profiles()}
        for required in ["frontend-developer", "backend-architect", "devops-automator",
                         "solidity-engineer", "ui-designer", "ux-researcher"]:
            self.assertIn(required, names)

    def test_names_are_unique_and_match_files(self):
        seen = set()
        for p in profiles():
            fm = frontmatter(p)
            self.assertEqual(fm["name"], p.stem)
            self.assertEqual(fm["division"], p.parent.name)
            self.assertNotIn(fm["name"], seen)
            seen.add(fm["name"])

    def test_handoffs_point_at_real_agents(self):
        names = {p.stem for p in profiles()}
        for p in profiles():
            for w in frontmatter(p)["works_with"].strip("[]").split(","):
                self.assertIn(w.strip(), names, f"{p.name} works_with unknown '{w.strip()}'")

    def test_agents_that_can_act_name_their_approval_gate(self):
        # The agency's safety floor: an agent that can execute commands, or whose
        # work goes out to the public or to prospects, must say in its Never
        # section that it stops at a human approval.
        for p in profiles():
            fm = frontmatter(p)
            never = p.read_text(encoding="utf-8").split("## Never\n", 1)[1].split("\n## ", 1)[0]
            self.assertGreaterEqual(never.count("\n- ") + never.startswith("- "), 3, f"{p.name}: thin Never list")
            if "Bash" in fm["tools"] or fm["division"] in {"marketing", "sales"}:
                self.assertRegex(never, r"(?i)approv|a human", f"{p.name} can act but names no approval gate")


@unittest.skipUnless(BASH, "bash not available")
class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lint_passes_on_shipped_profiles(self):
        r = run(SCRIPTS / "lint.sh")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_lint_catches_a_broken_profile(self):
        bad = self.tmp / "engineering" / "wrong-name.md"
        bad.parent.mkdir()
        bad.write_text("---\nname: something-else\ndivision: sales\nworks_with: [nobody]\n---\n# X\n",
                       encoding="utf-8")
        r = run(SCRIPTS / "lint.sh", bad)
        self.assertEqual(r.returncode, 1)
        for problem in ["must match the filename", "missing frontmatter key 'description'",
                        "unknown agent 'nobody'", "missing section '## Mission'"]:
            self.assertIn(problem, r.stdout)

    def test_claude_output_is_valid_subagent(self):
        r = run(SCRIPTS / "convert.sh", "claude", AGENCY / "engineering" / "frontend-developer.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        head, body = r.stdout.split("\n---\n", 1)
        keys = [line.split(":", 1)[0] for line in head.splitlines()[1:]]
        # Only keys Claude Code understands; the agency's own metadata stays out.
        self.assertEqual(keys, ["name", "description", "tools"])
        self.assertIn("You are **Frontend Developer**", body)
        self.assertIn("## Definition of done", body)
        self.assertNotIn("voice_directness", r.stdout)

    def test_cursor_output_is_agent_requested_rule(self):
        r = run(SCRIPTS / "convert.sh", "cursor", AGENCY / "sales" / "deal-closer.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith('---\ndescription: "Turns qualified'))
        self.assertIn("\nglobs:\nalwaysApply: false\n---\n", r.stdout)

    def test_prompt_output_has_no_frontmatter(self):
        r = run(SCRIPTS / "convert.sh", "prompt", AGENCY / "design" / "ui-designer.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(r.stdout.startswith("---"))

    def test_install_is_idempotent_and_uninstall_is_safe(self):
        dest = self.tmp / "agents"
        r = run(SCRIPTS / "install.sh", "claude", "--dir", dest)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(list(dest.glob("*.md"))), len(profiles()))

        again = run(SCRIPTS / "install.sh", "claude", "--dir", dest)
        self.assertIn(f"0 installed or updated, {len(profiles())} unchanged", again.stdout)

        # A file the user wrote themselves must survive install and uninstall.
        mine = dest / "ui-designer.md"
        mine.write_text("my own agent\n", encoding="utf-8")
        clash = run(SCRIPTS / "install.sh", "claude", "--dir", dest, "--agent", "ui-designer")
        self.assertEqual(clash.returncode, 2)
        self.assertEqual(mine.read_text(encoding="utf-8"), "my own agent\n")

        gone = run(SCRIPTS / "install.sh", "claude", "--dir", dest, "--uninstall")
        self.assertEqual(gone.returncode, 0, gone.stdout + gone.stderr)
        self.assertEqual([p.name for p in dest.glob("*.md")], ["ui-designer.md"])

    def test_filters_and_dry_run(self):
        dest = self.tmp / "rules"
        dry = run(SCRIPTS / "install.sh", "cursor", "--dir", dest, "--division", "design", "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("Installing 2 agent(s)", dry.stdout)
        self.assertFalse(dest.exists())

        one = run(SCRIPTS / "install.sh", "cursor", "--dir", dest, "--agent", "growth-hacker")
        self.assertEqual([p.name for p in dest.iterdir()], ["growth-hacker.mdc"])

        for bad in (["--division", "finance"], ["--agent", "nobody"]):
            r = run(SCRIPTS / "install.sh", "cursor", "--dir", dest, *bad)
            self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
