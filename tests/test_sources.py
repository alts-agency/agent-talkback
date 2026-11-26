"""The source list: what it accepts, what it drops, and which source speaks.

    python -m unittest discover -s tests -v

No test makes a sound or touches the network.
"""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import talkback  # noqa: E402

V1 = {"enabled": True, "heygen_voice": "weston",
      "openrouter_key_file": r"C:\keys\openrouter.local",
      "groq_voice": None, "groq_key_file": r"C:\keys\groq.local",
      "microsoft_voice": "en-US-GuyNeural", "microsoft_rate": "+14%", "port": 47800}


class SettingsFile(unittest.TestCase):
    """A Settings that reads and writes inside a temporary folder."""

    def setUp(self):
        self.original = (talkback.SETTINGS, talkback.LOG, talkback.SECRETS, talkback.HERE)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        talkback.SETTINGS = self.dir / "settings.json"
        talkback.LOG = self.dir / "talkback.log"
        talkback.SECRETS = self.dir / "secrets"
        talkback.HERE = self.dir  # a key beside the program stays inside the temporary folder
        self.addCleanup(self.restore)

    def restore(self):
        talkback.SETTINGS, talkback.LOG, talkback.SECRETS, talkback.HERE = self.original

    def settings(self, data=None):
        if data is not None:
            talkback.SETTINGS.write_text(json.dumps(data), encoding="utf-8")
        return talkback.Settings()


EDGE = {"name": "Microsoft Edge", "voice": "en-US-GuyNeural"}


def source(**parts):
    """A source for the tests: one voice set unless the test says otherwise."""
    return {"id": "x", "engine": "microsoft", "sets": [dict(EDGE, **parts)]}


