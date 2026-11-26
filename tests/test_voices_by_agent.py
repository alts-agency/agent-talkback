"""A voice per agent folder: the server keeps each talker it hears; the console names them and gives them voices.

No test makes a sound or touches the network.
"""
import json
import unittest
from unittest import mock

from test_sources import SettingsFile, talkback
import console

VOICES = ["bram", "nadine", "tabitha", "gareth", "noelle", "meredith", "sabrina", "lucian", "weston", "vincent"]
HEYGEN = {"name": "HeyGen", "model": "heygen/x", "voice": "noelle", "voices": VOICES}
A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"


ORPHEUS = {"name": "Orpheus", "model": "canopy/y", "voice": "autumn", "voices": ["autumn", "troy"]}


def choice(source="openrouter", voice_set="HeyGen", voice="bram"):
    return {"source": source, "set": voice_set, "voice": voice}


def two_providers(folder, **groq):
    """OpenRouter (primary, HeyGen) and Groq (Orpheus), both with a key, then Microsoft."""
    key = folder / "a.local"
    key.write_text("a-key", encoding="utf-8")
    first = {"id": "openrouter", "label": "OpenRouter", "engine": "openai", "base_url": "https://one.test/v1",
             "key": f"file:{key}", "set": "HeyGen", "sets": [HEYGEN]}
    second = dict({"id": "groq", "label": "Groq", "engine": "openai", "base_url": "https://two.test/v1",
                   "key": f"file:{key}", "set": "Orpheus", "sets": [ORPHEUS]}, **groq)
    return [first, second, talkback.SOURCES[2]]


def talker(talker_id=A, name="shop", folder="work/shop"):
    return {"id": talker_id, "name": name, "folder": folder}


def row(**parts):
    return dict({"name": "shop", "folder": "work/shop", "voice": None,
                 "first": "2026-11-27 01:00:00", "last": "2026-11-27 01:00:00"}, **parts)


class Pick(SettingsFile):
    def config(self, **groq):
        return self.settings({"version": talkback.SETTINGS_VERSION, "primary": "openrouter",
                              "sources": two_providers(self.dir, **groq)})

    def test_the_automatic_voice_comes_from_the_id_the_same_on_every_start(self):
        # fixed answers: Python's hash() would give others on the next start
        self.assertEqual([talkback.automatic_voice(i, HEYGEN) for i in (A, B)], ["bram", "lucian"])

    def test_the_hash_never_picks_the_primary_voice(self):
        for number in range(500):
            self.assertNotEqual(talkback.automatic_voice(f"id-{number}", HEYGEN), "noelle")

    def test_no_other_voice_is_the_primary_voice(self):
        for voices in ([], ["noelle"]):
            self.assertEqual(talkback.automatic_voice(A, dict(HEYGEN, voices=voices)), "noelle")

    def test_a_custom_voice_is_its_source_set_and_voice(self):
        entry, voice_set = talkback.custom_voice(self.config(), row(voice=choice("groq", "Orpheus", "troy")))
        self.assertEqual((entry["id"], voice_set["name"], voice_set["voice"], voice_set["model"]),
                         ("groq", "Orpheus", "troy", "canopy/y"))

    def test_a_choice_that_is_no_longer_there_is_no_custom_voice(self):
        settings = self.config()
        for gone in (None, choice("nope"), choice(voice_set="nope"), choice(voice="autumn")):
            self.assertIsNone(talkback.custom_voice(settings, row(voice=gone)), gone)
        self.assertIsNone(talkback.custom_voice(settings, None))
        self.assertIsNone(talkback.custom_voice(self.config(enabled=False), row(voice=choice("groq", "Orpheus", "troy"))))


class FromTheRequest(unittest.TestCase):
    def test_a_good_talker_is_kept_with_its_lengths_capped(self):
        clean = talkback.clean_talker({"id": A.upper(), "name": "n" * 200, "folder": "f" * 500, "voice": "bram"})
        self.assertEqual(clean, {"id": A, "name": "n" * talkback.MAX_NAME, "folder": "f" * 120})

    def test_anything_else_is_no_talker(self):
        for raw in (None, "shop", [], {}, {"id": "shop"}, {"id": 4}):
            self.assertIsNone(talkback.clean_talker(raw), raw)


