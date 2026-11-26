"""send.py reads the reply from each agent's hook. No test starts Talkback or opens a socket.

    python -m unittest discover -s tests -v
"""
import base64
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import send  # noqa: E402


class Hooks(unittest.TestCase):
    def sent(self, argv, stdin=""):
        """The text and source main() hands to deliver()."""
        stream = mock.Mock(buffer=io.BytesIO(stdin.encode("utf-8")))
        with mock.patch.object(sys, "argv", ["send.py", *argv]), mock.patch.object(sys, "stdin", stream), \
                mock.patch.object(send, "talker_for", return_value=None), mock.patch.object(send, "deliver") as deliver:
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


class IdFile(unittest.TestCase):
    """<folder>/.talkback.json: made by the first send, never changed after."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name).resolve() / "my-project"
        self.folder.mkdir()
        self.file = self.folder / ".talkback.json"

    def test_the_first_send_writes_it_with_a_new_id_and_the_folders_name(self):
        talker = send.talker_for(self.folder)
        saved = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(saved, {"id": str(uuid.UUID(saved["id"])), "name": "my-project"})
        self.assertEqual(talker, dict(saved, folder=f"{self.folder.parent.name}/my-project"))

    def test_the_id_survives_a_rename_of_the_folder(self):
        first = send.talker_for(self.folder)
        moved = self.folder.rename(self.folder.with_name("renamed"))
        second = send.talker_for(moved)
        self.assertEqual((second["id"], second["name"]), (first["id"], "my-project"))

    def test_a_file_that_cannot_be_read_is_left_alone(self):
        for content in ("<<<<<<< merge", '{"name": "x"}', '{"id": "not-a-uuid"}', "[]"):
            self.file.write_text(content, encoding="utf-8")
            self.assertIsNone(send.talker_for(self.folder), content)
            self.assertEqual(self.file.read_text(encoding="utf-8"), content)

    def test_a_file_saved_with_a_byte_order_mark_is_read(self):
        self.file.write_text(json.dumps({"id": str(uuid.uuid4()), "name": "x"}), encoding="utf-8-sig")
        self.assertEqual(send.talker_for(self.folder)["name"], "x")

    def test_a_folder_that_cannot_be_written_is_no_talker(self):
        self.assertIsNone(send.talker_for(self.folder / "not-there"))
        self.assertFalse((self.folder / "not-there").exists())

    def sent_from(self, argv, stdin="", env=None):
        stream = mock.Mock(buffer=io.BytesIO(stdin.encode("utf-8")))
        with mock.patch.object(sys, "argv", ["send.py", *argv]), mock.patch.object(sys, "stdin", stream), \
                mock.patch.dict(os.environ, env or {}, clear=False), mock.patch.object(send, "deliver") as deliver:
            if not env:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)
            send.main()
        return deliver.call_args.kwargs["talker"]

    def test_a_hook_uses_the_folder_in_its_json(self):
        payload = json.dumps({"cwd": str(self.folder), "last_assistant_message": "Done."})
        self.assertEqual(self.sent_from([], payload)["name"], "my-project")

    def test_claude_codes_project_folder_wins_over_where_a_cd_left_it(self):
        below = self.folder / "sub"
        below.mkdir()
        payload = json.dumps({"cwd": str(below), "last_assistant_message": "Done."})
        talker = self.sent_from([], payload, {"CLAUDE_PROJECT_DIR": str(self.folder)})
        self.assertEqual(talker["name"], "my-project")
        self.assertFalse((below / ".talkback.json").exists())

    def test_text_by_hand_uses_the_current_folder(self):
        with mock.patch.object(os, "getcwd", return_value=str(self.folder)):
            talker = self.sent_from(["--text", "Done."], env={"CLAUDE_PROJECT_DIR": str(self.folder.parent)})
        self.assertEqual(talker["name"], "my-project")

    def test_the_talker_goes_in_the_request_and_a_console_test_has_none(self):
        with mock.patch("urllib.request.urlopen") as opened:
            send.deliver("Done.", "claude", 1, talker={"id": "i", "name": "n", "folder": "f"})
            send.deliver("Testing.", "console", 1, only="openrouter")
        bodies = [json.loads(call.args[0].data) for call in opened.call_args_list]
        self.assertEqual(bodies[0]["talker"], {"id": "i", "name": "n", "folder": "f"})
        self.assertNotIn("talker", bodies[1])
        self.assertFalse(Path(".talkback.json").exists())


class StartedWith(unittest.TestCase):
    """Whatever Python runs send.py, Talkback is started with the one it runs on."""

    def command(self, settings):
        with tempfile.TemporaryDirectory() as folder:
            if settings is not None:
                (Path(folder) / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
            with mock.patch.object(send, "HERE", Path(folder)), mock.patch.object(send.subprocess, "run") as run, \
                    mock.patch.object(send.sys, "executable", r"C:\Kernel\python.exe"):
                send.start_talkback()
        return base64.b64decode(run.call_args.args[0][-1]).decode("utf-16-le")

    def test_the_stored_pythonw_is_used(self):
        stored = str(Path(sys.executable))         # any file that exists
        self.assertIn(stored, self.command({"pythonw": stored}))

    def test_without_one_or_with_one_that_is_gone_the_callers_is_used(self):
        for settings in (None, {}, {"pythonw": r"C:\gone\pythonw.exe"}):
            self.assertIn(r"C:\Kernel\pythonw.exe", self.command(settings), settings)


class Helpers(unittest.TestCase):
    """A background Claude Code session is a helper of another agent: its Stop hook says nothing."""

    def test_a_background_session_is_a_helper(self):
        import tempfile
        with tempfile.TemporaryDirectory() as jobs:
            job = Path(jobs) / "2ee1ba4b"
            job.mkdir()
            (job / "state.json").write_text(json.dumps({"sessionId": "2ee1ba4b-x", "resumeSessionId": "9abc-y"}))
            for session, helper in (("2ee1ba4b-x", True), ("9abc-y", True), ("someone-typing", False)):
                payload = json.dumps({"session_id": session, "last_assistant_message": "Done."})
                self.assertEqual(send.is_helper(payload, Path(jobs)), helper)

    def test_a_marked_terminal_session_is_a_helper(self):
        import tempfile
        with tempfile.TemporaryDirectory() as jobs, tempfile.TemporaryDirectory() as terminal:
            (Path(terminal) / "0f8e1c2a-1111-4222-8333-444455556666.json").write_text("{}")
            for session, helper in (("0f8e1c2a-1111-4222-8333-444455556666", True), ("someone-typing", False),
                                    ("../0f8e1c2a-1111-4222-8333-444455556666", False)):
                payload = json.dumps({"session_id": session, "last_assistant_message": "Done."})
                self.assertEqual(send.is_helper(payload, Path(jobs), Path(terminal)), helper)

    def test_a_helper_sends_nothing(self):
        payload = json.dumps({"session_id": "s", "last_assistant_message": "Done."})
        stream = mock.Mock(buffer=io.BytesIO(payload.encode("utf-8")))
        with mock.patch.object(sys, "argv", ["send.py", "--source", "claude"]), mock.patch.object(sys, "stdin", stream), \
                mock.patch.object(send, "is_helper", return_value=True), mock.patch.object(send, "deliver") as deliver:
            send.main()
        deliver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
