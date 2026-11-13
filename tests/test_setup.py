"""The setup console without the screen: secrets/, the reload request, the login shortcut.

    python -m unittest discover -s tests -v

No test makes a sound, creates a real shortcut or leaves 127.0.0.1.
"""
import base64
import json
import os
import queue
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import talkback  # noqa: E402

try:
    import console
    HAVE_CONSOLE = True
except Exception:  # no tkinter on this Python: the data-level tests still run
    HAVE_CONSOLE = False

SET = {"name": "HeyGen", "model": "heygen/x", "voice": "weston"}


def openrouter(key):
    """A source whose key is the given reference."""
    return {"id": "openrouter", "engine": "openai", "base_url": "https://e.test/v1", "key": key, "sets": [SET]}


class FolderSettings(unittest.TestCase):
    """Settings, log, secrets/ and the program folder all inside a temporary directory."""

    def setUp(self):
        self.original = (talkback.SETTINGS, talkback.LOG, talkback.SECRETS, talkback.HERE)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        talkback.SETTINGS = self.dir / "settings.json"
        talkback.LOG = self.dir / "talkback.log"
        talkback.SECRETS = self.dir / "secrets"
        talkback.HERE = self.dir
        self.addCleanup(self.restore)

    def restore(self):
        talkback.SETTINGS, talkback.LOG, talkback.SECRETS, talkback.HERE = self.original

    def settings(self, data=None):
        if data is not None:
            talkback.SETTINGS.write_text(json.dumps(data), encoding="utf-8")
        return talkback.Settings()


class Secrets(FolderSettings):
    def test_a_key_is_written_into_secrets_and_read_back(self):
        path = talkback.save_secret("openrouter", "  sk-test-123\n")
        self.assertEqual(path, self.dir / "secrets" / "openrouter.local")
        self.assertEqual(path.read_text(encoding="utf-8"), "sk-test-123\n")
        source = openrouter("file:secrets/openrouter.local")
        self.assertEqual(talkback.key_for(source), "sk-test-123")
        self.assertEqual(talkback.secret_state(source), "ready")

    def test_a_shipped_source_looks_for_its_key_in_secrets(self):
        keys = {source["id"]: source.get("key") for source in talkback.SOURCES}
        self.assertEqual(keys["openrouter"], "file:secrets/openrouter.local")
        self.assertEqual(keys["groq"], "file:secrets/groq.local")

    def test_an_empty_or_multi_line_key_is_refused(self):
        for bad in ("", "   ", "first line\nsecond line"):
            with self.subTest(key=bad):
                with self.assertRaises(ValueError):
                    talkback.save_secret("openrouter", bad)
        self.assertFalse((self.dir / "secrets" / "openrouter.local").exists())

    def test_a_missing_key_is_reported_rather_than_failing(self):
        self.assertEqual(talkback.secret_state(openrouter("file:secrets/openrouter.local")), "missing")
        self.assertFalse(talkback.can_speak(openrouter("file:secrets/openrouter.local")))

    def test_a_key_kept_elsewhere_is_copied_into_secrets(self):
        outside = self.dir / "elsewhere.local"
        outside.write_text("sk-elsewhere\n", encoding="utf-8")
        source = openrouter(f"file:{outside}")
        self.assertEqual(talkback.secret_state(source), "outside")

        copied = talkback.adopt_key(source)
        self.assertEqual(Path(copied), self.dir / "secrets" / "openrouter.local")
        self.assertEqual(source["key"], "file:secrets/openrouter.local")
        self.assertEqual(talkback.key_for(source), "sk-elsewhere")
        self.assertEqual(talkback.secret_state(source), "ready")

    def test_adopting_a_key_inside_secrets_changes_nothing(self):
        talkback.save_secret("openrouter", "sk-test-123")
        source = openrouter("file:secrets/openrouter.local")
        self.assertEqual(talkback.adopt_key(source), "")
        self.assertEqual(source["key"], "file:secrets/openrouter.local")

    def test_an_environment_key_is_not_copied(self):
        self.assertEqual(talkback.adopt_key(openrouter("env:OPENROUTER_API_KEY")), "")

    def test_microsoft_needs_no_key(self):
        self.assertEqual(talkback.secret_state({"id": "microsoft", "engine": "microsoft"}), "ready")

    def test_a_source_id_that_is_not_a_name_is_refused(self):
        for bad in ("Bad Id", "../escape", ""):
            with self.subTest(source_id=bad):
                with self.assertRaises(ValueError):
                    talkback.secret_path(bad)