class SourceList(unittest.TestCase):
    def test_the_shipped_order_is_openrouter_groq_microsoft(self):
        sources, problems = talkback.clean_sources(talkback.SOURCES)
        self.assertEqual([source["id"] for source in sources], ["openrouter", "groq", "microsoft"])
        self.assertEqual([source["label"] for source in sources], ["OpenRouter", "Groq", "Free (for Windows)"])
        self.assertEqual(problems, [])

    def test_an_older_label_is_renamed_on_load(self):
        sources, _ = talkback.clean_sources([{"id": "microsoft", "label": "Default (Free)",
                                             "engine": "microsoft", "sets": [EDGE]}])
        self.assertEqual(sources[0]["label"], "Free (for Windows)")

    def test_the_shipped_three_layers_are_provider_set_voice(self):
        sources, _ = talkback.clean_sources(talkback.SOURCES)
        self.assertEqual([source["set"] for source in sources], ["HeyGen", "Orpheus", "Microsoft Edge"])
        self.assertEqual([talkback.active_set(source)["voice"] for source in sources],
                         ["bram", "autumn", "en-US-GuyNeural"])
        self.assertEqual(talkback.active_set(sources[1])["model"], "canopylabs/orpheus-v1-english")

    def test_the_public_cap_is_five(self):
        self.assertEqual(talkback.MAX_SOURCES, 5)

    def test_the_cap_keeps_the_first_five_and_says_how_to_raise_it(self):
        many = [{"id": f"s{n}", "engine": "microsoft", "sets": [EDGE]} for n in range(8)]
        sources, problems = talkback.clean_sources(many)
        self.assertEqual([source["id"] for source in sources], ["s0", "s1", "s2", "s3", "s4"])
        self.assertTrue(any("MAX_SOURCES" in problem for problem in problems))

    def test_bad_entries_are_dropped_with_a_reason(self):
        raw = [
            {"id": "ok", "engine": "microsoft", "sets": [EDGE]},
            {"id": "Bad Id", "engine": "microsoft", "sets": [EDGE]},
            {"id": "noengine", "engine": "elevenlabs", "sets": [EDGE]},
            {"id": "nosets", "engine": "microsoft"},
            {"id": "novoice", "engine": "microsoft", "sets": [{"name": "Microsoft Edge"}]},
            {"id": "nokey", "engine": "openai", "base_url": "https://e.test/v1",
             "sets": [{"name": "m", "model": "m", "voice": "v"}]},
            {"id": "norate", "engine": "microsoft", "rate": "fast", "sets": [EDGE]},
            {"id": "dupe", "engine": "microsoft", "sets": [EDGE]},
            {"id": "dupe", "engine": "microsoft", "sets": [EDGE]},
        ]
        sources, problems = talkback.clean_sources(raw)
        self.assertEqual([source["id"] for source in sources], ["ok", "dupe"])
        # bad id, unknown engine, no sets, no voice, no key, bad rate, duplicate id
        self.assertEqual(len(problems), 7)

    def test_a_key_must_be_a_reference_not_the_key_itself(self):
        entry, problems = talkback.clean_source({"id": "x", "engine": "openai", "base_url": "https://e.test/v1",
                                                 "sets": [{"name": "m", "model": "m", "voice": "v"}],
                                                 "key": "sk-live-abc"})
        self.assertIsNone(entry)
        self.assertIn("env:", " ".join(problems))

    def test_a_request_limit_is_kept_only_when_it_could_work(self):
        base = {"id": "x", "engine": "openai", "base_url": "https://e.test/v1", "key": "file:k.local",
                "sets": [{"name": "m", "model": "m", "voice": "v"}]}
        kept, _ = talkback.clean_source({**base, "sets": [dict(base["sets"][0], request_limit=200)]})
        dropped, _ = talkback.clean_source({**base, "sets": [dict(base["sets"][0], request_limit=12)]})
        self.assertEqual(kept["sets"][0]["request_limit"], 200)
        self.assertNotIn("request_limit", dropped["sets"][0])

    def test_one_source_holds_several_sets_each_with_its_own_model_and_limit(self):
        entry, problems = talkback.clean_source({
            "id": "openrouter", "engine": "openai", "base_url": "https://openrouter.ai/api/v1",
            "key": "file:k.local", "set": "Orpheus",
            "sets": [{"name": "HeyGen", "model": "heygen/x", "voice": "weston"},
                     {"name": "Orpheus", "model": "canopylabs/y", "voice": "autumn", "request_limit": 200}]})
        self.assertEqual(problems, [])
        self.assertEqual(entry["set"], "Orpheus")
        self.assertEqual([item["model"] for item in entry["sets"]], ["heygen/x", "canopylabs/y"])

    def test_an_unknown_active_set_falls_back_to_the_first(self):
        entry, _ = talkback.clean_source({**source(), "set": "Nope"})
        self.assertEqual(entry["set"], "Microsoft Edge")

    def test_a_set_is_dropped_with_its_reason_but_the_source_survives(self):
        entry, problems = talkback.clean_source({"id": "x", "engine": "microsoft",
                                                 "sets": [EDGE, {"name": "Broken"}]})
        self.assertEqual(len(entry["sets"]), 1)
        self.assertTrue(any("Broken" in problem for problem in problems))

    def test_the_set_cap_is_eight(self):
        self.assertEqual(talkback.MAX_SETS, 8)
        sets = [{"name": f"set{n}", "voice": "v"} for n in range(10)]
        entry, problems = talkback.clean_source({"id": "x", "engine": "microsoft", "sets": sets})
        self.assertEqual(len(entry["sets"]), talkback.MAX_SETS)
        self.assertTrue(any("MAX_SETS" in problem for problem in problems))


class Keys(unittest.TestCase):
    def test_a_key_can_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"TALKBACK_TEST_KEY": "  from-env  "}):
            self.assertEqual(talkback.key_for({"key": "env:TALKBACK_TEST_KEY"}), "from-env")

    def test_a_key_can_come_from_a_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "k.local"
            path.write_text("from-file\n", encoding="utf-8")
            self.assertEqual(talkback.key_for({"key": f"file:{path}"}), "from-file")

    def test_a_missing_key_is_not_an_error(self):
        self.assertEqual(talkback.key_for({"key": "file:nowhere-at-all.local"}), "")
        self.assertFalse(talkback.can_speak({"engine": "openai", "key": "file:nowhere-at-all.local"}))
        self.assertTrue(talkback.can_speak({"engine": "microsoft", "voice": "x"}))


