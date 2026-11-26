"""Agent Talkback's setup console: the one window a person needs.

    python console.py          (or  Open console  in the tray menu)

It shows the voice that speaks (Primary) and Microsoft Edge's free Windows voice, which takes over when the primary
voice fails (Backup). It lists the providers with the key each one has, alphabetically, with the primary one marked
and a button on every other row that makes it primary. It edits any of them, shows every voice as a tile with its own
Test, starts Agent Talkback at login, and links to the project's website. It uses Windows'
light or dark mode, whichever Windows is in.

Every change is written to settings.json and the running program is asked to reload, so nothing restarts and the
tray never disagrees with this window.
"""
import asyncio
import base64
import copy
import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
from importlib import util
from pathlib import Path
from tkinter import messagebox

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import send      # noqa: E402  one way to hand the running program a reply
import talkback  # noqa: E402

WEBSITE = "https://alts.agency"      # the one link in the window, at the very bottom
PACKAGES = {"edge_tts": "edge-tts", "pystray": "pystray", "PIL": "Pillow"}
KEY_WORDS = {"ready": "Set", "missing": "Not set", "outside": "Outside secrets"}
MAX_LISTED = 150          # voices drawn at once in the voice list; the filter finds the rest
TILES_ACROSS = 3          # voices on one line of that list


def font(size, weight="normal"):
    return ("Segoe UI", size, weight)


# ── Colours: Windows' light or dark mode, whichever it is in ─────────────────────────────────────────

# Windows' own black and white, with Alts Agency's green only where it accents: the link, the primary voice, the
# "Set" of a saved key, a focused input and a switch that is on. The window itself stays neutral.
LIGHT = {"bg": "#F5F6F8", "card": "#FFFFFF", "line": "#E4E6EB", "text": "#1B1D22", "muted": "#6C7280",
         "accent": "#2E6B60", "on_accent": "#FFFFFF", "accent_hover": "#24544B", "accent_soft": "#EDF3F1",
         "hover": "#EFF1F5", "good": "#1B7A4B", "warn": "#9A5B00", "bad": "#B4372D"}
DARK = {"bg": "#17181C", "card": "#1F2126", "line": "#2E3138", "text": "#EDEEF1", "muted": "#9BA1AC",
        "accent": "#8FCFC2", "on_accent": "#0D1A20", "accent_hover": "#A6DCD0", "accent_soft": "#232B2B",
        "hover": "#262932", "good": "#5AC98C", "warn": "#E0A94E", "bad": "#F07A70"}


def windows_is_dark():
    """Whether Windows is set to dark mode for apps (HKCU\\...\\Themes\\Personalize\\AppsUseLightTheme == 0)."""
    try:
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except (ImportError, OSError):
        return False


def dark_title_bar(window, dark):
    """Ask Windows to draw the title bar in the same mode; silently does nothing where it cannot."""
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # 20 is current, 19 is the Windows 10 name for the same switch
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                return
    except (AttributeError, OSError):
        pass


# ── What the window needs from Windows ─────────────────────────────────────────────────────────────

def missing_packages():
    """The packages this program needs that this Python does not have."""
    return [name for module, name in PACKAGES.items() if util.find_spec(module) is None]


def startup_link():
    """The shortcut Windows runs at login."""
    folder = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
    return folder / "Talkback.lnk"


def startup_enabled():
    return startup_link().is_file()


def _quoted(text):
    """One PowerShell single-quoted string: `'` is doubled, nothing else is interpreted."""
    return "'" + str(text).replace("'", "''") + "'"


def _encoded(script):
    """-EncodedCommand: the script is passed as base64, so no quoting rule or shell ever sees the paths."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def desktop_link():
    """The shortcut the console can put on the desktop. Windows knows where the desktop is, OneDrive or not."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                                                      r"\User Shell Folders") as key:
            folder = os.path.expandvars(str(winreg.QueryValueEx(key, "Desktop")[0]))
    except (ImportError, OSError):
        folder = ""
    return (Path(folder) if folder else Path(os.environ.get("USERPROFILE", "")) / "Desktop") / "Agent Talkback.lnk"


def desktop_enabled():
    return desktop_link().is_file()


def _shortcut_script(link, entry):
    """The PowerShell that writes one shortcut: this Python's pythonw, one script here, with the app's icon."""
    return ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _quoted(link) + ");"
            "$s.TargetPath = " + _quoted(Path(sys.executable).with_name("pythonw.exe")) + ";"
            "$s.Arguments = " + _quoted('"' + str(HERE / entry) + '"') + ";"
            "$s.WorkingDirectory = " + _quoted(HERE) + ";"
            "$s.IconLocation = " + _quoted(HERE / "assets" / "talkback.ico") + ";"
            "$s.Save()")


def _write_shortcut(link, entry, enabled):
    """Write or delete one shortcut; False when the file on disk still disagrees with what was asked."""
    if enabled:
        script = _shortcut_script(link, entry)
    else:
        script = "Remove-Item -LiteralPath " + _quoted(link) + " -ErrorAction SilentlyContinue"
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", _encoded(script)],
                   capture_output=True, creationflags=0x08000000)
    return Path(link).is_file() == enabled


def set_startup(enabled):
    """Create or remove the login shortcut (pythonw talkback.py, with the app's icon); True when it worked."""
    return _write_shortcut(startup_link(), "talkback.py", enabled)


def set_desktop(enabled):
    """Create or remove the desktop shortcut (pythonw console.py, so this window is one double-click away)."""
    return _write_shortcut(desktop_link(), "console.py", enabled)


def reload_running(port):
    """Ask the running program to re-read settings.json. False when it is not running."""
    request = urllib.request.Request(f"http://127.0.0.1:{port}/reload", method="POST", data=b"{}",
                                     headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=1).read()
        return True
    except OSError:
        return False


