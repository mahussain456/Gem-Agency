"""'No wait': spoken changes of mind, resolved on this PC without a model."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jarvis


class CorrectionTests(unittest.TestCase):
    SURE = [
        ("Launch the Northgate site on Thursday, no wait, Friday.", "Launch the Northgate site on Friday."),
        ("So tell the client we can launch on Thursday no wait Friday and the homepage is done.",
         "So tell the client we can launch on Friday and the homepage is done."),
        ("Meet at the cafe, no wait, at the office tomorrow.", "Meet at the office tomorrow."),
        ("Send the brief to Maya, I mean Priya.", "Send the brief to Priya."),
        ("The budget is 500, no wait, 700 dollars.", "The budget is 700 dollars."),
        ("Call Omar at 3, sorry, 4 pm.", "Call Omar at 4 pm."),
        ("Open growth, no wait, approvals", "Open approvals"),
        ("We should ship it today. Scratch that. Let us wait until Monday.", "Let us wait until Monday."),
        ("Make the hero blue, no wait, make the whole header dark green.", "Make the whole header dark green."),
    ]
    UNSURE = [                      # left for the model, never guessed
        "You know what I mean.",
        "I am sorry, we are late.",
        "Thanks so much, sorry, really appreciate it.",
        "I mean it, the deadline is real.",
    ]

    def test_sure_corrections(self):
        for said, meant in self.SURE:
            with self.subTest(said=said):
                self.assertEqual(jarvis.resolve_corrections(said), (meant, True))

    def test_unsure_is_not_rewritten(self):
        for said in self.UNSURE:
            with self.subTest(said=said):
                text, sure = jarvis.resolve_corrections(said)
                self.assertFalse(sure)
                self.assertEqual(text, said)

    def test_dictation_resolves_without_a_model(self):
        import whisper_engine
        with mock.patch.object(whisper_engine, "transcribe",
                               return_value={"text": "Um, launch on Thursday, no wait, Friday.", "seconds": 0.6}), \
                mock.patch.object(jarvis, "_quick") as model:
            out = jarvis.dictate("dictate", b"\0" * 32000)
        model.assert_not_called()
        self.assertEqual((out["text"], out["provider"]), ("Launch on Friday.", "local"))

    def test_unsure_dictation_goes_to_the_model(self):
        import whisper_engine
        with mock.patch.object(whisper_engine, "transcribe",
                               return_value={"text": "I am sorry, we are late.", "seconds": 0.6}), \
                mock.patch.object(jarvis, "_quick", return_value={"text": "I'm sorry, we're late.", "provider": "chatgpt"}) as model:
            out = jarvis.dictate("dictate", b"\0" * 32000)
        model.assert_called_once()
        self.assertEqual(out["provider"], "chatgpt")


if __name__ == "__main__":
    unittest.main()
