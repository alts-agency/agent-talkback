# Talkback console window (`console.py`): internals
For developers who change the window or the tray. How to use them: `../README.md`.

`console.py` is the setup window (tkinter, no new dependency, standard-library widgets painted with the palette in
`LIGHT`/`DARK`; `windows_is_dark()` reads `AppsUseLightTheme`, `dark_title_bar()` asks DWM for a matching title bar).
The window is Windows' own black and white: `LIGHT`/`DARK` are neutral greys, and Alts Agency's green is only ever an
accent: the website link, the primary voice's name, a saved key's `Set`, a focused input, a switch that is on, and
`accent_soft` behind the voice in use (`#2E6B60` on light, `#8FCFC2` on dark). Contrast is checked against the card, the
background and that tint, so a palette change is a contrast check first and a colour second. Every input is built by
`entry_box()` with `ENTRY_PAD`, which is what keeps the fields in one dialog the same height.
It shows the same layers the tray does, as content rather than menus: one line each for **Primary voice** and
**Backup voice** (`voice_line()`: `nadine (HeyGen | OpenRouter)`, `Guy (Microsoft Edge | Free)`; the backup line is
Microsoft Edge's engine, so there is no second table for it), and a `Provider | Model | Key` table of the keyed
sources (`talkback.KEYED`). Nothing is edited inline: **Edit** opens `ProviderDialog` (name, endpoint, API key
(masked) and the models), and **Change** opens `VoiceDialog`: the source's voices as tiles, `TILES_ACROSS`
(3) to a line, each tile one outlined box in two parts, the voice above and a **Test** strip below a dividing line; for the free source the list is
Microsoft's own (`edge_voices()`, 322 of them, fetched beside the window and read back through a queue so Tk is only
touched from the main thread) with a filter box. Clicking a tile picks the voice: `pick_voice()` for a provider,
`set_voice()` for the secondary one, which never steals `primary`; the tile in use sits on `accent_soft` with an accent
outline and a tick. **Test** no longer picks anything: it queues
`test_words()` through `send.deliver(..., only=<id>, set_name=<set>, voice=<voice>)`, and `Speaker.speak()` copies that
set for the one line, so testing a voice changes nothing about what speaks next. The list scrolls on `Scroll`'s own
thumb (a rounded pill on a small canvas, with no arrows or trough).
With two or more voice sets the Primary voice dialog has tabs over the tiles (`model_tabs()`: `Model 1`, `Model 2`,
the shown one bold and underlined, `(Primary)` after the set in use, the shown model's id on a muted line under them)
and `voice_rows(entry, set_name)` gives one set's voices in settings' order, each once; it opens on the set in use.
With one set there are no tabs. The
backup list is cut to Microsoft's `en-` voices and sorted by name, with a configured voice merged where it sorts. A
test line names one voice, and Microsoft's audio is MP3, so `speak()` names that temp file `.mp3`: the suffix is what
tells MCI how to decode it, and a `.wav` name made Windows play nothing. It edits the same `settings.json` the tray reads and asks the running program to reload. The
window sizes itself to its content (`fit()`), so another provider is not cut off. Providers are shown by
`provider_rows()`: alphabetically by label, keyed sources only, never reordered. The window's job is to say which
one is primary. `_table()` draws the head and `_provider_row()` each row as children of one grid, so columns line up
by construction; never give a row its own grid beside a separate head. The primary row carries `(Primary)` next to
the name, every other row has **Set as primary** (`set_primary()` writes `settings["primary"]` and saves). The Model
column is `model_words()`: the model in use, then ` +1`, ` +2` for the provider's other models (it is part of the
redraw signature). Each row ends with **Edit**. A provider's models live in its Edit dialog: under name, endpoint and
key, a **Models** head with **+ Add model** and a table (the shared `table()`), one row per set: `Model 1`, the model
id, its voice, `(Primary)` on the set in use, **Edit**, and **Remove** unless it is the last. The dialog edits a
working copy (`work`), so nothing reaches `settings.json` before **Save**. `ModelDialog` (model, voice, limit; as tall
as its fields) adds through `with_model()` (set named by `set_name_for()`: `s2.1-pro-free`, then ` 2`; `voices` =
[voice]; the format of the set in use copied; refuses an empty model or voice, a model the provider already has, and
`MAX_SETS`) and edits through `edited_model()`, which renames the set after a changed model and moves `set` with it;
`without_model()` removes one and hands `set` to the first that is left. Add provider keeps the single form. Only the
buttons in a table act: rows do not open, do not light up under the pointer and do not drag. `Switch` is the painted
canvas used three times, stacked in one card under the providers (`_switch_row()`): **Talkback Voice**
(`toggle_speaking()` writes `settings["enabled"]`, then `set_speaking()` posts `/enabled` so the program and its tray
agree), **Start with Windows** (`set_startup()`) and **Desktop shortcut** (`set_desktop()`), both snapping back when
the shortcut could not be written. The desktop is the one Windows reports (`desktop_link()` reads the shell's own
folder), so a desktop moved to OneDrive is still found; the shortcut points at `console.py`. The window
is `resizable(False, False)`: fixed size, and minimising is left alone. `SetCurrentProcessExplicitAppUserModelID`,
`iconbitmap(default=…)` and `iconphoto(True, …)` make the taskbar show the project's icon instead of Python's.
`button()` draws no outline and does not change background when pressed (`takefocus=0`), so nothing flashes; the hand
pointer is the whole affordance. The `WEBSITE` link sits at the top right of the header, and the line at the bottom
appears only when a problem or a missing package needs saying. All three switches move the moment they are clicked and do
the slow part on a thread (`set_startup()` runs PowerShell, `set_speaking()` is an HTTP call), reporting through
`results`, which `drain()` empties on the main thread. `_provider_table()` keeps a signature of what it drew and
skips the redraw when nothing changed, because redrawing the table was the white flash every couple of seconds.