class SettingsUpgrade(SettingsFile):
    def test_version_1_keeps_the_chosen_voice_and_key_file(self):
        settings = self.settings(V1)
        self.assertEqual(settings["version"], talkback.SETTINGS_VERSION)
        openrouter = settings.source("openrouter")
        self.assertEqual(openrouter["set"], "HeyGen")
        self.assertEqual(talkback.active_set(openrouter)["voice"], "weston")
        self.assertEqual(openrouter["key"], "file:" + V1["openrouter_key_file"])
        self.assertEqual(talkback.active_set(settings.source("microsoft"))["voice"], "en-US-GuyNeural")
        saved = json.loads(talkback.SETTINGS.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], talkback.SETTINGS_VERSION)
        self.assertNotIn("heygen_voice", saved)

    def test_a_version_2_source_becomes_one_voice_set(self):
        version_2 = {"version": 2, "primary": "groq", "sources": [
            {"id": "groq", "label": "Groq \u00b7 Orpheus", "engine": "openai",
             "base_url": "https://api.groq.com/openai/v1", "model": "canopylabs/orpheus-v1-english",
             "voice": "diana", "voices": ["diana"], "key": "file:g.local", "request_limit": 200},
            {"id": "microsoft", "label": "Microsoft \u00b7 Guy", "engine": "microsoft",
             "voice": "en-US-AriaNeural", "voices": ["en-US-AriaNeural"], "rate": "+10%"}]}
        settings = self.settings(version_2)
        groq = settings.source("groq")
        self.assertEqual(groq["set"], "Orpheus")
        self.assertEqual(talkback.active_set(groq)["voice"], "diana")
        self.assertEqual(talkback.active_set(groq)["request_limit"], 200)
        microsoft = settings.source("microsoft")
        self.assertEqual(microsoft["set"], "Microsoft Edge")
        self.assertEqual(talkback.active_set(microsoft)["voice"], "en-US-AriaNeural")
        self.assertEqual(microsoft["rate"], "+10%")

    def test_a_flat_row_is_read_as_one_voice_set_whatever_the_version_says(self):
        flat = {"version": 3, "primary": "microsoft", "sources": [
            {"id": "microsoft", "label": "Default (Free)", "engine": "microsoft",
             "voice": "en-US-GuyNeural", "rate": "+14%", "enabled": True}]}
        settings = self.settings(flat)
        microsoft = settings.source("microsoft")
        self.assertEqual(settings["primary"], "microsoft")
        self.assertEqual(microsoft["set"], "Microsoft Edge")
        self.assertEqual(talkback.active_set(microsoft)["voice"], "en-US-GuyNeural")
        self.assertEqual(settings.problems.count("no sources in settings.json; the built-in ones are in use"), 0)

    def test_a_corrupt_file_falls_back_to_the_built_in_sources(self):
        talkback.SETTINGS.write_text("{not json", encoding="utf-8")
        settings = talkback.Settings()
        self.assertTrue(settings.problems)
        self.assertEqual([s["id"] for s in settings["sources"]], ["openrouter", "groq", "microsoft"])

    def test_an_unusable_port_falls_back(self):
        settings = self.settings({"version": 2, "port": 99999, "sources": talkback.SOURCES})
        self.assertEqual(settings["port"], 47800)

    def test_speaking_always_starts_on(self):
        settings = self.settings({"version": 2, "enabled": False, "sources": talkback.SOURCES})
        self.assertTrue(settings["enabled"])