def set_speaking(port, enabled):
    """Switch the running program's voice on or off; False when it is not running."""
    request = urllib.request.Request(f"http://127.0.0.1:{port}/enabled", method="POST",
                                     data=json.dumps({"enabled": bool(enabled)}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=1).read()
        return True
    except OSError:
        return False


def speaking(port):
    """Whether the running program would speak now: True, False, or None when it is not running."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1) as response:
            return bool(json.loads(response.read()).get("enabled", True))
    except (OSError, ValueError):
        return None


def edge_voices():
    """Every voice edge-tts offers, as (ShortName, locale, gender); [] when it cannot be asked."""
    try:
        import edge_tts
        return [(voice["ShortName"], voice["Locale"], voice["Gender"])
                for voice in asyncio.run(edge_tts.list_voices())]
    except Exception as e:  # offline, or edge-tts not installed yet: the configured voices still show
        talkback.log(f"console: the full Edge voice list is unavailable ({e!r})")
        return []


# ── The words this window shows ────────────────────────────────────────────────────────────────────

def key_words(entry):
    """The Key column: where this source's key stands."""
    if entry.get("engine") == "microsoft":
        return "Free"
    return KEY_WORDS[talkback.secret_state(entry)]


def key_colour(entry, colours):
    state = "ready" if entry.get("engine") == "microsoft" else talkback.secret_state(entry)
    return {"ready": colours["accent"], "missing": colours["muted"], "outside": colours["warn"]}[state]


def voice_line(entry, voice_set=None):
    """The one line per voice: 'nadine (HeyGen | OpenRouter)', 'Guy (Microsoft Edge | Free)'."""
    voice_set = voice_set or talkback.active_set(entry)
    where = "Free" if entry["engine"] == "microsoft" else entry["label"]
    return f"{talkback.voice_name(voice_set['voice'])} ({voice_set['name']} | {where})"


def free_source(settings):
    """The keyless Windows voice that speaks when the primary one fails."""
    return next((entry for entry in settings["sources"] if entry["engine"] == "microsoft"), None)


def provider_rows(settings):
    """The keyed sources, alphabetically by provider name: the only order this window shows them in."""
    return sorted((entry for entry in settings["sources"] if entry["engine"] in talkback.KEYED),
                  key=lambda entry: str(entry["label"]).casefold())


def new_id(label, taken):
    """A source id from a provider name: 'My Provider' is my-provider, and never one already in use."""
    stem = re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-")[:32] or "source"
    source_id, number = stem, 2
    while source_id in taken:
        source_id, number = f"{stem}-{number}", number + 1
    return source_id


def model_words(entry):
    """The Model column: the model in use, and ' +1', ' +2' when the provider holds more."""
    others = len(entry["sets"]) - 1
    model = talkback.active_set(entry).get("model") or "—"
    return f"{model} +{others}" if others else model


def set_name_for(model, taken=()):
    """A voice set named after its model: 'fish-audio/s2.1-pro-free:free' is 's2.1-pro-free', then ' 2', ' 3'."""
    stem = model.rsplit("/", 1)[-1].split(":", 1)[0] or model
    name, number = stem, 2
    while name in taken:
        name, number = f"{stem} {number}", number + 1
    return name


def _model_problem(entry, model, voice, other_than=None):
    """Why this model and voice cannot go into `entry` (besides the set named `other_than`), or ''."""
    if not model:
        return "Enter the model id."
    if not voice:
        return "Enter a voice id."
    if model in {voice_set.get("model") for voice_set in entry["sets"] if voice_set["name"] != other_than}:
        return f"{entry['label']} already has {model}."
    return ""


def _cleaned(spec, count):
    """(source, problem) once `clean_source()` has passed the spec with all `count` of its sets kept."""
    clean, problems = talkback.clean_source(spec)
    if clean is None or len(clean["sets"]) != count:
        return None, problems[0] if problems else "That model cannot be used."
    return clean, ""


def with_model(entry, model, voice, limit=None):
    """(source, problem): a copy of `entry` holding one more model, or why not. The set in use stays the same.

    The new set copies the format of the set in use: same endpoint, likely the same answer, and the program asks
    again in the other format when a model refuses it.
    """
    model, voice = model.strip(), voice.strip()
    problem = _model_problem(entry, model, voice)
    if problem:
        return None, problem
    if len(entry["sets"]) >= talkback.MAX_SETS:
        return None, (f"{entry['label']} already has {talkback.MAX_SETS} models, the most a provider holds. "
                      "Raise MAX_SETS in talkback.py to add more.")
    voice_set = {"name": set_name_for(model, {item["name"] for item in entry["sets"]}), "model": model,
                 "voice": voice, "voices": [voice]}
    if limit:
        voice_set["request_limit"] = int(limit)
    if talkback.active_set(entry).get("format"):
        voice_set["format"] = talkback.active_set(entry)["format"]
    spec = copy.deepcopy(entry)
    spec["sets"].append(voice_set)
    return _cleaned(spec, len(spec["sets"]))


def edited_model(entry, name, model, voice, limit=None):
    """(source, problem): a copy of `entry` whose model `name` has this model, voice and limit, or why not.

    The set keeps its format and its place, and its name while the model id stays; a changed model renames it after
    the new model, so the voice line never names a model that is gone. A new voice goes first in its voices.
    """
    model, voice = model.strip(), voice.strip()
    problem = _model_problem(entry, model, voice, other_than=name)
    if problem:
        return None, problem
    spec = copy.deepcopy(entry)
    voice_set = next(item for item in spec["sets"] if item["name"] == name)
    if model != voice_set.get("model"):
        renamed = set_name_for(model, {item["name"] for item in spec["sets"] if item is not voice_set})
        if spec.get("set") == name:
            spec["set"] = renamed
        voice_set["name"] = renamed
    voice_set.update(model=model, voice=voice)
    if voice not in voice_set.get("voices", []):
        voice_set["voices"] = [voice, *voice_set.get("voices", [])]
    if limit:
        voice_set["request_limit"] = int(limit)
    else:
        voice_set.pop("request_limit", None)
    return _cleaned(spec, len(spec["sets"]))


def without_model(entry, name):
    """A copy of `entry` without the model `name`; the last model stays. When the one in use goes, the first is."""
    spec = copy.deepcopy(entry)
    if len(spec["sets"]) < 2:
        return spec
    in_use = talkback.active_set(spec)["name"]
    spec["sets"] = [item for item in spec["sets"] if item["name"] != name]
    spec["set"] = spec["sets"][0]["name"] if in_use == name else in_use
    return spec


def model_tabs(entry):
    """The tabs over the Primary voice list: 'Model 1', 'Model 2' ... in settings' order, the one in use marked."""
    in_use = talkback.active_set(entry)["name"]
    return [{"set": voice_set["name"], "label": f"Model {number}", "model": voice_set.get("model") or voice_set["name"],
             "in_use": voice_set["name"] == in_use} for number, voice_set in enumerate(entry["sets"], start=1)]


def voice_rows(entry, set_name=None):
    """One model's voices in settings' order (the model in use when no name is given), each voice once."""
    voice_set = next((item for item in entry["sets"] if item["name"] == set_name), None) or talkback.active_set(entry)
    return [{"set": voice_set["name"], "voice": voice, "note": voice_set["name"]}
            for voice in dict.fromkeys([*voice_set.get("voices", []), voice_set["voice"]])]


def automatic_choice(settings, talker_id):
    """The automatic voice as a choice: where a folder's custom voice starts when it is switched on."""
    entry = settings.source()
    voice_set = talkback.active_set(entry)
    return {"source": entry["id"], "set": voice_set["name"],
            "voice": talkback.automatic_voice(talker_id, voice_set)}


def talker_rows(settings):
    """Every talker heard, the last heard first: its folder, whether its custom voice is on, the voice that speaks
    now as a choice and as words, and `lacking`, a chosen voice that is no longer there."""
    rows = []
    for talker_id, row in sorted(settings["talkers"].items(), key=lambda item: item[1]["last"], reverse=True):
        custom = talkback.custom_voice(settings, row)
        if custom:
            entry, voice_set = custom
            choice = dict(row["voice"])
        else:
            choice = automatic_choice(settings, talker_id)
            entry = settings.source()
            voice_set = dict(talkback.active_set(entry), voice=choice["voice"])
        rows.append({"id": talker_id, "folder": row["folder"] or row["name"], "on": row["voice"] is not None,
                     "choice": choice, "label": voice_line(entry, voice_set),
                     "lacking": row["voice"]["voice"] if row["voice"] and not custom else ""})
    return rows


def test_words(voice):
    """What Test says, in the voice it is testing."""
    return f"Testing Talkback in {talkback.voice_name(voice)}'s voice."


# ── Small widgets: a card, a button, a scrolling list ───────────────────────────────────────────────

ENTRY_PAD = 4            # the one number every input box is built with, so they are all the same height


def entry_box(parent, colours, **options):
    """A one-line input in the window's palette: one height, one focus ring, wherever it appears."""
    return tk.Entry(parent, bg=colours["card"], fg=colours["text"], insertbackground=colours["text"],
                    relief="flat", highlightthickness=1, highlightbackground=colours["line"],
                    highlightcolor=colours["accent"], font=font(10), **options)


def button(parent, text, command, colours, kind="ghost", background=None, **options):
    """A flat button: no outline and no flash when it is clicked, so the hand pointer is the one sign it is a button.

    `ghost` is a row-level button (Change, Edit, Set as primary), `accent` the one action in a dialog, `quiet` the
    same words in muted text.
    """
    foreground = {"ghost": colours["text"], "accent": colours["accent"], "quiet": colours["muted"]}[kind]
    background = background or colours["card"]
    return tk.Button(parent, text=text, command=command, bg=background, fg=foreground,
                     activebackground=background, activeforeground=foreground, relief="flat", bd=0,
                     highlightthickness=0, takefocus=0, padx=12, pady=5, cursor="hand2",
                     font=font(9, "bold" if kind == "accent" else "normal"), **options)


def card(parent, colours, pad=0):
    """A panel with a one-pixel edge, the shape everything sits in."""
    return tk.Frame(parent, bg=colours["card"], highlightthickness=1,
                    highlightbackground=colours["line"], padx=pad, pady=pad)


def divider(parent, colours, pady=0):
    line = tk.Frame(parent, bg=colours["line"], height=1)
    line.pack(fill="x", pady=pady)
    return line


class Scroll(tk.Frame):
    """A panel that scrolls when its content is taller than it is: a slim rounded thumb, no arrows, no trough.

    The thumb only exists while there is something to scroll, and it can be dragged, which is what makes it read as
    the scrollbars in modern chat windows rather than as the Windows scrollbar it replaced.
    """

    THUMB = 6                     # px wide; the pointer gets a little more room than the ink does

    def __init__(self, parent, colours, height=300):
        super().__init__(parent, bg=colours["card"])
        self.colours, self.first, self.last, self.grab = colours, 0.0, 1.0, None
        self.canvas = tk.Canvas(self, bg=colours["card"], height=height, highlightthickness=0, bd=0)
        self.track = tk.Canvas(self, width=self.THUMB + 10, bg=colours["card"], highlightthickness=0, bd=0,
                               cursor="hand2")
        self.body = tk.Frame(self.canvas, bg=colours["card"])
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.moved)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.track.pack(side="right", fill="y")
        self.body.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.resized)
        self.canvas.bind("<MouseWheel>", self.scroll)
        self.body.bind("<MouseWheel>", self.scroll)
        self.track.bind("<Configure>", lambda event: self.draw())
        self.track.bind("<Button-1>", self.press)
        self.track.bind("<B1-Motion>", self.drag)

    def scroll(self, event):
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def clear(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)

    def resized(self, event):
        """The body is always as wide as the canvas, so a row fills the width it is given."""
        self.canvas.itemconfigure(self.window, width=event.width)
        self.draw()

    def moved(self, first, last):
        self.first, self.last = float(first), float(last)
        self.draw()

    def draw(self):
        """The thumb: a pill as tall as the visible part, and nothing at all when everything fits."""
        self.track.delete("all")
        height = self.track.winfo_height()
        if height < 2 or self.last - self.first >= 0.999:
            return
        top = height * self.first
        bottom = max(height * self.last, top + 28)
        left, right = 5, 5 + self.THUMB
        radius = self.THUMB / 2
        colour = self.colours["muted"]
        self.track.create_oval(left, top, right, top + self.THUMB, fill=colour, outline="")
        self.track.create_oval(left, bottom - self.THUMB, right, bottom, fill=colour, outline="")
        self.track.create_rectangle(left, top + radius, right, bottom - radius, fill=colour, outline="")

    def press(self, event):
        self.grab = (event.y, self.first, self.last)

    def drag(self, event):
        if not self.grab:
            return
        start, first, last = self.grab
        span = max(last - first, 0.05)
        self.canvas.yview_moveto(first + (event.y - start) / max(self.track.winfo_height(), 1) * (1 - span))