**Voices by Agent** is the first row of the bottom card, above **Talkback Voice** (`_card_row()`, with a **Change**
button); it opens `TalkersDialog`: one row per talker from `talker_rows()`, the last heard first, showing the folder
hint (the name only when there is none), a `Menubutton` and a `Switch`. Off, the button is disabled and names the
automatic voice. On starts from that voice (`automatic_choice()`). The menu is built from `talkback.voice_menu()`, the
data behind the tray's menu, as provider, model, voice, with a tick on the voice in use (the voice level only). It takes
the palette's colours: `follow_system_theme()` does nothing for a Tk menu. Windows draws its frame and the submenu
arrows, which stay light and faint in dark mode. A chosen voice that is gone shows a warning line. Changes go through `Settings.set_talker()` and `save_and_reload()`; only that row is
redrawn. An open dialog does not show a folder heard after it opened.

## Tray (`talkback.py`)
The tray icon runs on the main thread (pystray). Left-clicking it switches speaking on or off (`default=True`).
Right-clicking opens `Agent Talkback` (ticked while it speaks; a click switches it) with `Running` or `Not running`
under it, then `Primary Voice >` a provider > a voice set > its voices, then `Open console`, then
`Interrupt current` and `Reload settings`, then `Quit`, with `pystray.Menu.SEPARATOR` between the segments.
`tray_menu()` hands pystray a generator, so the words and the ticks are made when the menu opens, not once at
startup. That keeps the status line current. Providers and sets carry `checked=` too, so the tick shows the voice in
use from the top down. `voice_menu()` leaves Microsoft out because the backup voice is chosen in the console.
`follow_system_theme()` calls `uxtheme` ordinals 135 and 104 so the native menu turns dark on a dark Windows.

The icon is `icon_image()`: the Alts Agency chevron (`BRAND_PATH`, parsed by `chevron_points()`), white on black, with
the lockup's `\` stroke left out because the chevron alone reads as a play sign. It turns grey while Talkback Voice is
off, and `tools/build_assets.py` draws `assets/` from the same path and colours.

Picking a voice writes `primary`, that source's `set` and that set's `voice` at once (`pick_voice`). `enabled` starts as
`True`. Voices are labelled with `voice_name()` (`en-US-GuyNeural` is `Guy`). `Open console` starts `console.py` as a
separate process, so the tray keeps running while it is open. `main()` hands `make_handler` a repaint built from the
`wake` list that `tray()` fills, which is how `POST /enabled` from the console moves the icon and menu.