class WhichSourceSpeaks(SettingsFile):
    def test_picking_a_voice_makes_it_speak_first_and_saves_it(self):
        settings = self.settings()
        settings.pick_voice("groq", "Orpheus", "diana")
        self.assertEqual(settings["primary"], "groq")
        self.assertEqual(talkback.active_set(settings.source("groq"))["voice"], "diana")
        self.assertEqual(talkback.speech_order(settings)[0]["id"], "groq")
        saved = json.loads(talkback.SETTINGS.read_text(encoding="utf-8"))
        self.assertEqual(saved["primary"], "groq")

    def test_the_rest_keep_list_order_behind_the_picked_source(self):
        settings = self.settings()
        settings.pick_voice("microsoft", "Microsoft Edge", "en-US-AriaNeural")
        self.assertEqual([s["id"] for s in talkback.speech_order(settings)], ["microsoft", "openrouter", "groq"])

    def test_the_free_windows_voice_is_the_second_one_tried(self):
        settings = self.settings()
        self.assertEqual([s["id"] for s in talkback.speech_order(settings)],
                         ["openrouter", "microsoft", "groq"])

    def test_the_secondary_voice_is_chosen_without_becoming_the_primary(self):
        settings = self.settings()
        settings.set_voice("microsoft", "Microsoft Edge", "en-US-JennyNeural")
        self.assertEqual(settings["primary"], "openrouter")
        self.assertEqual(talkback.active_set(settings.source("microsoft"))["voice"], "en-US-JennyNeural")

    def test_a_voice_is_named_the_way_a_person_says_it(self):
        self.assertEqual(talkback.voice_name("en-US-GuyNeural"), "Guy")
        self.assertEqual(talkback.voice_name("en-GB-SoniaNeural"), "Sonia")
        self.assertEqual(talkback.voice_name("en-US-AvaMultilingualNeural"), "Ava")
        self.assertEqual(talkback.voice_name("nadine"), "nadine")

    def test_picking_a_voice_in_another_set_moves_that_set_into_use(self):
        two_sets = {"id": "openrouter", "engine": "openai", "base_url": "https://openrouter.ai/api/v1",
                    "key": "file:nowhere.local", "set": "HeyGen",
                    "sets": [{"name": "HeyGen", "model": "heygen/x", "voice": "weston"},
                             {"name": "Orpheus", "model": "canopylabs/y", "voice": "autumn"}]}
        settings = self.settings({"version": talkback.SETTINGS_VERSION, "sources": [two_sets],
                                  "primary": "openrouter"})
        settings.pick_voice("openrouter", "Orpheus", "daniel")
        entry = settings.source("openrouter")
        self.assertEqual(entry["set"], "Orpheus")
        self.assertEqual(talkback.active_set(entry)["voice"], "daniel")
        self.assertEqual(entry["sets"][0]["voice"], "weston")  # the other set is untouched

    def test_disabled_sources_are_not_tried(self):
        raw = [{"id": "microsoft", "engine": "microsoft", "sets": [EDGE]},
               {"id": "parked", "engine": "microsoft", "sets": [EDGE], "enabled": False}]
        settings = self.settings({"version": talkback.SETTINGS_VERSION, "sources": raw, "primary": "parked"})
        self.assertEqual([s["id"] for s in talkback.speech_order(settings)], ["microsoft"])

    def test_a_source_without_a_key_stays_in_the_menu_but_is_marked(self):
        settings = self.settings()
        rows = talkback.voice_menu(settings)
        self.assertEqual([row["id"] for row in rows], ["openrouter", "groq"])   # Microsoft is the backup, not a choice
        self.assertTrue(rows[0]["label"].endswith("(no key)"))
        self.assertFalse(any(row["id"] == "microsoft" for row in rows))

    def test_the_tray_offers_the_voice_in_use_even_if_it_is_not_listed(self):
        settings = self.settings()
        talkback.active_set(settings.source("openrouter"))["voice"] = "some-new-voice"
        self.assertIn("some-new-voice", talkback.voice_menu(settings)[0]["sets"][0]["voices"])


try:
    import pystray  # noqa: F401
    HAVE_TRAY = True
except Exception:  # no pystray here (or no display): the data-level tests still run
    HAVE_TRAY = False