def table(parent, columns, colours):
    """A table's head and every one of its rows share one grid, so a column is one width across all of them."""
    frame = tk.Frame(parent, bg=colours["card"], padx=18)
    frame.pack(fill="x")
    for index, (name, weight) in enumerate(columns):
        frame.grid_columnconfigure(index, weight=weight)
        tk.Label(frame, text=name.upper(), bg=colours["card"], fg=colours["muted"], font=font(8, "bold"),
                 anchor="w").grid(row=0, column=index, sticky="w", pady=(13, 9))
    frame.grid_columnconfigure(len(columns), weight=0)
    return frame


def hover(widgets, colours, colour_name="hover", cursor="hand2"):
    """Light a row up as the pointer crosses it (and every label inside it)."""
    normal = colours["card"]

    def lighten(event=None):
        for widget in widgets:
            if not isinstance(widget, tk.Button):
                widget.configure(bg=colours[colour_name])

    def plain(event=None):
        for widget in widgets:
            if not isinstance(widget, tk.Button):
                widget.configure(bg=normal)

    for widget in widgets:
        widget.bind("<Enter>", lighten)
        widget.bind("<Leave>", plain)
        if cursor:
            try:
                widget.configure(cursor=cursor)
            except tk.TclError:
                pass


class Switch(tk.Canvas):
    """An on/off switch that reads at a glance: a track that turns accent when it is on, and a knob that slides."""

    def __init__(self, parent, colours, on, command):
        super().__init__(parent, width=46, height=24, bg=colours["card"], highlightthickness=0, bd=0, cursor="hand2")
        self.colours, self.on, self.command = colours, bool(on), command
        self.bind("<Button-1>", lambda event: self.command(not self.on))
        self.draw()

    def set(self, on):
        """Only redraw when the state really changed: a redraw every two seconds is a flicker."""
        on = bool(on)
        if on == self.on:
            return
        self.on = on
        self.draw()

    def draw(self):
        self.delete("all")
        track = self.colours["accent"] if self.on else self.colours["line"]
        self.create_oval(2, 3, 20, 21, fill=track, outline=track)
        self.create_oval(26, 3, 44, 21, fill=track, outline=track)
        self.create_rectangle(11, 3, 35, 21, fill=track, outline=track)
        knob = 35 if self.on else 11
        self.create_oval(knob - 8, 4, knob + 8, 20, fill=self.colours["card"], outline="")


# ── The console window ────────────────────────────────────────────────────────────────────────────

