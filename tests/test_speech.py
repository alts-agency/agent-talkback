"""The spoken words are the product: these tests freeze them.

    python -m unittest discover -s tests -v

Fixture names carry the expected decision as their prefix: `header-*` is spoken
from the In short / From you header, `whole-*` whole, `first-*` first sentences.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import talkback  # noqa: E402

REPLIES = ROOT / "tests" / "replies"
HOW = {"header": "header", "whole": "whole", "first": "first sentences"}


class SpokenWords(unittest.TestCase):
    def test_golden_replies(self):
        """Every fixture says exactly what its .txt says, by the named route."""
        fixtures = sorted(REPLIES.glob("*.md"))
        self.assertTrue(fixtures, "no reply fixtures found")
        for reply in fixtures:
            prefix, _, _ = reply.stem.partition("-")
            expected = reply.with_suffix(".txt").read_text(encoding="utf-8").rstrip("\n")
            with self.subTest(reply=reply.name):
                said, how = talkback.what_to_say(reply.read_text(encoding="utf-8"))
                self.assertEqual(how, HOW[prefix])
                self.assertEqual(said, expected)

    def test_header_stops_at_the_end_of_each_paragraph(self):
        said, how = talkback.what_to_say("**In short:** One.\n\nNot spoken.\n\n**From you:** Two.")
        self.assertEqual((said, how), ("One. From you: Two.", "header"))

    def test_header_without_from_you_is_summary_only(self):
        said, how = talkback.what_to_say("**In short:** Just the summary, nothing else.")
        self.assertEqual((said, how), ("Just the summary, nothing else.", "header"))

    def test_plain_short_reply_is_spoken_whole(self):
        text = "The build is green again."
        self.assertEqual(talkback.what_to_say(text), (text, "whole"))

    def test_long_reply_falls_back_to_first_sentences(self):
        text = " ".join(["The parser now keeps its place in the file."] * 30)
        said, how = talkback.what_to_say(text)
        self.assertEqual(how, "first sentences")
        self.assertLessEqual(len(said), 220)

    def test_empty_reply_has_nothing_to_say(self):
        self.assertEqual(talkback.what_to_say("")[0], "")


class StripForSpeech(unittest.TestCase):
    def test_markdown_is_removed(self):
        self.assertEqual(talkback._strip_for_speech("Run `pip install talkback` and read **the docs**."),
                         "Run and read the docs.")

    def test_links_and_urls_go_but_their_words_stay(self):
        self.assertEqual(talkback._strip_for_speech("See [the log](https://example.com/a/b)."),
                         "See the log.")

    def test_acronyms_are_said_as_words(self):
        self.assertEqual(talkback._strip_for_speech("The API returns JSON."),
                         "The A P I returns jason.")

    def test_dashes_become_commas(self):
        self.assertEqual(talkback._strip_for_speech("Fast \u2014 and cheap."), "Fast, and cheap.")


class GroqPieces(unittest.TestCase):
    def test_every_piece_fits_the_200_character_request(self):
        text = " ".join(["This sentence is here to be split up sensibly."] * 12)
        pieces = talkback._pieces(text)
        self.assertTrue(all(pieces))
        self.assertTrue(all(len(piece) <= 200 for piece in pieces))
        self.assertEqual(" ".join(pieces), " ".join(text.split()))

    def test_short_text_is_one_piece(self):
        self.assertEqual(talkback._pieces("A short reply."), ["A short reply."])


if __name__ == "__main__":
    unittest.main()