@unittest.skipUnless(HAVE_TRAY, "pystray is not installed here")
class TrayMenu(SettingsFile):
    def actions(self, picked=None):
        def noop(icon, item):
            pass

        def pick(source_id, set_name, voice):
            def choose(icon, item):
                (picked if picked is not None else []).append((source_id, set_name, voice))
            return choose

        return {"nothing": noop, "on": noop, "off": noop, "toggle": noop, "pick": pick,
                "open": noop, "reload": noop, "stop": noop, "quit": noop}

    def item(self, menu, text):
        """A menu item by its words, so a test names what it clicks instead of counting separators."""
        return next(item for item in menu.items if item.text == text)

    def test_the_menu_is_segmented_and_the_tick_says_whether_it_speaks(self):
        settings = self.settings()
        menu = talkback.tray_menu(settings, self.actions())
        self.assertEqual([item.text for item in menu.items],
                         ["Toggle", "Agent Talkback", "Running", "- - - -", "Primary Voice", "Open console",
                          "- - - -", "Interrupt current", "Reload settings", "- - - -", "Quit"])
        self.assertTrue(self.item(menu, "Agent Talkback").checked)     # a tick while it is speaking
        self.assertFalse(self.item(menu, "Running").enabled)           # the status line is a label, not a command

        settings["enabled"] = False
        menu = talkback.tray_menu(settings, self.actions())             # the words are made as the menu opens
        self.assertFalse(self.item(menu, "Agent Talkback").checked)
        self.assertIn("Not running", [item.text for item in menu.items])

    def test_the_primary_voice_menu_holds_every_provider_and_no_microsoft(self):
        settings = self.settings()
        menu = talkback.tray_menu(settings, self.actions())
        voice = self.item(menu, "Primary Voice").submenu
        self.assertEqual([item.text for item in voice.items],
                         [row["label"] for row in talkback.voice_menu(settings)])
        self.assertNotIn("Free (for Windows)", [item.text for item in voice.items])

    def test_the_provider_that_speaks_is_ticked_all_the_way_down(self):
        settings = self.settings()
        menu = talkback.tray_menu(settings, self.actions())
        voice = self.item(menu, "Primary Voice").submenu
        rows = talkback.voice_menu(settings)
        primary, other = rows[0], rows[1]                    # the labels carry "(no key)" here, so read them
        self.assertTrue(self.item(voice, primary["label"]).checked)
        self.assertFalse(self.item(voice, other["label"]).checked)
        other_menu = self.item(voice, other["label"])
        self.assertEqual([item.text for item in other_menu.submenu.items],
                         [voice_set["name"] for voice_set in other["sets"]])
        self.assertEqual([item.text for item in other_menu.submenu.items[0].submenu.items],
                         [talkback.voice_name(name) for name in other["sets"][0]["voices"]])

    def test_a_voice_is_picked_with_its_set(self):
        settings = self.settings()
        picked = []
        menu = talkback.tray_menu(settings, self.actions(picked))
        rows = talkback.voice_menu(settings)
        provider = self.item(self.item(menu, "Primary Voice").submenu, rows[1]["label"])
        first_set = provider.submenu.items[0]
        first_set.submenu.items[0](None)  # a menu item is called with the icon
        self.assertEqual(picked, [(rows[1]["id"], first_set.text, rows[1]["sets"][0]["voices"][0])])