class Table(SettingsFile):
    def rows(self):
        return talkback.Settings()["talkers"]

    def test_the_first_speech_registers_the_talker_with_the_files_name(self):
        self.settings().note_talker(talker(), "2026-11-27 01:00:00")
        self.assertEqual(self.rows(), {A: row()})

    def test_a_later_send_moves_only_last_heard(self):
        settings = self.settings()
        settings.note_talker(talker(), "2026-11-27 01:00:00")
        console_side = talkback.Settings()
        self.assertTrue(console_side.set_talker(A, voice=choice()))
        settings.note_talker(talker(name="renamed-in-file", folder="x/y"), "2026-11-28 09:00:00")
        self.assertEqual(self.rows()[A], row(voice=choice(), edited=True, last="2026-11-28 09:00:00"))

    def test_a_console_change_keeps_what_the_server_wrote_since(self):
        console_side = self.settings()
        console_side.note_talker(talker(), "2026-11-27 01:00:00")
        talkback.Settings().note_talker(talker(B, "blog"), "2026-11-27 02:00:00")
        console_side.set_talker(A, voice=choice())
        self.assertEqual(sorted(self.rows()), [A, B])
        self.assertEqual(self.rows()[A]["voice"], choice())

    def test_setting_an_unknown_talker_says_so(self):
        self.assertFalse(self.settings().set_talker(A, voice=choice()))

    def test_the_table_stays_small_and_never_drops_one_that_was_set(self):
        settings = self.settings()
        with mock.patch.object(talkback, "MAX_TALKERS", 3):
            ids = [f"00000000-0000-4000-8000-{number:012}" for number in range(5)]
            settings.note_talker(talker(ids[0]), "2026-01-00")
            settings.set_talker(ids[0], voice=choice())
            for number in range(1, 5):
                settings.note_talker(talker(ids[number]), f"2026-01-0{number}")
        self.assertEqual(sorted(self.rows()), [ids[0], ids[3], ids[4]])

    def test_unusable_rows_are_dropped_and_old_keys_go(self):
        self.settings({"version": talkback.SETTINGS_VERSION, "project_voices": {"blog": "vincent"},
                       "projects_heard": [], "talkers": {A: row(), B: "x", "nope": row()}})
        saved = json.loads(talkback.SETTINGS.read_text(encoding="utf-8"))
        self.assertEqual(saved["talkers"], {A: row()})
        self.assertNotIn("project_voices", saved)
        self.assertNotIn("projects_heard", saved)

    def migrated(self, **old):
        sources = two_providers(self.dir)
        self.settings({"version": talkback.SETTINGS_VERSION, "primary": "openrouter", "sources": sources,
                       "talkers": {A: dict(row(), **old)}})
        return self.rows()[A]

    def test_a_row_marked_primary_becomes_the_primary_voice_chosen(self):
        kept = self.migrated(primary=True, edited=True)
        self.assertEqual(kept, row(voice=choice(voice="noelle"), edited=True))

    def test_a_row_with_a_voice_name_becomes_that_voice_of_the_primary_set(self):
        self.assertEqual(self.migrated(voice="weston", primary=False), row(voice=choice(voice="weston")))

    def test_an_automatic_row_stays_automatic_and_a_new_choice_survives_a_load(self):
        self.assertEqual(self.migrated(primary=False), row())
        self.assertEqual(self.migrated(voice=choice("groq", "Orpheus", "troy"))["voice"],
                         choice("groq", "Orpheus", "troy"))

    def test_a_choice_missing_a_part_is_automatic(self):
        self.assertIsNone(self.migrated(voice={"source": "groq", "voice": "troy"})["voice"])

    def test_the_status_page_names_no_folder_and_no_python(self):
        settings = self.settings({"version": talkback.SETTINGS_VERSION, "talkers": {A: row()},
                                  "pythonw": r"C:\Python\pythonw.exe"})
        view = talkback.status_view(settings)
        self.assertNotIn("talkers", view)
        self.assertNotIn("pythonw", view)

    def test_the_stored_python_is_carried_through_every_load(self):
        self.settings({"version": talkback.SETTINGS_VERSION, "pythonw": r"C:\Python\pythonw.exe"}).reload()
        self.assertEqual(talkback.Settings()["pythonw"], r"C:\Python\pythonw.exe")


class Started(SettingsFile):
    """Talkback keeps the Python it runs on, so a send from any other Python can start it."""

    def test_the_running_pythonw_is_stored(self):
        settings = self.settings()
        with mock.patch.dict("sys.modules", {"pystray": mock.Mock(), "PIL": mock.Mock()}), \
                mock.patch.object(talkback.sys, "executable", r"C:\Py\python.exe"):
            talkback.keep_pythonw(settings)
        self.assertEqual(talkback.Settings()["pythonw"], r"C:\Py\pythonw.exe")

    def test_a_python_that_cannot_show_the_tray_is_not_stored(self):
        settings = self.settings()
        with mock.patch.dict("sys.modules", {"pystray": None}):
            talkback.keep_pythonw(settings)
        self.assertEqual(settings["pythonw"], "")


