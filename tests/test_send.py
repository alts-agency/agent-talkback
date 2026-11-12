"""send.py reads the reply from each agent's hook. No test starts Talkback or opens a socket.

    python -m unittest discover -s tests -v
"""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import send  # noqa: E402


class Hooks(unittest.TestCase):
    def sent(self, argv, stdin=""):
        """The text and source main() hands to deliver()."""
        stream = mock.Mock(buffer=io.BytesIO(stdin.encode("utf-8")))
        with mock.patch.object(sys, "argv", ["send.py", *argv]), mock.patch.object(sys, "stdin", stream), \
                mock.patch.object(send, "deliver") as deliver:
            send.main()
        return deliver.call_args.args if deliver.called else None

    def test_claude_code_sends_its_reply_on_stdin(self):
        payload = json.dumps({"hook_event_name": "Stop", "last_assistant_message": "Done."})
        self.assertEqual(self.sent(["--source", "claude"], payload), ("Done.", "claude"))

    def test_codex_notify_passes_its_reply_as_an_argument(self):
        payload = json.dumps({"type": "agent-turn-complete", "turn-id": "1",
                              "input-messages": ["fix it"], "last-assistant-message": "Fixed."})
        self.assertEqual(self.sent(["--source", "codex", payload]), ("Fixed.", "codex"))

    def test_hermes_post_llm_call_sends_its_reply_in_extra(self):
        payload = json.dumps({"hook_event_name": "post_llm_call", "tool_name": None, "session_id": "s1",
                              "extra": {"user_message": "hi", "assistant_response": "Hello."}})
        self.assertEqual(self.sent(["--source", "hermes"], payload), ("Hello.", "hermes"))

    def test_text_by_hand(self):
        self.assertEqual(self.sent(["--text", "Build passed."]), ("Build passed.", "agent"))

    def test_an_empty_reply_sends_nothing(self):
        self.assertIsNone(self.sent([], ""))


if __name__ == "__main__":
    unittest.main()