class Speaking(SettingsFile):
    """The layering itself, with the audio stubbed: no sound, no network."""

    REPLY = "**In short:** The build is green again. **From you:** Nothing."

    def speak(self, settings, only="", **patches):
        speaker = talkback.Speaker(settings)
        with mock.patch.multiple(talkback, play=lambda path, stop: "ended", **patches):
            speaker.speak(self.REPLY, "test", only)
        return speaker

    def test_a_source_without_a_key_is_skipped_and_microsoft_speaks(self):
        spoken = []
        self.speak(self.settings(), microsoft_to_file=lambda *args: spoken.append(args[1]))
        self.assertEqual(spoken, ["en-US-GuyNeural"])
        log = (self.dir / "talkback.log").read_text(encoding="utf-8")
        self.assertIn("skipped openrouter (no key)", log)
        self.assertIn("microsoft/Microsoft Edge en-US-GuyNeural", log)

    def test_a_test_line_speaks_with_the_named_source_only(self):
        spoken = []
        self.speak(self.settings(), only="microsoft", microsoft_to_file=lambda *args: spoken.append(args[1]))
        self.assertEqual(spoken, ["en-US-GuyNeural"])

    def test_the_free_voice_speaks_before_another_keyed_provider(self):
        """The secondary voice is the fallback, so a keyed Groq does not jump in front of it."""
        key = self.dir / "groq.local"
        key.write_text("a-key", encoding="utf-8")
        sources = [talkback.SOURCES[0], dict(talkback.SOURCES[1], key=f"file:{key}"), talkback.SOURCES[2]]
        settings = self.settings({"version": talkback.SETTINGS_VERSION, "sources": sources,
                                  "primary": "openrouter"})
        spoken = []
        self.speak(settings, microsoft_to_file=lambda *args: spoken.append(args[1]),
                   openai_to_file=lambda *args: self.fail("Groq should not speak before the free voice"))
        self.assertEqual(spoken, ["en-US-GuyNeural"])

    def test_a_source_with_a_key_speaks_before_microsoft(self):
        key = self.dir / "openrouter.local"
        key.write_text("a-key", encoding="utf-8")
        sources = [dict(talkback.SOURCES[0], key=f"file:{key}"), talkback.SOURCES[2]]
        settings = self.settings({"version": talkback.SETTINGS_VERSION, "sources": sources,
                                  "primary": "openrouter"})
        spoken = []
        self.speak(settings,
                   openai_to_file=lambda text, entry, key_value, path: spoken.append((entry["model"], key_value)),
                   microsoft_to_file=lambda *args: self.fail("Microsoft should not speak when OpenRouter works"))
        self.assertEqual(spoken, [("heygen/heygen-voice-1-turbo:free", "a-key")])

    def two_sets(self):
        key = self.dir / "openrouter.local"
        key.write_text("a-key", encoding="utf-8")
        return {"id": "openrouter", "label": "OpenRouter", "engine": "openai",
                "base_url": "https://openrouter.ai/api/v1", "key": f"file:{key}", "set": "HeyGen",
                "sets": [{"name": "HeyGen", "model": "heygen/x", "voice": "weston"},
                         {"name": "Orpheus", "model": "canopylabs/y", "voice": "autumn",
                          "request_limit": 200}]}

    def test_the_limit_of_another_set_is_not_applied(self):
        settings = self.settings({"version": talkback.SETTINGS_VERSION, "primary": "openrouter",
                                  "sources": [self.two_sets(), talkback.SOURCES[2]]})
        seen = {}
        self.speak(settings, openai_to_file=lambda text, entry, key_value, path: seen.update(entry),
                   microsoft_to_file=lambda *args: self.fail("Microsoft should not speak"))
        self.assertEqual((seen["model"], seen["voice"]), ("heygen/x", "weston"))
        self.assertNotIn("request_limit", seen)

    def test_the_limit_of_the_set_in_use_is_applied(self):
        source = self.two_sets()
        source["set"] = "Orpheus"
        settings = self.settings({"version": talkback.SETTINGS_VERSION, "primary": "openrouter",
                                  "sources": [source, talkback.SOURCES[2]]})
        seen = {}
        self.speak(settings, openai_to_file=lambda text, entry, key_value, path: seen.update(entry),
                   microsoft_to_file=lambda *args: self.fail("Microsoft should not speak"))
        self.assertEqual((seen["model"], seen["voice"], seen["request_limit"]),
                         ("canopylabs/y", "autumn", 200))

    def test_a_failing_source_falls_through_to_the_next(self):
        key = self.dir / "openrouter.local"
        key.write_text("a-key", encoding="utf-8")
        sources = [dict(talkback.SOURCES[0], key=f"file:{key}"), talkback.SOURCES[2]]
        settings = self.settings({"version": 2, "sources": sources, "primary": "openrouter"})

        def broken(*args):
            raise RuntimeError("503 from the endpoint")

        spoken = []
        self.speak(settings, openai_to_file=broken, microsoft_to_file=lambda *args: spoken.append(args[1]))
        self.assertEqual(spoken, ["en-US-GuyNeural"])
        log = (self.dir / "talkback.log").read_text(encoding="utf-8")
        self.assertIn("openrouter/HeyGen did not speak", log)