class Spoken(SettingsFile):
    REPLY = "**In short:** The build is green again. **From you:** Nothing."

    def setUp(self):
        super().setUp()
        self.config = self.settings({
            "version": talkback.SETTINGS_VERSION, "primary": "openrouter", "sources": two_providers(self.dir),
            "talkers": {B: row(voice=choice("groq", "Orpheus", "troy"))}})

    def speak(self, who, broken=(), **extra):
        """What was asked for, in order: (address, model, voice), or Microsoft's voice."""
        asked = []

        def keyed(text, entry, key, path):
            asked.append((entry["base_url"].split("/")[2], entry["model"], entry["voice"]))
            if asked[-1][0] in broken:
                raise RuntimeError("503")

        with mock.patch.multiple(talkback, play=lambda path, stop: "ended", openai_to_file=keyed,
                                 microsoft_to_file=lambda text, voice, rate, path: asked.append(voice)):
            talkback.Speaker(self.config).speak(self.REPLY, "test", talker=who, **extra)
        return asked

    def test_a_new_talker_speaks_automatically_and_is_registered(self):
        self.assertEqual(self.speak(talker()), [("one.test", "heygen/x", "bram")])
        self.assertEqual(talkback.Settings()["talkers"][A]["voice"], None)
        self.assertEqual(talkback.active_set(talkback.Settings().source())["voice"], "noelle")

    def test_a_custom_voice_speaks_through_its_own_source_and_set(self):
        self.assertEqual(self.speak(talker(B)), [("two.test", "canopy/y", "troy")])
        self.assertEqual(talkback.Settings()["primary"], "openrouter")

    def test_when_its_source_fails_the_usual_order_follows_each_in_its_own_voice(self):
        self.assertEqual(self.speak(talker(B), broken=("two.test",)),
                         [("two.test", "canopy/y", "troy"), ("one.test", "heygen/x", "noelle")])
        self.assertEqual(self.speak(talker(B), broken=("two.test", "one.test"))[-1], "en-US-GuyNeural")

    def test_a_custom_voice_that_is_gone_speaks_automatically(self):
        self.config.set_talker(B, voice=choice("groq", "Orpheus", "bram"))
        self.assertEqual(self.speak(talker(B)), [("one.test", "heygen/x", "lucian")])

    def test_no_talker_is_the_primary_voice(self):
        self.assertEqual(self.speak(None), [("one.test", "heygen/x", "noelle")])
        self.assertEqual(list(talkback.Settings()["talkers"]), [B])

    def test_a_given_voice_wins_over_a_custom_one_and_registers_nobody(self):
        self.assertEqual(self.speak(talker(B), set_name="HeyGen", voice="weston"),
                         [("one.test", "heygen/x", "weston")])
        self.assertEqual(self.speak(talker(), set_name="HeyGen", voice="weston")[0][2], "weston")
        self.assertNotIn(A, talkback.Settings()["talkers"])

    def test_the_fallback_speaks_as_before_and_the_talker_is_still_heard(self):
        self.assertEqual(self.speak(talker(), broken=("one.test",))[-1], "en-US-GuyNeural")
        self.assertIn(A, talkback.Settings()["talkers"])

    def test_nothing_spoken_registers_nobody(self):
        self.config["enabled"] = False
        self.speak(talker())
        self.assertNotIn(A, talkback.Settings()["talkers"])

    def test_the_queue_hands_the_talker_to_the_speaker(self):
        speaker = talkback.Speaker(self.config)
        speaker.items.put({"text": self.REPLY, "source": "test", "talker": talker()})
        with mock.patch.object(speaker, "speak", side_effect=SystemExit) as speak, self.assertRaises(SystemExit):
            speaker.run()                       # SystemExit ends the endless loop after the one item
        self.assertEqual(speak.call_args.args[-1], talker())


class ConsoleRows(SettingsFile):
    """What the Voices by Agent dialog shows."""

    def config(self, talkers):
        return self.settings({"version": talkback.SETTINGS_VERSION, "primary": "openrouter",
                              "sources": two_providers(self.dir), "talkers": talkers})

    def test_the_last_heard_is_first_each_with_the_voice_of_now(self):
        rows = console.talker_rows(self.config({
            A: row(last="2026-11-27 01:00:00"),
            B: row(folder="work/blog", voice=choice("groq", "Orpheus", "troy"), last="2026-11-27 03:00:00")}))
        self.assertEqual([(r["id"], r["folder"], r["on"], r["label"]) for r in rows],
                         [(B, "work/blog", True, "troy (Orpheus | Groq)"),
                          (A, "work/shop", False, "bram (HeyGen | OpenRouter)")])

    def test_a_choice_that_is_gone_shows_what_speaks_and_names_what_was_lost(self):
        only = console.talker_rows(self.config({A: row(voice=choice(voice="autumn"))}))[0]
        self.assertEqual((only["on"], only["label"], only["lacking"]),
                         (True, "bram (HeyGen | OpenRouter)", "autumn"))

    def test_a_talker_with_no_folder_shows_its_name(self):
        self.assertEqual(console.talker_rows(self.config({A: row(folder="")}))[0]["folder"], "shop")

    def test_switching_on_starts_from_the_voice_it_has_been_speaking_in(self):
        self.assertEqual(console.automatic_choice(self.config({}), A), choice(voice="bram"))


if __name__ == "__main__":
    unittest.main()