class Reload(FolderSettings):
    def test_the_console_can_make_the_running_program_reread_settings(self):
        self.settings()
        settings = talkback.Settings()
        speaker = SimpleNamespace(items=queue.Queue())
        server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        server.RequestHandlerClass = talkback.make_handler(settings, speaker)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        saved = json.loads(talkback.SETTINGS.read_text(encoding="utf-8"))
        self.assertEqual(saved["primary"], "openrouter")  # what the console is about to replace
        saved["primary"] = "microsoft"
        talkback.SETTINGS.write_text(json.dumps(saved), encoding="utf-8")

        self.assertTrue(console.reload_running(server.server_address[1]))
        self.assertEqual(settings["primary"], "microsoft")
        self.assertIn("reloaded on request", talkback.LOG.read_text(encoding="utf-8"))

    def test_a_program_that_is_not_running_answers_false(self):
        self.assertFalse(console.reload_running(1))
        self.assertIsNone(console.speaking(1))

    def test_a_test_line_carries_the_one_source_it_may_use(self):
        self.settings()
        settings = talkback.Settings()
        items = queue.Queue()
        server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        server.RequestHandlerClass = talkback.make_handler(settings, SimpleNamespace(items=items))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        body = json.dumps({"text": "Testing talkback in Guy's Voice.", "source": "console",
                           "only": "microsoft"}).encode("utf-8")
        request = urllib.request.Request(f"http://127.0.0.1:{port}/say", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        self.assertEqual(urllib.request.urlopen(request, timeout=2).status, 202)
        self.assertEqual(items.get(timeout=2), {"text": "Testing talkback in Guy's Voice.", "source": "console",
                                                "only": "microsoft", "set": "", "voice": ""})

    def test_a_test_line_can_name_the_voice_as_well_as_the_source(self):
        """A test speaks one voice without choosing it, so the store arrives as data and never as a pick."""
        self.settings()
        settings = talkback.Settings()
        items = queue.Queue()
        server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        server.RequestHandlerClass = talkback.make_handler(settings, SimpleNamespace(items=items))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        body = json.dumps({"text": "Testing talkback in nadine's Voice.", "source": "console", "only": "openrouter",
                           "set": "HeyGen", "voice": "nadine"}).encode("utf-8")
        request = urllib.request.Request(f"http://127.0.0.1:{port}/say", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        self.assertEqual(urllib.request.urlopen(request, timeout=2).status, 202)
        self.assertEqual(items.get(timeout=2), {"text": "Testing talkback in nadine's Voice.", "source": "console",
                                                "only": "openrouter", "set": "HeyGen", "voice": "nadine"})

    def test_the_console_can_see_whether_speaking_is_off(self):
        self.settings()
        settings = talkback.Settings()
        server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        server.RequestHandlerClass = talkback.make_handler(settings, SimpleNamespace(items=queue.Queue()))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        self.assertTrue(console.speaking(port))
        settings["enabled"] = False
        self.assertFalse(console.speaking(port))

    def test_the_console_can_switch_speaking_and_the_tray_hears_about_it(self):
        self.settings()
        settings = talkback.Settings()
        speaker = SimpleNamespace(items=queue.Queue(), stop=threading.Event())
        painted = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        server.RequestHandlerClass = talkback.make_handler(settings, speaker, lambda: painted.append(1))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        self.assertTrue(console.set_speaking(server.server_address[1], False))
        self.assertFalse(settings["enabled"])
        self.assertTrue(speaker.stop.is_set())          # what is playing stops, nothing queued is spoken
        self.assertEqual(painted, [1])                  # the tray was told to repaint
        self.assertIn("speaking off", talkback.LOG.read_text(encoding="utf-8"))
        self.assertTrue(console.set_speaking(server.server_address[1], True))
        self.assertTrue(settings["enabled"])
        self.assertFalse(console.set_speaking(1, True))  # nothing is running on port 1


class SecondCopy(FolderSettings):
    """A copy started while one is already running: it says so, and never starts a second tray icon."""

    def settings_on(self, port):
        data = {"version": talkback.SETTINGS_VERSION, "enabled": True, "port": port, "primary": "openrouter",
                "sources": json.loads(json.dumps(talkback.SOURCES))}
        return self.settings(data)

    def listening(self):
        """A port that answers like a running copy does, which is how the second copy notices it."""

        class Answer(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Answer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_two_copies_cannot_share_the_port(self):
        """Windows lets a second socket bind a port another one holds; LocalServer turns that off."""
        first = talkback.LocalServer(("127.0.0.1", 0), None)
        self.addCleanup(first.server_close)
        with self.assertRaises(OSError):
            talkback.LocalServer(("127.0.0.1", first.server_address[1]), None)

    def test_the_second_copy_says_where_the_running_one_is(self):
        port = self.listening()
        self.settings_on(port)
        said, trays = [], []
        with mock.patch.object(talkback, "notice", said.append), \
                mock.patch.object(talkback, "tray", lambda *args: trays.append(args)):
            talkback.main()
        self.assertEqual(trays, [])                        # no second tray icon
        self.assertIn("already running", said[0])
        self.assertIn(str(port), said[0])
        self.assertIn("tray icon", said[0])
        self.assertIn("second copy stopped", talkback.LOG.read_text(encoding="utf-8"))

    def test_a_copy_started_by_a_sender_says_nothing(self):
        self.settings_on(self.listening())
        said = []
        with mock.patch.object(talkback, "notice", said.append), \
                mock.patch.object(talkback, "tray", lambda *args: self.fail("a second tray started")), \
                mock.patch.object(sys, "argv", ["talkback.py", "--quiet"]):
            talkback.main()
        self.assertEqual(said, [])                         # an agent's reply never waits on a dialog


@unittest.skipUnless(HAVE_CONSOLE, "the console needs tkinter")
class ConsoleWords(FolderSettings):
    def test_the_key_column_says_where_the_key_is(self):
        self.assertEqual(console.key_words({"id": "microsoft", "engine": "microsoft"}), "Free")
        source = openrouter("file:secrets/openrouter.local")
        self.assertEqual(console.key_words(source), "Not set")
        talkback.save_secret("openrouter", "sk-test-123")
        self.assertEqual(console.key_words(source), "Set")

    def test_the_two_voice_lines_read_as_one_sentence(self):
        self.settings()
        settings = talkback.Settings()
        self.assertEqual(console.voice_line(settings.source()), "bram (HeyGen | OpenRouter)")
        self.assertEqual(console.voice_line(console.free_source(settings)), "Guy (Microsoft Edge | Free)")

    def test_the_provider_table_is_alphabetical_and_leaves_microsoft_out(self):
        self.settings()
        rows = console.provider_rows(talkback.Settings())
        self.assertEqual([entry["label"] for entry in rows], ["Groq", "OpenRouter"])

    def test_a_new_provider_gets_a_clean_never_used_id(self):
        self.assertEqual(console.new_id("My Provider", set()), "my-provider")
        self.assertEqual(console.new_id("My Provider", {"my-provider"}), "my-provider-2")
        self.assertEqual(console.new_id("  ", set()), "source")
        self.assertRegex(console.new_id("x" * 80, set()), r"^[a-z0-9][a-z0-9-]{0,39}$")

    def test_the_test_line_names_the_voice(self):
        self.assertEqual(console.test_words("en-US-JennyNeural"), "Testing talkback in Jenny's Voice.")
        self.assertEqual(console.test_words("nadine"), "Testing talkback in nadine's Voice.")

    def test_both_palettes_have_every_colour(self):
        self.assertEqual(set(console.LIGHT), set(console.DARK))


@unittest.skipUnless(HAVE_CONSOLE, "the console needs tkinter")
class LoginShortcut(unittest.TestCase):
    def test_the_shortcut_is_in_the_startup_folder(self):
        with tempfile.TemporaryDirectory() as appdata, mock.patch.dict(os.environ, {"APPDATA": appdata}):
            link = console.startup_link()
            self.assertEqual(link.parent.name, "Startup")
            self.assertIn("Microsoft/Windows/Start Menu/Programs", link.as_posix())
            self.assertFalse(console.startup_enabled())
            link.parent.mkdir(parents=True)
            link.write_text("", encoding="utf-8")
            self.assertTrue(console.startup_enabled())

    def test_the_desktop_shortcut_is_named_after_the_app(self):
        link = console.desktop_link()
        self.assertEqual(link.name, "Agent Talkback.lnk")
        self.assertTrue(link.parent.is_dir(), f"{link.parent} is not a folder")

    def test_switching_the_desktop_shortcut_off_deletes_it(self):
        with tempfile.TemporaryDirectory() as folder:
            link = Path(folder) / "Agent Talkback.lnk"
            link.write_text("", encoding="utf-8")
            with mock.patch.object(console, "desktop_link", return_value=link):
                self.assertTrue(console.desktop_enabled())
                self.assertTrue(console.set_desktop(False))
                self.assertFalse(link.exists())

    def test_a_powershell_string_escapes_only_quotes(self):
        self.assertEqual(console._quoted(r"C:\it's here"), r"'C:\it''s here'")

    def test_the_script_is_passed_encoded_not_through_a_shell(self):
        self.assertEqual(base64.b64decode(console._encoded("Get-Date")).decode("utf-16-le"), "Get-Date")


class Hardening(FolderSettings):
    """What the release promises: a page cannot post here, a body is capped, the status page describes nothing."""

    def serving(self):
        self.settings()
        settings = talkback.Settings()
        self.items = queue.Queue()
        server = ThreadingHTTPServer(("127.0.0.1", 0), None)
        server.RequestHandlerClass = talkback.make_handler(settings, SimpleNamespace(items=self.items))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def raw(self, port, request):
        """A request written by hand, so a header urllib would not let us lie about is still tested."""
        with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
            sock.sendall(request)
            return sock.recv(200)

    def post(self, port, body, host=None, origin=None, content_type="application/json"):
        head = [f"POST /say HTTP/1.1", f"Host: {host or f'127.0.0.1:{port}'}",
                f"Content-Type: {content_type}", f"Content-Length: {len(body)}"]
        if origin:
            head.append(f"Origin: {origin}")
        return self.raw(port, ("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body)

    def status_of(self, reply):
        return int(reply.split(b"\r\n", 1)[0].split(b" ")[1])

    def test_a_page_cannot_make_the_program_speak(self):
        port = self.serving()
        body = b'{"text": "This should never be spoken aloud."}'
        self.assertEqual(self.status_of(self.post(port, body, origin="https://evil.example")), 403)
        self.assertEqual(self.status_of(self.post(port, body, content_type="text/plain")), 400)
        self.assertEqual(self.items.qsize(), 0)

    def test_a_foreign_host_is_refused(self):
        """DNS rebinding gives a page a name that resolves here; the Host header still says so."""
        port = self.serving()
        reply = self.post(port, b'{"text": "not spoken"}', host=f"rebind.example:{port}")
        self.assertEqual(self.status_of(reply), 403)
        self.assertEqual(self.items.qsize(), 0)

    def test_a_body_over_the_cap_is_refused(self):
        port = self.serving()
        head = (f"POST /say HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {talkback.MAX_BODY + 1}\r\n\r\n")
        self.assertEqual(self.status_of(self.raw(port, head.encode("ascii"))), 400)

    def test_a_full_queue_drops_rather_than_growing(self):
        port = self.serving()
        for _ in range(talkback.MAX_QUEUE):
            self.items.put({"text": "waiting"})
        self.assertEqual(self.status_of(self.post(port, b'{"text": "one reply too many"}')), 429)
        self.assertEqual(self.items.qsize(), talkback.MAX_QUEUE)

    def test_a_local_client_is_still_welcome(self):
        port = self.serving()
        body = b'{"text": "A reply that is allowed to speak."}'
        self.assertEqual(self.status_of(self.post(port, body, origin=f"http://127.0.0.1:{port}")), 202)
        self.assertEqual(self.items.get(timeout=2)["text"], "A reply that is allowed to speak.")

    def test_the_status_page_holds_no_key_paths(self):
        port = self.serving()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2) as response:
            text = response.read().decode("utf-8")
        self.assertNotIn("file:", text)
        self.assertNotIn("secrets", text)
        self.assertEqual({entry["key"] for entry in json.loads(text)["sources"]}, {"missing"})

    def test_the_log_never_carries_the_reply(self):
        said = "a sentence that must not end up in the log"
        self.assertEqual(talkback._without_reply(f"console: did not speak (Error('{said}'))", said),
                         "console: did not speak (Error('[reply text]'))")

    def test_a_broken_settings_file_is_set_aside(self):
        talkback.SETTINGS.write_text("{not json", encoding="utf-8")
        settings = talkback.Settings()
        self.assertTrue((self.dir / "settings.bad.json").exists())
        self.assertTrue(settings["sources"])
        self.assertIn("built-in settings", talkback.LOG.read_text(encoding="utf-8"))

    def test_settings_are_written_whole(self):
        self.settings()
        settings = talkback.Settings()
        settings["primary"] = "microsoft"
        self.assertFalse((self.dir / "settings.json.tmp").exists())
        self.assertEqual(json.loads(talkback.SETTINGS.read_text(encoding="utf-8"))["primary"], "microsoft")


if __name__ == "__main__":
    unittest.main()