class SpeechRequests(SettingsFile):
    def openai(self, **set_keys):
        return {"id": "openrouter", "engine": "openai", "base_url": "https://example.test/v1",
                "key": "env:NOWHERE", "set": "HeyGen",
                "sets": [{"name": "HeyGen", "model": "heygen/x", "voice": "nadine", **set_keys}]}

    def test_a_request_asks_for_wav_unless_the_set_says_otherwise(self):
        entry = talkback.request_entry(self.openai(), talkback.active_set(self.openai()))
        self.assertEqual(talkback._speech_request(entry, "hello")[1]["response_format"], "wav")
        entry = talkback.request_entry(self.openai(format="pcm"), talkback.active_set(self.openai(format="pcm")))
        self.assertEqual(talkback._speech_request(entry, "hello")[1]["response_format"], "pcm")

    def test_the_format_belongs_to_the_set_and_is_checked(self):
        source, _ = talkback.clean_source(self.openai(format="pcm"))
        self.assertEqual(talkback.active_set(source)["format"], "pcm")
        source, problems = talkback.clean_source(self.openai(format="mp3"))
        self.assertIsNone(source)
        self.assertEqual(problems, ["openrouter: HeyGen: format must be one of wav, pcm"])

    def test_a_refused_format_is_asked_again_the_other_way(self):
        asked = []

        class Answer:
            headers = {"Content-Type": "audio/pcm;rate=24000;channels=1"}

            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def endpoint(request, **options):
            asked.append(json.loads(request.data)["response_format"])
            if asked[-1] == "wav":  # what HeyGen Voice answers: it speaks pcm only
                raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(
                    b'{"error": {"message": "HeyGen Voice only supports response_format pcm"}}'))
            return Answer(b"\x00\x00" * 240)

        entry = talkback.request_entry(self.openai(), talkback.active_set(self.openai()))
        path = self.dir / "speech.wav"
        with mock.patch("urllib.request.urlopen", side_effect=endpoint):
            talkback.openai_to_file("hello", entry, "a-key", str(path))
        self.assertEqual(asked, ["wav", "pcm"])
        with wave.open(str(path)) as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels()), (24000, 1))
            self.assertEqual(wav.getnframes(), 240)

    def test_any_other_error_is_not_retried(self):
        def broken(request, **options):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"no"))

        entry = talkback.request_entry(self.openai(), talkback.active_set(self.openai()))
        with mock.patch("urllib.request.urlopen", side_effect=broken) as urlopen:
            with self.assertRaises(urllib.error.HTTPError):
                talkback.openai_to_file("hello", entry, "a-key", str(self.dir / "speech.wav"))
        self.assertEqual(urlopen.call_count, 1)


class Cartesia(unittest.TestCase):
    ENTRY = {"engine": "cartesia", "base_url": "https://api.cartesia.ai/tts/sse", "model": "sonic-3.6",
             "voice": "Daniel"}
    ID = "33333333-3333-4333-8333-333333333333"

    def test_the_engine_is_a_source_like_any_keyed_one(self):
        entry, problems = talkback.clean_source({
            "id": "cartesia", "engine": "cartesia", "base_url": "https://api.cartesia.ai", "key": "file:k.local",
            "sets": [{"name": "Default", "voice": "Daniel", "model": "sonic-3.6"}]})
        self.assertEqual((entry["engine"], problems), ("cartesia", []))

    def test_the_request_goes_to_tts_bytes_whatever_path_the_address_has(self):
        with mock.patch.object(talkback, "cartesia_voice_id", return_value=self.ID):
            url, payload = talkback._cartesia_request(self.ENTRY, "Hello there.", "k")
        self.assertEqual(url, "https://api.cartesia.ai/tts/bytes")
        self.assertEqual((payload["model_id"], payload["transcript"], payload["voice"]),
                         ("sonic-3.6", "Hello there.", {"id": self.ID}))
        self.assertEqual(payload["output_format"]["container"], "wav")

    def test_a_voice_id_passes_through_and_a_name_is_looked_up_once(self):
        self.assertEqual(talkback.cartesia_voice_id(dict(self.ENTRY, voice=self.ID), "k"), self.ID)
        body = json.dumps({"data": [{"id": "other", "name": "Daniela"}, {"id": self.ID, "name": "Daniel"}]}).encode()
        talkback._CARTESIA_VOICES.clear()
        with mock.patch("urllib.request.urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = body
            first = talkback.cartesia_voice_id(self.ENTRY, "k")
            second = talkback.cartesia_voice_id(self.ENTRY, "k")
        self.assertEqual((first, second, opened.call_count), (self.ID, self.ID, 1))
        talkback._CARTESIA_VOICES.clear()


class Prices(unittest.TestCase):
    def test_a_price_is_said_after_its_number(self):
        self.assertEqual(talkback._strip_for_speech("It costs \u20ac5 today."), "It costs 5 euros today.")
        self.assertEqual(talkback._strip_for_speech("That was $1,200."), "That was 1,200 dollars.")

    def test_a_price_written_after_the_number_still_reads(self):
        self.assertEqual(talkback._strip_for_speech("Billed 5\u20ac monthly."), "Billed 5 euros monthly.")

    def test_a_percentage_still_reads(self):
        self.assertEqual(talkback._strip_for_speech("Up 14% this week."), "Up 14 percent this week.")


if __name__ == "__main__":
    unittest.main()
