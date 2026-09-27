import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parents[1]
SERVER_PATH = PROJECT_DIR / "server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("mission_server", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MissionMemoryTests(unittest.TestCase):
    def setUp(self):
        self.server = load_server()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.server.MISSION_MEMORY_DB = Path(self.tmp.name) / "mission_memory.db"

    def test_init_memory_seeds_single_current_mission(self):
        self.server.init_mission_memory()

        with sqlite3.connect(self.server.MISSION_MEMORY_DB) as conn:
            conn.row_factory = sqlite3.Row
            missions = conn.execute("SELECT * FROM missions").fetchall()
            events = conn.execute("SELECT * FROM mission_events").fetchall()

        self.assertEqual(len(missions), 1)
        self.assertEqual(missions[0]["status"], "active")
        self.assertIn("Mission Memory", missions[0]["goal"])
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "decision")

    def test_get_current_memory_returns_structured_state(self):
        updated = self.server.mission_memory_update({
            "goal": "Ship shared operational brain",
            "constraints": ["stdlib only", "local SQLite"],
            "current_hypothesis": "Structured mission state improves handoffs",
            "open_questions": ["Which agent owns QA?"],
            "decisions": ["Use one active mission row"],
            "artifacts": ["server.py"],
            "blocked_items": ["No user acceptance yet"],
            "next_best_action": "Expose it in the dashboard",
            "owner_agent": "forge",
        })

        memory = self.server.mission_memory_current()

        self.assertEqual(memory["id"], updated["id"])
        self.assertEqual(memory["goal"], "Ship shared operational brain")
        self.assertEqual(memory["constraints"], ["stdlib only", "local SQLite"])
        self.assertEqual(memory["owner_agent"], "forge")
        self.assertEqual(memory["next_best_action"], "Expose it in the dashboard")
        self.assertTrue(memory["updated_at"])
        self.assertGreaterEqual(len(memory["timeline"]), 1)

    def test_memory_update_normalizes_owner_and_list_fields(self):
        memory = self.server.mission_memory_update({
            "owner_agent": "@Dev",
            "constraints": "one\ntwo",
            "open_questions": "first, second",
            "decisions": ["decide"],
            "artifacts": [{"name": "ignored object"}, "artifact.md"],
            "blocked_items": None,
        })

        self.assertEqual(memory["owner_agent"], "dev")
        self.assertEqual(memory["constraints"], ["one", "two"])
        self.assertEqual(memory["open_questions"], ["first", "second"])
        self.assertEqual(memory["artifacts"], ["artifact.md"])
        self.assertEqual(memory["blocked_items"], [])

    def test_snapshot_includes_mission_memory_safe_section(self):
        self.server.mission_memory_update({"goal": "Snapshot mission memory"})
        with mock.patch.object(self.server, "gateway_data", return_value={}), \
             mock.patch.object(self.server, "activity_data", return_value={}), \
             mock.patch.object(self.server, "sessions_data", return_value={}), \
             mock.patch.object(self.server, "vps_health", return_value={}), \
             mock.patch.object(self.server, "cron_jobs", return_value={}), \
             mock.patch.object(self.server, "board_list", return_value=[]):
            snap = self.server.snapshot()

        self.assertTrue(snap["mission_memory"]["ok"])
        self.assertEqual(snap["mission_memory"]["data"]["goal"], "Snapshot mission memory")


class AttachmentSupportTests(unittest.TestCase):
    def setUp(self):
        self.server = load_server()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.server.UPLOAD_ROOT = Path(self.tmp.name) / "uploads"

    def test_save_uploaded_files_persists_text_and_image_metadata(self):
        files = [
            {
                "name": "notes.txt",
                "type": "text/plain",
                "data_base64": "SGVsbG8gTWlzc2lvbiBDb250cm9sIQ==",
            },
            {
                "name": "pixel.png",
                "type": "image/png",
                "data_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0Y4AAAAASUVORK5CYII=",
            },
        ]

        uploaded = self.server.save_uploaded_files(files)

        self.assertEqual(len(uploaded), 2)
        text_file, image_file = uploaded
        self.assertEqual(text_file["name"], "notes.txt")
        self.assertEqual(text_file["kind"], "text")
        self.assertIn("Hello Mission Control!", text_file["preview"])
        self.assertTrue(Path(text_file["path"]).exists())
        # Uploads are served from /api/uploads/<stored_name>; the bare /uploads/
        # form belonged to a superseded helper that has since been removed.
        self.assertTrue(text_file["url"].startswith("/api/uploads/"), text_file["url"])
        self.assertIn("stored_name", text_file)
        self.assertEqual(image_file["kind"], "image")
        self.assertEqual(image_file["mime_type"], "image/png")
        self.assertTrue(Path(image_file["path"]).exists())

    def test_bridge_messages_adds_attachment_instructions_and_paths(self):
        # Attachments must come from files this server actually stored. A
        # caller-supplied "path" is deliberately rejected — honouring one would
        # let a client have an agent read any file on the machine.
        attachments = self.server.save_uploaded_files([
            {
                "name": "diagram.png",
                "type": "image/png",
                "data_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Z0Y4AAAAASUVORK5CYII=",
            },
            {
                "name": "brief.md",
                "type": "text/markdown",
                "data_base64": "IyBCcmllZgpVc2UgdGhlIGF0dGFjaGVkIGxhdW5jaCBjaGVja2xpc3Qu",
            },
        ])
        self.assertEqual(len(attachments), 2)

        messages = self.server.bridge_messages(
            "Dev",
            "Review the uploaded material",
            conversation_id="abc123",
            history=[],
            target="@dev",
            attachments=attachments,
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1]["role"], "user")
        content = messages[-1]["content"]
        # An image attachment makes the content multimodal (a parts list); the
        # narrative instructions live in the text part.
        text = content if isinstance(content, str) else "".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict))
        self.assertIn("Review the uploaded material", text)
        self.assertIn("Attached files:", text)
        self.assertIn("diagram.png", text)
        self.assertIn("brief.md", text)
        self.assertIn("read_file", text)
        self.assertIn("Use the attached launch checklist.", text)  # text excerpt inlined
        for meta in attachments:
            self.assertIn(meta["path"], text)  # agent is told the real saved path
            self.assertTrue(Path(meta["path"]).exists())


if __name__ == "__main__":
    unittest.main()
