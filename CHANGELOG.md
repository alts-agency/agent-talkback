# Changelog
What a user of Talkback would notice. Format follows Keep a Changelog; versions follow semver.

## [Unreleased]

## [0.2.0] - 2026-11-12
The first public release.

### Added
- `send.py` reads Codex's `notify` call, which passes the reply as a JSON argument (`last-assistant-message`),
  and Hermes Agent's `post_llm_call` shell hook (`extra.assistant_response`). README has copy-paste setups for
  Claude Code, Codex and Hermes Agent.
- The app mark is the chevron from the Alts Agency mark: its own path, with the lockup's
  `\` stroke left out so the chevron reads as a play sign, drawn white on black at 4x and scaled down. The tray, the
  window, the taskbar and every generated asset use it.
- The console keeps Windows' own black and white. Alts Agency's green (`#2E6B60` in light, `#8FCFC2` in dark) is only
  an accent: the website link, the primary voice's name, a saved key's `Set`, a focused input, a switch that is on, and
  the tint behind the voice in use.
- The voice list scrolls with a slim rounded thumb that appears only when there is something to scroll, instead of the
  Windows scrollbar with its arrows and trough.
- A voice row is now a long box plus **Test** on its right: clicking the box chooses the voice, **Test** only speaks
  that voice for that one line (the program copies the set for it) and never changes what speaks next. The chosen row
  says `(Primary)` or `(Backup)` in the accent colour.
- The tray menu is rebuilt to match the system: `Agent Talkback` with a tick while it speaks (a click switches it on
  or off) and `Running` / `Not running` under it, then `Primary Voice`, `Open console`, then `Interrupt current`,
  `Reload settings`, then `Quit`. The provider and the voice in use are ticked at every level, so the active voice is
  visible from the top, and the menu itself is dark on a dark Windows.
- Microsoft's voice is out of the tray menu: the backup voice is chosen in the console, because a reply falling back
  to it is not a choice.
- The console's two switches are stacked in one card under the providers, with the alts.agency link at the top right,
  and the taskbar shows the project's icon instead of Python's.
- Starting Agent Talkback while it is already running now says so — the second copy points at the tray icon instead of
  disappearing without a word. A copy started by `send.py` stays quiet (`--quiet`), so an agent's reply never waits on
  a message box.
- Two copies can no longer share the port. On Windows a second copy bound the same port anyway (`HTTPServer` sets
  `SO_REUSEADDR`, and Windows allows the duplicate), which is how two tray icons could run and split the replies
  between them. The program now asks `/status` before binding and refuses to share the port at all.
- **Talkback Voice** is a switch at the top right, where the "Running · voice on" pill used to sit, with the
  alts.agency link under it: it turns speaking on and off like the tray icon does, and the tray repaints itself to
  match (`POST /enabled`). The pill is gone.
- The window has a fixed size now: it cannot be resized.
- In the provider table only the buttons are clickable — **Set as primary** and **Edit** both use the same bordered
  button as **Change** above — and a row no longer lights up under the pointer.
