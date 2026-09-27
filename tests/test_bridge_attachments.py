import base64
import importlib.util
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SERVER_PATH = PROJECT_DIR / "server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("mission_server", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BridgeAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.server = load_server()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.server.CHAT_UPLOADS_ROOT = Path(self.tmp.name) / "uploads"

    def _data_url(self, mime_type: str, raw: bytes) -> str:
        return f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}"

    def test_bridge_messages_embed_text_attachment_context_and_save_file(self):
        attachment = {
            "name": "notes.txt",
            "mime_type": "text/plain",
            "data_url": self._data_url("text/plain", b"ship file uploads\nkeep image context"),
        }

        messages = self.server.bridge_messages(
            "Rank",
            "Review these notes",
            conversation_id="chat-123",
            history=[],
            target="@rank",
            attachments=[attachment],
        )

        user_message = messages[-1]
        self.assertIsInstance(user_message["content"], list)
        text_parts = [part for part in user_message["content"] if part.get("type") == "text"]
        self.assertTrue(any("Review these notes" in part.get("text", "") for part in text_parts))
        self.assertTrue(any("notes.txt" in part.get("text", "") for part in text_parts))
        self.assertTrue(any("ship file uploads" in part.get("text", "") for part in text_parts))
        saved_paths = [line.split(": ", 1)[1].strip() for part in text_parts for line in part.get("text", "").splitlines() if line.startswith("Saved path:")]
        self.assertTrue(saved_paths)
        self.assertTrue(Path(saved_paths[0]).exists())

    def test_bridge_messages_add_image_parts_for_vision_models(self):
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5Wv1sAAAAASUVORK5CYII="
        )
        attachment = {
            "name": "diagram.png",
            "mime_type": "image/png",
            "data_url": self._data_url("image/png", png_bytes),
        }

        messages = self.server.bridge_messages(
            "Lumen",
            "Describe this image",
            conversation_id="chat-vision",
            history=[],
            target="@lumen",
            attachments=[attachment],
        )

        user_message = messages[-1]
        self.assertIsInstance(user_message["content"], list)
        image_parts = [part for part in user_message["content"] if part.get("type") == "image_url"]
        self.assertEqual(len(image_parts), 1)
        self.assertTrue(image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        text_parts = [part for part in user_message["content"] if part.get("type") == "text"]
        self.assertTrue(any("diagram.png" in part.get("text", "") for part in text_parts))
        self.assertTrue(any("Describe this image" in part.get("text", "") for part in text_parts))


if __name__ == "__main__":
    unittest.main()