class Console(tk.Tk):
    """The setup window. It edits the same settings.json the tray reads, and asks the tray to reload."""

    def __init__(self):
        super().__init__()
        self.dark = windows_is_dark()
        self.c = DARK if self.dark else LIGHT
        self.title("Agent Talkback")
        self.geometry("1000x760")
        self.resizable(False, False)     # the window is the size of what it shows, and is not for dragging bigger
        self.configure(bg=self.c["bg"])
        try:                               # one name for this window, so the taskbar uses our icon and not Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AltsAgency.AgentTalkback")
        except (AttributeError, OSError):
            pass
        try:
            self.iconbitmap(default=str(HERE / "assets" / "talkback.ico"))
            self.iconphoto(True, tk.PhotoImage(file=str(HERE / "assets" / "icon-256.png")))
        except tk.TclError:
            pass
        self.settings = talkback.Settings()
        self.stamp = self._stamp()
        self.results = queue.Queue()     # what the worker threads (the two switches) hand back
        self.provider_signature = None   # the last table drawn, so an unchanged one is not drawn again
        self._build()
        self.refresh()
        self.after(10, lambda: dark_title_bar(self, self.dark))
        self.after(120, self.drain)
        self.after(2000, self.watch)

    # ── Building the window ──

    def _build(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        body = self.body = tk.Frame(self, bg=self.c["bg"])
        body.grid(row=0, column=0, sticky="nsew")

        header = tk.Frame(body, bg=self.c["bg"], padx=24)
        header.pack(fill="x", pady=(22, 14))
        left = tk.Frame(header, bg=self.c["bg"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text="Agent Talkback", bg=self.c["bg"], fg=self.c["text"],
                 font=font(22, "bold")).pack(anchor="w")
        tk.Label(left, text="Released as open source by Alts Agency. This Agent Talkback tool allows your agents to speak back to you as they reply. "
                            "You can find updates and instructions on our website.", bg=self.c["bg"], fg=self.c["muted"], font=font(10),
                 justify="left", wraplength=720).pack(anchor="w", pady=(4, 0))

        self.link = tk.Label(header, text="alts.agency \u2197", bg=self.c["bg"], fg=self.c["accent"],
                             font=font(10, "bold"), cursor="hand2")
        self.link.pack(side="right", anchor="n", padx=(18, 0), pady=(6, 0))
        self.link.bind("<Button-1>", lambda event: webbrowser.open(WEBSITE))
        self.link.bind("<Enter>", lambda event: self.link.configure(font=font(10, "bold underline")))
        self.link.bind("<Leave>", lambda event: self.link.configure(font=font(10, "bold")))

        voices = card(body, self.c)
        voices.pack(fill="x", padx=24, pady=(0, 18))
        self.primary = self._voice_row(voices, "PRIMARY VOICE",
                                       "Your agent will use this model first, if available.",
                                       self.change_primary, first=True)
        divider(voices, self.c)
        self.secondary = self._voice_row(voices, "BACKUP VOICE",
                                         "Microsoft Edge's voice takes over when the primary voice fails. It needs "
                                         "Microsoft's servers online, but no key.",
                                         self.change_secondary)

        self._section(body, "Providers", "Your primary voice comes from one of these.",
                      ("+  Add provider", self.add_provider))
        self.provider_card = card(body, self.c)
        self.provider_card.pack(fill="x", padx=24, pady=(0, 18))

        switches = card(body, self.c)
        switches.pack(fill="x", padx=24, pady=(0, 28))
        agents = self._card_row(switches, "Voices by Agent",
                                "You can customize different voices for different agents if you run an agent fleet.")
        self.agents_change = button(agents, "Change", self.change_talkers, self.c, "ghost")
        self.agents_change.pack(side="right", pady=(2, 0))
        divider(switches, self.c)
        self.voice_switch = self._switch_row(
            switches, "Talkback Voice",
            "Reply text goes to your voice providers only while this is on.",
            self.settings["enabled"], self.toggle_speaking)
        divider(switches, self.c)
        self.startup_on = tk.BooleanVar(value=startup_enabled())
        self.switch = self._switch_row(switches, "Start with Windows",
                                       "Agent Talkback starts in the tray when you log in.",
                                       self.startup_on.get(), self.toggle_startup)
        divider(switches, self.c)
        self.desktop_on = tk.BooleanVar(value=desktop_enabled())
        self.desktop_switch = self._switch_row(switches, "Desktop shortcut",
                                               "Puts Agent Talkback on your desktop, and takes it away when you switch "
                                               "this off.",
                                               self.desktop_on.get(), self.toggle_desktop)

        self.bottom = tk.Frame(self, bg=self.c["bg"], padx=24, pady=12)
        self.bottom.grid(row=1, column=0, sticky="ew")
        self.bottom.grid_remove()        # shown only when there is something to say: a problem, or a missing package
        self.status = tk.Label(self.bottom, anchor="w", justify="left", bg=self.c["bg"], fg=self.c["bad"],
                               font=font(9), wraplength=760)
        self.status.pack(side="left")
        self.install = button(self.bottom, "Install packages", self.install_packages, self.c, "ghost")
        self.bind("<Escape>", lambda event: self.destroy())

    def _card_row(self, parent, title, note):
        """One setting in a card: the words on the left, room for its control on the right."""
        row = tk.Frame(parent, bg=self.c["card"], padx=18, pady=14)
        row.pack(fill="x")
        words = tk.Frame(row, bg=self.c["card"])
        words.pack(side="left")
        tk.Label(words, text=title, bg=self.c["card"], fg=self.c["text"], font=font(11, "bold")).pack(anchor="w")
        tk.Label(words, text=note, bg=self.c["card"], fg=self.c["muted"], font=font(9), justify="left",
                 wraplength=560).pack(anchor="w", pady=(2, 0))
        return row

    def _switch_row(self, parent, title, note, on, command):
        """One switch in a card: the words on the left, the switch on the right."""
        row = self._card_row(parent, title, note)
        switch = Switch(row, self.c, on, command)
        switch.pack(side="right", pady=(2, 0))
        return switch

    def _voice_row(self, parent, heading, note, command, first=False):
        row = tk.Frame(parent, bg=self.c["card"], padx=18, pady=14)
        row.pack(fill="x")
        words = tk.Frame(row, bg=self.c["card"])
        words.pack(side="left")
        tk.Label(words, text=heading, bg=self.c["card"], fg=self.c["muted"],
                 font=font(8, "bold")).pack(anchor="w")
        value = tk.Label(words, text="", bg=self.c["card"], fg=self.c["accent"] if first else self.c["text"],
                         font=font(13))
        value.pack(anchor="w", pady=(3, 0))
        tk.Label(words, text=note, bg=self.c["card"], fg=self.c["muted"], font=font(9)).pack(anchor="w",
                                                                                          pady=(2, 0))
        change = button(row, "Change", command, self.c, "ghost")
        change.pack(side="right", pady=(4, 0))
        return {"value": value, "change": change}

    def _section(self, parent, title, note, action):
        head = tk.Frame(parent, bg=self.c["bg"], padx=24)
        head.pack(fill="x", pady=(4, 8))
        words = tk.Frame(head, bg=self.c["bg"])
        words.pack(side="left")
        tk.Label(words, text=title, bg=self.c["bg"], fg=self.c["text"], font=font(12, "bold")).pack(anchor="w")
        tk.Label(words, text=note, bg=self.c["bg"], fg=self.c["muted"], font=font(9)).pack(anchor="w", pady=(1, 0))
        if action:
            button(head, action[0], action[1], self.c, "ghost", background=self.c["bg"]).pack(side="right", pady=(4, 0))

    # ── Showing the current state ──

    def _stamp(self):
        try:
            return talkback.SETTINGS.stat().st_mtime
        except OSError:
            return None

    def watch(self):
        """Follow a change made in the tray (or by hand) while this window is open."""
        stamp = self._stamp()
        if stamp and stamp != self.stamp:
            self.settings = talkback.Settings()
            self.stamp = self._stamp()    # loading writes the file: without this the window rewrites it every 2 s
            self.refresh()
        else:
            self.show_speaking()      # the tray can be clicked while this window is open: follow it
        self.after(2000, self.watch)

    def show_speaking(self):
        """The toggle follows the running program, and settings.json when there is no program to ask."""
        state = speaking(self.settings["port"])
        self.voice_switch.set(self.settings["enabled"] if state is None else state)

    def toggle_speaking(self, wanted):
        """Move at once, write the file, then tell the running program beside the window.

        The write is a few milliseconds; the request to the program is not, so it happens on a thread and the result
        comes back through `results`. That is what used to make the switch feel stuck for a second.
        """
        self.voice_switch.set(wanted)
        self.settings["enabled"] = wanted
        self.settings.save()
        self.stamp = self._stamp()
        threading.Thread(target=lambda: self.results.put(
            ("voice", wanted, set_speaking(self.settings["port"], wanted))), daemon=True).start()

    def refresh(self):
        """Everything on screen comes from one read of settings: the toggle, the two voice lines, the provider table."""
        self.show_speaking()

        primary = self.settings.source()
        self.primary["value"].configure(text=voice_line(primary) if primary else "\u2014")
        backup = free_source(self.settings)
        self.secondary["value"].configure(text=voice_line(backup) if backup else "No voice set up")
        self.secondary["change"].configure(state="normal" if backup else "disabled")


        self._provider_table()

        self.startup_on.set(startup_enabled())
        self.switch.set(self.startup_on.get())
        self.desktop_on.set(desktop_enabled())
        self.desktop_switch.set(self.desktop_on.get())
        missing = missing_packages()
        problems = [line for line in self.settings.problems if line]
        if problems or missing:            # the only lines worth a word: something is wrong, or something is missing
            self.status.configure(text=problems[0] if problems else "Missing: " + ", ".join(missing),
                                  fg=self.c["bad"] if problems else self.c["warn"])
        if missing and (HERE / "requirements.txt").is_file():
            self.install.pack(side="left", padx=(12, 0))
        else:
            self.install.pack_forget()
        if problems or missing:
            self.bottom.grid()
        else:
            self.bottom.grid_remove()
        self.fit()

    def fit(self):
        """Size the window to what it shows, so a third provider is not cut off and a short list is not padded."""
        self.update_idletasks()
        extra = self.bottom.winfo_reqheight() if self.bottom.winfo_manager() else 0  # grid_remove() keeps a size
        needed = self.body.winfo_reqheight() + extra + 2
        width = self.winfo_width() if self.winfo_width() > 1 else 1000
        self.geometry(f"{width}x{max(560, min(needed, self.winfo_screenheight() - 120))}")

    def _provider_table(self):
        rows = provider_rows(self.settings)
        signature = tuple((entry["id"], entry["label"], model_words(entry),
                           key_words(entry), entry["id"] == self.settings["primary"]) for entry in rows)
        if signature == self.provider_signature:
            return                    # nothing in the table changed, so it is not redrawn (a redraw is a white flash)
        self.provider_signature = signature
        for child in self.provider_card.winfo_children():
            child.destroy()
        if not rows:
            self._empty_row(self.provider_card, "No providers yet. Add one and give it a key.")
            return
        grid = table(self.provider_card, (("Provider", 4), ("Model", 3), ("Key", 2)), self.c)
        for number, entry in enumerate(rows):
            self._provider_row(grid, number + 1, entry)

    def _provider_row(self, table, row, entry):
        """One provider: its name (marked when primary, or the button that makes it so), model, key and Edit.

        Only the buttons do anything. The row does not open and does not light up under the pointer, so a mis-click
        on a table is a mis-click on nothing.
        """
        name = tk.Frame(table, bg=self.c["card"])
        name.grid(row=row, column=0, sticky="w", padx=(0, 14), pady=6)
        tk.Label(name, text=entry["label"], bg=self.c["card"], fg=self.c["text"],
                 font=font(10, "bold")).pack(side="left")
        if entry["id"] == self.settings["primary"]:
            tk.Label(name, text="(Primary)", bg=self.c["card"], fg=self.c["accent"],
                     font=font(9, "bold")).pack(side="left", padx=(8, 0))
        else:
            button(name, "Set as primary", lambda entry=entry: self.set_primary(entry), self.c,
                   "ghost").pack(side="left", padx=(8, 0))
        tk.Label(table, text=model_words(entry), bg=self.c["card"],
                 fg=self.c["muted"], font=font(10), anchor="w").grid(row=row, column=1, sticky="w",
                                                                     padx=(0, 14), pady=11)
        tk.Label(table, text=key_words(entry), bg=self.c["card"], fg=key_colour(entry, self.c),
                 font=font(10, "bold"), anchor="w").grid(row=row, column=2, sticky="w", padx=(0, 14), pady=11)
        button(table, "Edit", lambda entry=entry: self.edit_provider(entry), self.c,
               "ghost").grid(row=row, column=3, sticky="e", pady=5)

    def set_primary(self, entry):
        """Make this provider the one every reply is tried with first."""
        self.settings["primary"] = entry["id"]
        self.save_and_reload()

    def _empty_row(self, parent, words):
        tk.Label(parent, text=words, bg=self.c["card"], fg=self.c["muted"], font=font(9), padx=18,
                 pady=14).pack(anchor="w")

    # ── Changing things ──

    def save_and_reload(self):
        """Write settings.json, then have the running program read it, so the tray matches this window."""
        self.settings.save()
        self.stamp = self._stamp()
        running = reload_running(self.settings["port"])
        talkback.log(f"console: settings saved, program {'reloaded' if running else 'not running'}")
        self.refresh()

    def edit_provider(self, entry):
        ProviderDialog(self, entry).show()

    def add_provider(self):
        ProviderDialog(self, None).show()

    def change_primary(self):
        entry = self.settings.source()
        VoiceDialog(self, entry, secondary=False).show()

    def change_secondary(self):
        free = free_source(self.settings)
        if free:
            VoiceDialog(self, free, secondary=True).show()

    def change_talkers(self):
        TalkersDialog(self).show()

    def toggle_startup(self, wanted):
        """The switch moves at once; the shortcut is written on a thread, and snaps back if it could not be written."""
        self.startup_on.set(wanted)
        self.switch.set(wanted)
        threading.Thread(target=lambda: self.results.put(("startup", wanted, set_startup(wanted))),
                         daemon=True).start()

    def toggle_desktop(self, wanted):
        """The same for the desktop link: move at once, write or delete it on a thread, snap back if it failed."""
        self.desktop_on.set(wanted)
        self.desktop_switch.set(wanted)
        threading.Thread(target=lambda: self.results.put(("desktop", wanted, set_desktop(wanted))),
                         daemon=True).start()

    def drain(self):
        """What the switch threads finished, applied where Tk is safe: the one place they report back."""
        while True:
            try:
                kind, wanted, ok = self.results.get_nowait()
            except queue.Empty:
                break
            if kind == "voice" and not ok:
                talkback.log("console: voice switched, program not running")
            elif kind == "startup" and not (ok and startup_enabled() == wanted):
                self.startup_on.set(not wanted)
                self.switch.set(not wanted)
                messagebox.showerror("The shortcut was not changed",
                                     "Agent Talkback could not write its login shortcut in the Startup folder.",
                                     parent=self)
            elif kind == "desktop" and not (ok and desktop_enabled() == wanted):
                self.desktop_on.set(not wanted)
                self.desktop_switch.set(not wanted)
                messagebox.showerror("The shortcut was not changed",
                                     "Agent Talkback could not write its shortcut on the desktop.", parent=self)
        self.after(120, self.drain)

    def install_packages(self):
        requirements = HERE / "requirements.txt"
        if not requirements.is_file():
            return messagebox.showinfo("Install the packages", "Run: pip install edge-tts pystray Pillow",
                                       parent=self)
        subprocess.Popen([str(Path(sys.executable).with_name("python.exe")), "-m", "pip", "install",
                          "-r", str(requirements)], creationflags=0x00000010)


# ── The dialogs ───────────────────────────────────────────────────────────────────────────────────

class Dialog(tk.Toplevel):
    """A centered window over the console (or over the dialog it was opened from), in the same palette."""

    def __init__(self, app, title, width, height, over=None):
        super().__init__(over or app)
        self.app, self.c, self.width, self.height = app, app.c, width, height
        self.over = over or app
        self.title(title)
        self.configure(bg=self.c["bg"])
        self.transient(self.over)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda event: self.destroy())
        self.grab_set()

    def show(self):
        self.update_idletasks()
        self.fits = not self.height                              # None: as tall as what it holds, so nothing is cut
        self.height = self.height or self.winfo_reqheight()
        over = self.over
        x = over.winfo_rootx() + (over.winfo_width() - self.width) // 2
        y = over.winfo_rooty() + (over.winfo_height() - self.height) // 3
        self.geometry(f"{self.width}x{self.height}+{max(x, 0)}+{max(y, 0)}")
        dark_title_bar(self, self.c is DARK)
        self.wait_window(self)

    def refit(self):
        """After the content changed: as tall as it now is, where it already stands."""
        if getattr(self, "fits", False):
            self.update_idletasks()
            self.height = self.winfo_reqheight()
            self.geometry(f"{self.width}x{self.height}")

    def head(self, title, note=""):
        box = tk.Frame(self, bg=self.c["bg"], padx=22)
        box.pack(fill="x", pady=(20, 12))
        tk.Label(box, text=title, bg=self.c["bg"], fg=self.c["text"], font=font(16, "bold")).pack(anchor="w")
        if note:
            tk.Label(box, text=note, bg=self.c["bg"], fg=self.c["muted"], font=font(9), justify="left",
                     wraplength=self.width - 44).pack(anchor="w", pady=(4, 0))

    def message(self, words, bad=False):
        """One line under the content: what just happened, or why not."""
        self.note.configure(text=words, fg=self.c["bad"] if bad else self.c["good"])

    def footer(self, buttons, note_label=False):
        bar = tk.Frame(self, bg=self.c["bg"], padx=22, pady=16)
        bar.pack(fill="x", side="bottom")
        for text, command, kind in reversed(buttons):  # the last one listed is the rightmost
            button(bar, text, command, self.c, kind, background=self.c["bg"]).pack(side="right", padx=(8, 0))
        if note_label:
            self.note = tk.Label(bar, text="", bg=self.c["bg"], fg=self.c["muted"], font=font(9), anchor="w")
            self.note.pack(side="left")
        return bar

    def message_line(self):
        """The line above the buttons where a form says why it cannot save."""
        self.note = tk.Label(self, text="", bg=self.c["bg"], fg=self.c["muted"], font=font(9), anchor="w",
                             justify="left", wraplength=self.width - 44, padx=22)
        self.note.pack(side="bottom", fill="x")

    def _field(self, parent, key, label, value, hint, masked=False):
        tk.Label(parent, text=label, bg=self.c["bg"], fg=self.c["text"], font=font(9, "bold")).pack(anchor="w",
                                                                                                   pady=(0, 3))
        entry = entry_box(parent, self.c, show="•" if masked else "")
        entry.insert(0, value)
        entry.pack(fill="x", ipady=ENTRY_PAD, pady=(0, 2))
        tk.Label(parent, text=hint, bg=self.c["bg"], fg=self.c["muted"], font=font(8), justify="left",
                 wraplength=self.width - 60).pack(anchor="w", pady=(0, 8))
        self.entries[key] = entry

    def text(self, key):
        return self.entries[key].get().strip()


class ProviderDialog(Dialog):
    """Add or edit a provider. Edit is the one place for a provider: its name, endpoint and key, then its models.

    The models are changed in a working copy (`work`) and written with everything else on Save; Cancel drops them.
    Add provider keeps one form with the first model, because a provider needs one to exist.
    """

    VISIBLE_MODELS = 4            # from the fifth model on, the Models table scrolls

    def __init__(self, app, entry):
        super().__init__(app, "Edit provider" if entry else "Add provider", 640 if entry else 580,
                         None if entry else 700)
        self.entry, self.entries = entry, {}
        self.work = copy.deepcopy(entry) if entry else None
        self.head("Edit provider" if entry else "Add provider",
                  "Agent Talkback uses these to call the provider's speech endpoint. The key is saved in "
                  "secrets/, never in settings.json or git.")
        self.message_line()
        self.footer([("Cancel", self.destroy, "ghost"),
                     ("Save" if entry else "Add provider", self.save, "accent")])
        form = tk.Frame(self, bg=self.c["bg"], padx=22)
        form.pack(fill="both", expand=True, pady=(0, 6))
        self._field(form, "name", "Provider name", entry["label"] if entry else "",
                    "The name shown in the tray and this window, such as OpenRouter.")
        self._field(form, "endpoint", "Endpoint", entry.get("base_url", "") if entry else "",
                    "The OpenAI-compatible base URL, such as https://openrouter.ai/api/v1")
        if entry:
            self._field(form, "key", "API key", "", "Leave blank to keep the saved key.", masked=True)
            self._models(form)
        else:
            self._field(form, "model", "Model", "",
                        "The speech model id at that endpoint, such as heygen/heygen-voice-1-turbo:free")
            self._field(form, "voice", "Voice", "",
                        "A voice id this model accepts. Change, next to Primary voice, lists the ones set up.")
            self._field(form, "key", "API key", "", "Paste it once. It is saved in secrets/ and never shown again.",
                        masked=True)
            self._field(form, "limit", "Max characters per request", "",
                        "Optional. For a model that refuses long text. Groq's limit is 200.")
        self.bind("<Return>", lambda event: self.save())
        self.entries["name"].focus_set()

    def _models(self, parent):
        """The Models part: a head with + Add model on its right, the table, and the line that says when it saves."""
        head = tk.Frame(parent, bg=self.c["bg"])
        head.pack(fill="x", pady=(8, 8))
        tk.Label(head, text="Models", bg=self.c["bg"], fg=self.c["text"], font=font(12, "bold")).pack(side="left")
        button(head, "+  Add model", lambda: self.open_model(None), self.c, "ghost",
               background=self.c["bg"]).pack(side="right")
        self.models_card = card(parent, self.c)
        self.models_card.pack(fill="x")
        tk.Label(parent, text="Press Save to keep changes to models.", bg=self.c["bg"], fg=self.c["muted"],
                 font=font(8)).pack(anchor="w", pady=(4, 0))
        self.draw_models()

    def draw_models(self):
        """One row per model of the working copy, in its order: number, model, voice, (Primary), Edit, Remove."""
        for child in self.models_card.winfo_children():
            child.destroy()
        sets, in_use = self.work["sets"], talkback.active_set(self.work)["name"]
        holder = self.models_card
        if len(sets) > self.VISIBLE_MODELS:
            scroll = Scroll(self.models_card, self.c, height=44 * self.VISIBLE_MODELS + 40)
            scroll.pack(fill="x")
            holder = scroll.body
        grid = table(holder, (("", 0), ("Model", 4), ("Voice", 2), ("", 0)), self.c)
        for row, voice_set in enumerate(sets, start=1):
            tk.Label(grid, text=f"Model {row}", bg=self.c["card"], fg=self.c["muted"], font=font(9),
                     anchor="w").grid(row=row, column=0, sticky="w", padx=(0, 14), pady=9)
            tk.Label(grid, text=voice_set.get("model") or "—", bg=self.c["card"], fg=self.c["text"],
                     font=font(10), anchor="w").grid(row=row, column=1, sticky="w", padx=(0, 14))
            tk.Label(grid, text=talkback.voice_name(voice_set["voice"]), bg=self.c["card"], fg=self.c["text"],
                     font=font(10), anchor="w").grid(row=row, column=2, sticky="w", padx=(0, 14))
            if voice_set["name"] == in_use:
                tk.Label(grid, text="(Primary)", bg=self.c["card"], fg=self.c["accent"],
                         font=font(9, "bold")).grid(row=row, column=3, sticky="w", padx=(0, 8))
            actions = tk.Frame(grid, bg=self.c["card"])
            actions.grid(row=row, column=4, sticky="e", pady=3)
            button(actions, "Edit", lambda name=voice_set["name"]: self.open_model(name), self.c,
                   "ghost").pack(side="left")
            if len(sets) > 1:
                button(actions, "Remove", lambda name=voice_set["name"]: self.remove_model(name), self.c,
                       "ghost").pack(side="left")
        self.refit()

    def open_model(self, name):
        """Add model (name None) or Edit model, over this dialog; this dialog takes the grab back afterwards."""
        ModelDialog(self, name).show()
        try:
            self.grab_set()
        except tk.TclError:          # this dialog was closed meanwhile
            pass

    def remove_model(self, name):
        self.work = without_model(self.work, name)
        self.draw_models()

    def save(self):
        name, endpoint, key = self.text("name"), self.text("endpoint"), self.text("key")
        if not name:
            return self.message("Enter a name for the provider.", bad=True)
        if not endpoint.startswith(("https://", "http://")):
            return self.message("The endpoint must start with https:// or http://.", bad=True)
        if self.entry:
            spec = copy.deepcopy(self.work)
        else:
            spec = self._new_spec(name)
            if spec is None:
                return
        spec.update(label=name, base_url=endpoint, key=spec.get("key") or f"file:secrets/{spec['id']}.local")
        clean, problems = talkback.clean_source(spec)
        if clean is None:
            return self.message(problems[0] if problems else "That provider cannot be used.", bad=True)
        if key:
            try:
                talkback.save_secret(clean["id"], key)
            except (OSError, ValueError) as error:
                return self.message(str(error), bad=True)
            clean["key"] = f"file:secrets/{clean['id']}.local"
        sources = self.app.settings["sources"]
        if self.entry:
            sources[[entry["id"] for entry in sources].index(clean["id"])] = clean
        else:
            sources.append(clean)
            if talkback.can_speak(clean):
                self.app.settings["primary"] = clean["id"]
        self.app.save_and_reload()
        self.destroy()

    def _new_spec(self, name):
        """Add provider: the new source with its first model, or None after saying what is missing."""
        model, voice, limit = self.text("model"), self.text("voice"), self.text("limit")
        if not model:
            return self.message("Enter a model id this provider accepts.", bad=True)
        if not voice:
            return self.message("Enter a voice id.", bad=True)
        if limit and not (limit.isdigit() and int(limit) >= 40):
            return self.message("Max characters is a whole number, 40 or more, or blank.", bad=True)
        voice_set = {"name": "Default", "model": model, "voice": voice, "voices": [voice]}
        if limit:
            voice_set["request_limit"] = int(limit)
        source_id = new_id(name, {entry["id"] for entry in self.app.settings["sources"]})
        return {"id": source_id, "engine": "openai", "set": "Default", "sets": [voice_set]}


class ModelDialog(Dialog):
    """Add model or Edit model, opened from Edit provider. It changes that dialog's working copy, not settings."""

    def __init__(self, owner, name=None):
        voice_set = next((item for item in owner.work["sets"] if item["name"] == name), None)
        title = "Edit model" if voice_set else "Add model"
        super().__init__(owner.app, title, 520, None, over=owner)
        self.owner, self.name, self.entries = owner, name, {}
        voice_set = voice_set or {}
        label = owner.work["label"]
        self.head(title, f"A voice model for {label}, on the same endpoint and key." if voice_set else
                  f"Another voice model for {label}, on the same endpoint and key.")
        self.message_line()
        self.footer([("Cancel", self.destroy, "ghost"), ("Save" if voice_set else "Add model", self.save, "accent")])
        form = tk.Frame(self, bg=self.c["bg"], padx=22)
        form.pack(fill="both", expand=True, pady=(0, 6))
        self._field(form, "model", "Model", voice_set.get("model", ""),
                    f"The speech model id at {label}, such as fish-audio/s2.1-pro-free:free")
        self._field(form, "voice", "Voice", voice_set.get("voice", ""), "A voice id this model accepts.")
        self._field(form, "limit", "Max characters per request", str(voice_set.get("request_limit") or ""),
                    "Optional. For a model that refuses long text. Groq's limit is 200.")
        self.bind("<Return>", lambda event: self.save())
        self.entries["model"].focus_set()

    def save(self):
        limit = self.text("limit")
        if limit and not (limit.isdigit() and int(limit) >= 40):
            return self.message("Max characters is a whole number, 40 or more, or blank.", bad=True)
        if self.name:
            clean, problem = edited_model(self.owner.work, self.name, self.text("model"), self.text("voice"), limit)
        else:
            clean, problem = with_model(self.owner.work, self.text("model"), self.text("voice"), limit)
        if clean is None:
            return self.message(problem, bad=True)
        self.owner.work = clean
        self.owner.draw_models()
        self.destroy()


class VoiceDialog(Dialog):
    """The list behind Change: the voices of one model as tiles, with Test under each. A click picks it."""

    def __init__(self, app, entry, secondary):
        super().__init__(app, "Backup voice" if secondary else "Primary voice", 700, 620)
        self.entry, self.secondary, self.rows = entry, secondary, []
        self.current = self._current()
        self.pending = None
        self.mark = "Backup" if secondary else "Primary"
        self.head("Backup voice" if secondary else "Primary voice",
                  "Microsoft Edge's voices speak when the primary voice fails. They are free and need no key." if secondary else
                  "Choose your primary voice below.")
        self.filter_box = None
        if secondary:
            holder = tk.Frame(self, bg=self.c["bg"], padx=22)
            holder.pack(fill="x")
            self.query = tk.StringVar()
            box = entry_box(holder, self.c, textvariable=self.query)
            box.pack(fill="x", ipady=ENTRY_PAD)
            box.bind("<KeyRelease>", self.filter_later)
            tk.Label(holder, text="Type to filter: a name, or a language such as en-GB.", bg=self.c["bg"],
                     fg=self.c["muted"], font=font(8)).pack(anchor="w", pady=(4, 8))
            self.filter_box = box
        self.tab = talkback.active_set(entry)["name"]      # the list opens on the model in use
        self.tab_bar = None
        if not secondary and len(entry["sets"]) > 1:
            holder = tk.Frame(self, bg=self.c["bg"], padx=22)
            holder.pack(fill="x", pady=(0, 10))
            self.tab_bar = tk.Frame(holder, bg=self.c["bg"])
            self.tab_bar.pack(anchor="w")
            self.tab_model = tk.Label(holder, text="", bg=self.c["bg"], fg=self.c["muted"], font=font(9))
            self.tab_model.pack(anchor="w", pady=(2, 0))
        self.footer([("Done", self.destroy, "accent")], note_label=True)
        self.voice_list = Scroll(self, self.c, height=self.height - (190 if secondary else
                                                                     210 if self.tab_bar else 150))
        self.voice_list.pack(fill="both", expand=True, padx=22)
        self._fill()
        if secondary:
            # The Edge list comes over the network: fetch it beside the window, then read it here.
            self.fetched = queue.Queue()
            threading.Thread(target=lambda: self.fetched.put(edge_voices()), daemon=True).start()
            self.after(300, self._poll_edge)

    def _current(self):
        voice_set = talkback.active_set(self.entry)
        return voice_set["name"], voice_set["voice"]

    def set_name(self):
        return talkback.active_set(self.entry)["name"]

    def _fill(self):
        self.rows = voice_rows(self.entry, self.tab)   # in settings' order: the voice in use is marked, never moved
        self.render()

    def show_tab(self, set_name):
        self.tab = set_name
        self._fill()

    def _tabs(self):
        """Model 1, Model 2 ...: the shown one in bold and underlined, the one in use followed by '(Primary)'."""
        for child in self.tab_bar.winfo_children():
            child.destroy()
        for tab in model_tabs(self.entry):
            shown = tab["set"] == self.tab
            pick = (lambda event=None, name=tab["set"]: self.show_tab(name))
            item = tk.Frame(self.tab_bar, bg=self.c["bg"])
            item.pack(side="left", padx=(0, 22))
            label = button(item, tab["label"], pick, self.c, "ghost" if shown else "quiet", background=self.c["bg"])
            label.configure(font=font(10, "bold underline" if shown else "normal"), padx=0, pady=2)
            label.pack(side="left")
            if tab["in_use"]:
                mark = tk.Label(item, text="(Primary)", bg=self.c["bg"], fg=self.c["accent"], font=font(8, "bold"),
                                cursor="hand2")
                mark.pack(side="left", padx=(5, 0))
                mark.bind("<Button-1>", pick)
            if shown:
                self.tab_model.configure(text=tab["model"])

    def _poll_edge(self):
        try:
            voices = self.fetched.get_nowait()
        except queue.Empty:
            return self.after(300, self._poll_edge)
        if voices:
            self._add_edge(voices)
        else:
            self.message("Could not get Microsoft's voice list. Showing the voices in your settings.", bad=True)

    def _add_edge(self, voices):
        """Every English Edge voice, alphabetically, with a configured one merged in where it sorts.

        Only the en- ones: the full catalogue is 322 voices across every language, and the others only get in the way
        of the one thing this window does. The voice in use is marked where it sits, not lifted to the top.
        """
        notes = {name: f"{locale} \u00b7 {gender.lower()}"
                 for name, locale, gender in voices if locale.lower().startswith("en")}
        rows = [{"set": self.set_name(), "voice": name, "note": notes[name]} for name in sorted(notes)]
        known = {row["voice"] for row in rows}
        for row in self.rows:              # a voice already in settings is part of the list, wherever it sorts
            row["note"] = notes.get(row["voice"], row["note"])
            if row["voice"] not in known:
                rows.append(row)
        rows.sort(key=lambda row: row["voice"])
        self.rows = rows
        self.render()

    def filter_later(self, event=None):
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(220, self.render)

    def render(self):
        self.pending = None
        query = (self.query.get().strip().lower() if self.filter_box else "")
        rows = [row for row in self.rows if query in (row["voice"] + " " + row["note"]).lower()]
        if self.tab_bar:
            self._tabs()
        self.voice_list.clear()
        if not rows:
            tk.Label(self.voice_list.body, text="No voice matches that.", bg=self.c["card"],
                     fg=self.c["muted"], font=font(9), padx=14, pady=16).grid(row=0, column=0, sticky="w")
            return
        for column in range(TILES_ACROSS):
            self.voice_list.body.grid_columnconfigure(column, weight=1, uniform="tile")
        for place, row in enumerate(rows[:MAX_LISTED]):
            self._tile(row, place)
        if len(rows) > MAX_LISTED:
            tk.Label(self.voice_list.body, text=f"{len(rows) - MAX_LISTED} more. Type in the filter to narrow the list.",
                     bg=self.c["card"], fg=self.c["muted"], font=font(8), pady=10).grid(
                         row=MAX_LISTED // TILES_ACROSS + 1, column=0, columnspan=TILES_ACROSS, sticky="w")
        self.message(f"{len(rows)} voices.")

    def _tile(self, row, place):
        """One voice as a tile, three to a line: one outlined box in two parts, the voice above and Test below.

        The upper part picks the voice; the strip under the dividing line only tests it. Both are inside one outline,
        so a Test plainly belongs to its voice, and each part lights up on its own under the pointer, so it is clear
        which of the two a click will do. The voice in use has the accent outline, the soft tint and a tick.
        """
        chosen = (row["set"], row["voice"]) == self.current
        cell = tk.Frame(self.voice_list.body, bg=self.c["card"])
        line, column = divmod(place, TILES_ACROSS)
        cell.grid(row=line, column=column, sticky="nsew", padx=(10 if column == 0 else 0, 10),
                  pady=(10 if line == 0 else 0, 10))
        edge = self.c["accent"] if chosen else self.c["line"]
        resting = self.c["accent_soft"] if chosen else self.c["card"]
        box = tk.Frame(cell, bg=edge, highlightthickness=1, highlightbackground=edge)
        box.pack(fill="x")
        pick = tk.Frame(box, bg=resting, padx=12, pady=9)
        pick.pack(fill="x")
        top = tk.Frame(pick, bg=resting)
        top.pack(fill="x")
        name = tk.Label(top, text=talkback.voice_name(row["voice"]), bg=resting,
                        fg=self.c["accent"] if chosen else self.c["text"], font=font(10, "bold"), anchor="w")
        name.pack(side="left")
        parts = [pick, top, name]
        if chosen:
            tick = tk.Label(top, text="\u2713", bg=resting, fg=self.c["accent"], font=font(10, "bold"))
            tick.pack(side="right")
            parts.append(tick)
        if row["note"] != row["set"]:          # a model's own name is already on its tab; a language is worth a line
            note = tk.Label(pick, text=row["note"], bg=resting, fg=self.c["muted"], font=font(8), anchor="w")
            note.pack(anchor="w")
            parts.append(note)
        test = tk.Label(box, text="\u25B6  Test", bg=self.c["card"], fg=self.c["muted"], font=font(8), anchor="w",
                        padx=12, pady=5, cursor="hand2")
        test.pack(fill="x", pady=(1, 0))       # the gap shows the box's own colour: the line between the two parts
        test.bind("<Button-1>", lambda event, row=row: self.test(row))
        test.bind("<Enter>", lambda event: test.configure(bg=self.c["hover"], fg=self.c["text"]))
        test.bind("<Leave>", lambda event: test.configure(bg=self.c["card"], fg=self.c["muted"]))

        def tint(on):                 # a tint under the pointer is the only hover: the chosen tile keeps its own colour
            colour = resting if chosen or not on else self.c["hover"]
            for widget in parts:
                widget.configure(bg=colour)

        for widget in parts:
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", lambda event, row=row: self.choose(row))
            widget.bind("<Enter>", lambda event: tint(True))
            widget.bind("<Leave>", lambda event: tint(False))

    def choose(self, row):
        if self.secondary:
            self.app.settings.set_voice(self.entry["id"], row["set"], row["voice"])
        else:
            self.app.settings.pick_voice(self.entry["id"], row["set"], row["voice"])
        self.app.save_and_reload()
        self.current = (row["set"], row["voice"])
        self.render()

    def test(self, row):
        """Speak the test line in that voice alone, without making it the one that speaks."""
        if not talkback.can_speak(self.entry):
            return self.message("Add a key for this provider first.", bad=True)
        if speaking(self.app.settings["port"]) is False:
            return self.message("Speaking is off. Click the tray icon to switch it on.", bad=True)
        if not send.deliver(test_words(row["voice"]), "console", self.app.settings["port"],
                            only=self.entry["id"], set_name=row["set"], voice=row["voice"]):
            return self.message("Agent Talkback is not running. Start it with pythonw talkback.py.", bad=True)
        self.message(f"Testing in {talkback.voice_name(row['voice'])}.")


class TalkersDialog(Dialog):
    """One row per folder heard: a switch for a custom voice, and the tray's voice menu to choose it.

    A change redraws only its own row, so no row jumps.
    """

    def __init__(self, app):
        super().__init__(app, "Voices by Agent", 700, 620)
        self.head("Voices by Agent", "Your agents' folders are listed after their first spoken replies. Toggle to turn "
                                     "on custom voice for the folders.")
        self.footer([("Done", self.destroy, "ghost")], note_label=True)
        holder = card(self, self.c)
        holder.pack(fill="both", expand=True, padx=22)
        self.rows = Scroll(holder, self.c, height=self.height - 190)
        self.rows.pack(fill="both", expand=True)
        self.rows.body.grid_columnconfigure(0, weight=1)
        self.bind("<MouseWheel>", self.rows.scroll)  # the wheel works over a row's own widgets too
        self.lines = {}                              # talker id: its widgets
        self.render()

    def render(self):
        self.rows.clear()
        self.lines = {}
        rows = talker_rows(self.app.settings)
        if not rows:
            tk.Label(self.rows.body, text="No agent folders yet.", bg=self.c["card"], fg=self.c["muted"],
                     font=font(9), padx=18, pady=16).grid(row=0, column=0, sticky="w")
        for number, row in enumerate(rows):
            self.lines[row["id"]] = self._line(number, row)
            self._show(row)

    def _line(self, number, row):
        talker_id = row["id"]
        holder = tk.Frame(self.rows.body, bg=self.c["card"])
        holder.grid(row=number, column=0, sticky="ew")
        if number:                                   # inset on the left by what the scroll thumb takes on the right
            divider(holder, self.c).pack_configure(padx=(Scroll.THUMB + 10, 0))
        line = tk.Frame(holder, bg=self.c["card"], padx=18, pady=14)
        line.pack(fill="x")
        words = tk.Frame(line, bg=self.c["card"])
        switch = Switch(line, self.c, row["on"], lambda on: self.toggle(talker_id, on))
        switch.pack(side="right")
        selector = tk.Menubutton(line, bg=self.c["card"], fg=self.c["text"], activebackground=self.c["hover"],
                                 activeforeground=self.c["text"], disabledforeground=self.c["muted"],
                                 font=font(10), relief="flat", bd=0, highlightthickness=1,
                                 highlightbackground=self.c["line"], width=30, anchor="w", padx=10, pady=5)
        selector.pack(side="right", padx=(0, 16))
        words.pack(side="left")
        tk.Label(words, text=row["folder"], bg=self.c["card"], fg=self.c["text"], font=font(11, "bold"),
                 justify="left", wraplength=270).pack(anchor="w")
        warning = tk.Label(words, text="", bg=self.c["card"], fg=self.c["warn"], font=font(9), justify="left",
                           wraplength=270)
        chosen = tk.StringVar()
        selector.configure(menu=self._voice_cascade(selector, talker_id, chosen))
        return {"switch": switch, "selector": selector, "chosen": chosen, "warning": warning}

    def _voice_cascade(self, selector, talker_id, chosen):
        """Provider > model > voice, from the same `voice_menu()` the tray's menu is built from."""
        look = {"tearoff": 0, "font": font(10), "bg": self.c["card"], "fg": self.c["text"],
                "activebackground": self.c["hover"], "activeforeground": self.c["text"], "selectcolor": self.c["text"]}
        menu = tk.Menu(selector, **look)
        for provider in talkback.voice_menu(self.app.settings):
            models = tk.Menu(menu, **look)
            for voice_set in provider["sets"]:
                voices = tk.Menu(models, **look)
                for voice in voice_set["voices"]:
                    choice = {"source": provider["id"], "set": voice_set["name"], "voice": voice}
                    voices.add_radiobutton(label=talkback.voice_name(voice), variable=chosen,
                                           value=json.dumps(choice, sort_keys=True),
                                           command=lambda choice=choice: self.change(talker_id, choice))
                models.add_cascade(label=voice_set["name"], menu=voices)
            menu.add_cascade(label=provider["label"], menu=models)
        return menu

    def _show(self, row):
        """Bring one row up to date: switched off, the selector is greyed and shows the automatic voice."""
        line = self.lines[row["id"]]
        line["switch"].set(row["on"])
        line["chosen"].set(json.dumps(row["choice"], sort_keys=True))
        line["selector"].configure(text=row["label"] + "  \u25be", state="normal" if row["on"] else "disabled",
                                   cursor="hand2" if row["on"] else "arrow")
        if row["lacking"]:
            line["warning"].configure(text=f"{row['lacking']} is no longer available. Using the automatic voice.")
            line["warning"].pack(anchor="w", pady=(2, 0))
        else:
            line["warning"].pack_forget()

    def toggle(self, talker_id, on):
        """On starts from the voice the folder has been speaking in; off hands it back to the automatic one."""
        self.change(talker_id, automatic_choice(self.app.settings, talker_id) if on else None)

    def change(self, talker_id, choice):
        if not self.app.settings.set_talker(talker_id, voice=choice):
            self.render()
            return self.message("That folder is no longer listed.", bad=True)
        self.app.save_and_reload()
        self._show(next(row for row in talker_rows(self.app.settings) if row["id"] == talker_id))
        self.message("Saved.")


def main():
    Console().mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        talkback.log(f"console stopped: {e!r}")
        raise