- New words: "Your agent will use this model first, if available." under Primary voice, and the longer Microsoft
  Edge note under Backup voice (takes over when the primary fails, needs Microsoft's servers, no key required).
- The program is **Agent Talkback**: window, title bar, tray tooltip and tray menu say so, the window's subtitle says
  what it is, and **alts.agency ↗** at the very bottom opens https://alts.agency.
- Providers are listed alphabetically and exactly one is primary: it says **(Primary)** next to its name, and every
  other row has **Set as primary**. Dragging rows is gone — the order the window showed was never the order that
  decided who spoke.
- **Start with Windows** is a switch in a card of its own instead of a checkbox from 1995.
- The tray menu is segmented into the name, the voice controls, the actions and Quit. **Stop current reply** is now
  **Interrupt current**, and **Open Talkback** is **Open Agent Talkback**.
- Gone from the bottom of the window: "All packages installed", "Settings saved · keys in secrets/ · port 47800".
  That line now speaks only when something is wrong or a package is missing.
- Every table is one grid for its head and its rows, so `Provider`, `Model` and `Key` sit exactly above the words
  under them.
- `console.py`: the setup window, now a real application window: light or dark following Windows, two voice lines
  (**Primary voice** and **Backup voice**: Microsoft Edge's engine, free and keyless, which takes over when the primary
  one fails) and a `Provider | Model | Key` table. The free Windows voice used to have its own table below the
  providers as well; it says everything the backup line says, so it is gone.
  A provider is edited in one dialog (name, endpoint, model, voice, key, request limit) and **+ Add provider**
  creates one, so nothing needs to be typed into `settings.json`. `Open Agent Talkback` in the tray opens the same window,
  and a change reaches the running program at once.
- Picking a voice opens the full list with **Test** beside every row; for the secondary voice that is all 322 of
  Microsoft's voices, filterable by name or language. A test speaks that voice alone (`only` on `POST /say`), and
  says so plainly when speaking is switched off or Talkback is not running.
- `secrets/`: every key now lives here, one file per source named after its id (`openrouter.local`, `groq.local`).
  The folder is gitignored except its README, so a clone has no keys and a key never reaches `settings.json`,
  `talkback.log` or the status page.
- `requirements.txt`: the three packages, pinned, installed by the console's `Install packages` or by pip.
- `POST /reload`: makes a running program read `settings.json` again, which is how the console and the tray agree.
- `STYLE.md`: the project's voice, code rules, data formats and mark.
- `SECURITY.md`: trust model, release guarantees and how to report a problem.
- `assets/`: the 7×7 dot-matrix mark as SVG, 16/32/256 px PNG, a multi-size `.ico` and a GitHub social preview,
  generated by `tools/build_assets.py` from the tray's own geometry.
- `tests/`: the spoken-words contract as golden fixtures plus pure-function tests, run with
  `python -m unittest discover -s tests -v`.

### Changed
- A source's shipped `key` is `file:secrets/<id>.local`; an absolute path or `env:NAME` still works, and
  `adopt_key()` copies a key kept elsewhere into `secrets/` in one step.
- Voices have three layers in `settings.json` (version 3) and in the tray: a source (endpoint and key), its voice sets
  (one model each) and the voices in a set. The tray reads `Voice > Free (for Windows) > Microsoft Edge > Guy`
  and gained `Reload settings`; sources are tried in list order.
- The tray menu now reads `Voice On/Off > On | Off`, `Voice`, `Open Talkback`, `Stop current reply`,
  `Reload settings`, `Quit`, and voices are named as they are spoken (`Guy`, not `en-US-GuyNeural`).
- The secondary voice is always the fallback right after the primary one: if the primary fails, the free Windows
  voice speaks before any other keyed provider.
- Any OpenAI-compatible `/audio/speech` endpoint works, so another provider is a settings row rather than code, and one
  key can speak several models by holding several voice sets.
- Groq's 200-character limit is a `request_limit` on its voice set, so a limit is only ever applied to the model that
  has it; longer text is split and fetched in parallel.
- A source with no key is skipped instead of failing the reply, and the tray marks it `(no key)`.
- A shipped build carries at most 5 sources; `MAX_SOURCES` raises it.
- A version 1 or 2 `settings.json` is upgraded on start, keeping the chosen voices and key files.
- Microsoft's shipped source is labelled `Free (for Windows)`, its voice set `Microsoft Edge`; an older
  `Default (Free)` is renamed on load.

### Fixed
- The provider table no longer flashes white every couple of seconds: it is redrawn only when something in it changed.
- The two switches move as soon as they are clicked; the slow part (writing the login shortcut, telling the running
  program) happens on a thread and reports back, instead of holding the window for about a second.
- Testing a Microsoft voice now makes a sound: edge-tts answers in MP3 and the file was named `.wav`, so Windows opened
  it as a wave file and played nothing. The suffix now follows the bytes.
- The backup list shows only Microsoft's English (`en-`) voices, sorted by name, and the voice in use is marked where it
  sits instead of being lifted to the top of either list.
- Every input box is built by one `entry_box()` with one height, so a dialog's fields line up.
- The local server now refuses a foreign `Host` or `Origin`, reads `application/json` only, caps a request at 100 KB
  and answers `429` once 24 replies are waiting, so a web page cannot make the machine speak.
- `GET /status` names each key `file`, `env` or `missing` instead of its path. `settings.json` is written through a
  staging file, and an unreadable one is kept as `settings.bad.json` instead of being overwritten.
- Speech audio goes into this process's own temporary folder under a fresh name, not a predictable file in `%TEMP%`.
- `send.py` starts the program with PowerShell's `-EncodedCommand`, so no path is ever pasted into a command string.
- Buttons no longer flash when they are clicked: no outline and no pressed colour, just the hand pointer.
- Minimising the console works again (it used to jump straight back out of the taskbar).
- **Talkback Voice** at the top kept showing the state from when the window opened; it now follows the tray every
  two seconds.
- OpenRouter's HeyGen Voice refused every request (`400`, it speaks only `pcm`, not `wav`), so every reply fell back
  to Microsoft. A voice set can now name its `format` (`pcm`/`wav`, default `wav`), the shipped HeyGen set says `pcm`,
  and a provider that refuses the format is asked again the other way once.
- Prices are spoken after the number: "5 euros", not "euros 5".

### Security
- `Pillow` is pinned to 13.0.0. 12.3.0 had 40 known advisories (`pip-audit`).

### Planned
- A desktop shortcut to the setup console.

## [0.1.0] - 2026-11-05 (not published)
### Added
- Speaks an agent reply from the `**In short:**` and `**From you:**` paragraphs; without that header, a short reply
  whole, otherwise its first sentence or two.
- Tray icon: 7×7 dot-matrix Play/Pause. Left-click toggles speaking, right-click opens voices and Stop.
- Voices: HeyGen Voice Turbo through OpenRouter, Groq Orpheus, Microsoft neural as the free backup.
- `send.py` for agent hooks; starts Talkback through WMI when it is not running.
- Starts at login from the Startup folder.

---
Made by Max Tiong ([github.com/mkraeborn](https://github.com/mkraeborn)) and Alts Agency
([alts.agency](https://alts.agency)).
