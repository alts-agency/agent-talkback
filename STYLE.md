# Talkback style
One voice for docs, code, data and artwork, so someone adding their own layer can guess how the rest is
written. When this file and existing code disagree, the code wins and this file gets fixed in the same commit.

## Voice for docs
- Second person, present tense, short sentences. Say what a thing does, then how to use it.
- British spelling. No marketing adjectives, no "simply", no "just". Name the thing instead of "it".
- One sentence at the top says what the program is for. Commands come before feature lists.
- Every doc that a stranger reads states the platform (Windows 10/11) and what leaves the machine.
- Do not describe a feature the code does not have. Line numbers, versions and paths go stale: name the symbol.

## Code
- Python 3.10+. Standard library first. A new dependency needs a reason in `AGENTS.md`, not a comment.
- One small program. Split a module only when the file stops being readable in one sitting.
- Four spaces, 120 columns, `# ── Section name ──────` banners, a module docstring that says what and why.
- `lower_snake` names. A function is named for what it returns (`what_to_say`), a boolean for the question it
  answers (`enabled`). No `data`, `info`, `handle` on their own.
- Comments explain why, never what. No commented-out code, no dead branches kept "just in case".
- Failure is logged in one line and survived: a dead provider, a missing key or an unreadable settings file must
  never end the process silently.
- Anything Windows-specific lives behind one function per concern, so the rest can be read and tested anywhere.

## Layers: sources and providers
The point of the design is that people add their own without editing ours.
- A **source** is data: `id`, `label`, `engine`, `base_url`, `key`, `set`, `enabled` (and `rate` for microsoft),
  with `sets` inside it: each voice set is `name`, `model`, `voice`, `voices`, `request_limit`. Users add them in
  `settings.json`; list order is priority, the picked source speaks first, and the default Microsoft one is always
  last so a voice remains. Adding a source or a voice set needs no code.
- A **new endpoint family** is code: one request function and one entry in `ENGINES`, about 20 lines. Anything
  OpenAI-compatible needs none, because `engine: "openai"` already covers it.
- Adding a source or an engine never changes the tray, the HTTP API or the spoken-words rules.
- A shipped build carries at most 5 sources (`MAX_SOURCES`) and 8 voice sets in each (`MAX_SETS`), a guard against a
  script adding rows by mistake.
- **Keys live in `secrets/`**, one file per source named after its id. `console.py` is where a person puts one; the
  folder is gitignored. A key never appears in settings, a doc, a log line, an error message or a reply, and no code
  echoes one back.
- The setup console is the only window the program has. It edits `settings.json`, never a second copy of it, and
  asks the running program to reload instead of writing to another process. It is built from `tkinter` widgets and
  two palettes (`LIGHT`, `DARK`) so it still opens before `pip install`; a theming package is a dependency the first
  run cannot have. Colours come from the palette, never from a literal in a widget call.
- The code never calls a source free or paid: that lives in the README, where it can be corrected when a provider
  changes its terms. Microsoft is the only one the README calls free, because it needs no key.

## Data formats
- Settings keys are `lower_snake` with one meaning each. A new key needs a default, a line in the README, and
  must be harmless when absent.
- HTTP: JSON in, JSON out, lowercase keys, `202` for accepted. The API never returns key paths, key values or
  anything else that describes the machine.
- The log is one line per event: `YYYY-MM-DD HH:MM:SS <source>: <what happened>`. Reply text never appears in it,
  including inside an error message.
- Asset and doc filenames use `kebab-case`; Python files use `lower_snake`.

## The mark
- The mark is the chevron of the Alts Agency mark (`mark-white.svg` in the Alts Agency brand kit): its own path,
  parsed by `chevron_points()` in `talkback.py`, with the lockup's `\` stroke left out so the chevron reads as a play
  sign. `tools/build_assets.py` draws every icon asset from that path; `assets/social-preview.png` is a render of
  `social/preview.html` at 1280x640, built in HTML on purpose so the GitHub card is typography, not a drawing.
  Assets are never hand-edited.
- Palette: black `#000000` badge, white `#FFFFFF` chevron, grey `#75797F` while speaking is off.
- State is carried by colour and by the tray title, so a greyscale screenshot and a screen reader both still say
  whether it is on.
- Everything is drawn at 4× and scaled down; 16 px must read in a taskbar without explanation.
- The mark exists in: the tray, the window and taskbar icon, `README.md`, the GitHub social preview
  (`assets/social-preview.png`, rendered from `social/preview.html`) and `assets/talkback.ico` for any packaged
  launcher.

## Tests
- Spoken words are the product: fixtures live in `tests/replies/` and are named for the decision they should take
  (`header-`, `whole-`, `first-`). The `.txt` beside each fixture is the contract; changing it is a product change.
- Unit tests cover pure functions. Audio, network and playback stay behind one boundary and are not unit tested.
- `python -m unittest discover -s tests -v` passes before every commit. No test makes a sound.

## Commits and changelog
- `Talkback: <imperative summary>`, one verified change per commit, no trailers.
- `CHANGELOG.md` records what a user would notice, under Added, Changed, Fixed and Removed.

## Attribution
The agency name lives in `README.md`, the window header and the console's website link; the icon is the brand
chevron. Neither the log nor a spoken reply ever mentions the agency.
