"""Talkback: one Windows program that speaks your agents' replies.

Agents POST their reply to http://127.0.0.1:<port>/say; this program decides what part is spoken (the
In short / From you header, else the old speak.py rules), which voice speaks it, and whether to speak at all.
Voices are a list of sources in settings.json: an endpoint, a key and a voice each, tried in order. Keys live
in secrets/, beside this program, and console.py is where a person puts them. The tray icon holds the switches.
See README.md.
"""
import asyncio
import copy
import ctypes
import datetime
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
SETTINGS = HERE / "settings.json"
SECRETS = HERE / "secrets"      # one file per source, named after its id; never in git
LOG = HERE / "talkback.log"

SETTINGS_VERSION = 3
MAX_SOURCES = 5          # shipped builds carry 5; raise this number to use more (README: Your own voices)
MAX_SETS = 8             # voice sets (models) one source may hold; raise this number to use more
MICROSOFT_RATE = "+14%"
MAX_CHARS = 800          # a longer reply is spoken from its first sentence or two only
MAX_BODY = 100_000       # the most bytes this program reads from one request: a reply, not a payload
MAX_QUEUE = 24           # replies waiting at most; past this the queue answers 429 and logs it
_SAVE_LOCK = threading.Lock()   # one writer of settings.json, whichever thread asks
_AUDIO_LOCK = threading.Lock()  # one creator of the private audio folder
AUDIO = None             # this process's own temp folder for speech, made on the first reply

# The two ways a source can be spoken. `openai` covers OpenRouter, Groq and anything else that speaks the
# OpenAI /audio/speech shape; `microsoft` is edge-tts and needs no key.
ENGINES = {"openai": "an OpenAI-compatible /audio/speech endpoint, with a key",
           "microsoft": "edge-tts, no key needed"}

# Three layers, in the tray and in this file: a source (endpoint + key), its voice sets (one model each), and the
# voices inside a set. The primary source is tried first and the keyless Windows voice second, so there is always
# a voice left; speech_order() says the rest. Per source: `set` is the set in use. Per set: `model`, `voice` (the
# one in use), `voices` (what the tray offers) and `request_limit` (the most characters that model takes per request).
SOURCES = [
    {"id": "openrouter", "label": "OpenRouter", "engine": "openai",
     "base_url": "https://openrouter.ai/api/v1", "key": "file:secrets/openrouter.local", "set": "HeyGen",
     "sets": [{"name": "HeyGen", "model": "heygen/heygen-voice-1-turbo:free", "voice": "bram",
                "voices": ["bram", "nadine", "tabitha", "gareth", "noelle", "meredith", "sabrina", "lucian",
                           "weston", "vincent"], "format": "pcm"}]},
    {"id": "groq", "label": "Groq", "engine": "openai",
     "base_url": "https://api.groq.com/openai/v1", "key": "file:secrets/groq.local", "set": "Orpheus",
     "sets": [{"name": "Orpheus", "model": "canopylabs/orpheus-v1-english", "voice": "autumn",
                "voices": ["autumn", "diana", "hannah", "austin", "daniel", "troy"],
                "request_limit": 200}]},
    {"id": "microsoft", "label": "Free (for Windows)", "engine": "microsoft", "rate": MICROSOFT_RATE,
     "set": "Microsoft Edge",
     "sets": [{"name": "Microsoft Edge", "voice": "en-US-GuyNeural",
                "voices": ["en-US-GuyNeural", "en-US-AriaNeural", "en-US-JennyNeural", "en-GB-SoniaNeural",
                           "en-AU-NatashaNeural"]}]},
]
DEFAULTS = {"version": SETTINGS_VERSION, "enabled": True, "port": 47800, "primary": "openrouter",
            "sources": SOURCES}
RENAMED_LABELS = {"Default (Free)": "Free (for Windows)"}   # an older name, kept so a saved file picks it up


# ── What is spoken: the In short / From you header, else a short reply whole, else its first sentences ──

def _is_mostly_code(text):
    code_chars = sum(len(m.group(0)) for m in re.finditer(r"```.*?```", text, re.DOTALL))
    return len(text) > 0 and (code_chars / len(text)) > 0.40


def _is_multi_bullet(text):
    return len(re.findall(r"^\s*[-*+]\s+|^\s*\d+\.\s+", text, re.MULTILINE)) >= 3


def _looks_like_tool_output(text):
    lines = text.strip().splitlines()
    if not lines:
        return False
    noisy = sum(1 for l in lines if l.startswith(("/", "C:\\", "|", "+", "-", "{", "[", "  modified:", "  new file:")))
    return len(lines) > 3 and (noisy / len(lines)) > 0.50


def _strip_for_speech(text):
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"#{1,6}\s+", "", text)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,2}([^_]+)_{1,2}", r"\1", text)
    text = re.sub(r"~~([^~]+)~~", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^\|.*\|$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*_]{3,}$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    for dash in ("\u2014", "\u2013", "\u2012", "\u2212"):
        text = text.replace(dash, ", ")
    text = re.sub(r"\s+-\s+", ", ", text)
    text = re.sub(r"\s+,", ",", text)
    for symbol, word in {"\u20ac": "euros", "\u00a3": "pounds", "$": "dollars"}.items():
        text = re.sub(re.escape(symbol) + r"\s*(\d[\d,]*(?:\.\d+)?)", lambda m: f"{m.group(1)} {word}", text)
        text = text.replace(symbol, " " + word + " ")
    text = text.replace("%", " percent ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    for pattern, said in {r"\bWACC\b": "wack", r"\bJSON\b": "jason", r"\bf2f\b": "face to face", r"\bLLM\b": "L L M",
                          r"\bMCP\b": "M C P", r"\bAPI\b": "A P I", r"\bTTS\b": "T T S", r"\bDCF\b": "D C F",
                          r"\bROIC\b": "R O I C", r"\bCAGR\b": "C A G R", r"\bHTML\b": "H T M L", r"\bURL\b": "U R L",
                          r"\bPR\b": "P R"}.items():
        text = re.sub(pattern, said, text)
    return text.strip()


_HEADER_LABEL = re.compile(r"^\s*(?:>\s*)?\*\*(In short|From you):\*\*\s*(.*)$", re.IGNORECASE)


def _spoken_header(text):
    """From 'In short:' to the end of the 'From you:' paragraph; each runs to the next blank line."""
    parts, label = {}, None
    for line in text.splitlines()[:40]:
        m = _HEADER_LABEL.match(line)
        if m:
            label = m.group(1).lower()
            parts[label] = [m.group(2).strip()]
        elif label and line.strip():
            parts[label].append(re.sub(r"^\s*>\s*", "", line).strip())
        else:
            label = None
    summary = _strip_for_speech(" ".join(parts.get("in short", [])))
    ask = _strip_for_speech(" ".join(parts.get("from you", [])))
    if not summary:
        return ""
    return summary + (" From you: " + ask if ask else "")


def _first_sentences(text, limit=220):
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line.strip() or re.match(r"^\s*(#{1,6}\s|\||[-*+]\s|\d+\.\s|>)", line):
            continue
        para = _strip_for_speech(line)
        if len(para) < 10:
            continue
        if len(para) <= limit:
            return para
        cut = max(para.rfind(". ", 0, limit), para.rfind("? ", 0, limit), para.rfind("! ", 0, limit))
        return para[: cut + 1] if cut > 30 else para[:limit].rsplit(" ", 1)[0]
    return ""


def what_to_say(message):
    """(text, how): the header if there is one; a short plain reply whole; else its first sentence or two."""
    said = _spoken_header(message)
    if said:
        return said, "header"
    if len(message) > MAX_CHARS or _is_mostly_code(message) or _looks_like_tool_output(message) or _is_multi_bullet(message):
        return _first_sentences(message), "first sentences"
    return _strip_for_speech(message), "whole"


# ── Sources: one endpoint, one key, one voice each ───────────────────────────────────────────────

SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
KEY_SPEC = re.compile(r"^(?:env:[A-Za-z_][A-Za-z0-9_]*|file:.+)$")
RATE = re.compile(r"^[+-]\d{1,3}%$")
# What an OpenAI-compatible endpoint may answer with. Both are read into one WAV, so the player never changes; a
# provider usually takes only one of them (HeyGen Voice: pcm only), and FALLBACK_FORMAT is tried when it says so.
FORMATS = ("wav", "pcm")


def clean_set(spec, index, engine):
    """(set, problem): one voice set of a source made safe to use, or the reason it cannot be used."""
    if not isinstance(spec, dict):
        return None, f"voice set {index + 1}: not an object"
    name = str(spec.get("name") or "").strip()
    if not name:
        return None, f"voice set {index + 1}: no name"
    voice = str(spec.get("voice") or "").strip()
    if not voice:
        return None, f"{name}: no voice"
    voices = spec.get("voices")
    entry = {"name": name, "voice": voice,
             "voices": list(dict.fromkeys(str(v).strip() for v in voices if str(v).strip()))
                       if isinstance(voices, list) else []}
    if engine == "openai":
        model = str(spec.get("model") or "").strip()
        if not model:
            return None, f"{name}: no model"
        entry["model"] = model
        if isinstance(spec.get("request_limit"), int) and spec["request_limit"] >= 40:
            entry["request_limit"] = spec["request_limit"]
        if spec.get("format"):
            fmt = str(spec["format"]).strip().lower()
            if fmt not in FORMATS:
                return None, f"{name}: format must be one of {', '.join(FORMATS)}"
            entry["format"] = fmt
    return entry, ""


def clean_sets(raw, engine):
    """(sets, problems): the usable voice sets in list order, at most MAX_SETS."""
    sets, problems, seen = [], [], set()
    for index, spec in enumerate(raw if isinstance(raw, list) else []):
        entry, problem = clean_set(spec, index, engine)
        if entry is None:
            problems.append(problem)
        elif entry["name"] in seen:
            problems.append(f"voice set {entry['name']!r}: duplicate name; only the first one is used")
        elif len(sets) == MAX_SETS:
            problems.append(f"only the first {MAX_SETS} voice sets of a source are used; raise MAX_SETS")
            break
        else:
            seen.add(entry["name"])
            sets.append(entry)
    return sets, problems


def clean_source(spec, index=0):
    """(entry, problems): one source made safe to use, or the reasons it cannot be used."""
    spec = _one_set(spec) if isinstance(spec, dict) else spec  # a row written before sets existed
    if not isinstance(spec, dict):
        return None, [f"source {index + 1}: not an object"]
    sid = str(spec.get("id") or "").strip()
    if not SOURCE_ID.match(sid):
        return None, [f"source {index + 1}: id {sid!r} must be lower-case letters, digits and dashes"]
    engine = str(spec.get("engine") or "").strip()
    if engine not in ENGINES:
        return None, [f"{sid}: engine {engine!r} is not one of {', '.join(ENGINES)}"]
    label = str(spec.get("label") or sid)
    entry = {"id": sid, "label": RENAMED_LABELS.get(label, label), "engine": engine,
             "enabled": bool(spec.get("enabled", True))}
    if engine == "openai":
        base = str(spec.get("base_url") or "").strip().rstrip("/")
        if not base.startswith(("https://", "http://")):
            return None, [f"{sid}: base_url must be an http(s) address"]
        key = str(spec.get("key") or "").strip()
        if not KEY_SPEC.match(key):
            return None, [f"{sid}: key must be 'env:NAME' or 'file:path'"]
        entry.update(base_url=base, key=key)
    else:
        entry["rate"] = str(spec.get("rate") or MICROSOFT_RATE)
        if not RATE.match(entry["rate"]):
            return None, [f"{sid}: rate {entry['rate']!r} must look like '+14%' or '-5%'"]
    sets, problems = clean_sets(spec.get("sets"), engine)
    if not sets:
        return None, [f"{sid}: {problems[0] if problems else 'no voice sets'}"]
    entry["sets"] = sets
    wanted = str(spec.get("set") or "").strip()
    entry["set"] = next((item["name"] for item in sets if item["name"] == wanted), sets[0]["name"])
    return entry, problems


def clean_sources(raw):
    """(sources, problems): the usable sources in order, at most MAX_SOURCES, never empty."""
    if not isinstance(raw, list) or not raw:
        return copy.deepcopy(SOURCES), ["no sources in settings.json; the built-in ones are in use"]
    sources, problems, seen = [], [], set()
    for index, spec in enumerate(raw):
        entry, why = clean_source(spec, index)
        if entry is None:
            problems += why
        elif entry["id"] in seen:
            problems.append(f"{entry['id']}: duplicate id; only the first one is used")
        elif len(sources) == MAX_SOURCES:
            problems.append(f"only the first {MAX_SOURCES} sources are used; raise MAX_SOURCES in talkback.py")
            break
        else:
            seen.add(entry["id"])
            sources.append(entry)
            problems += why
    if not sources:
        return copy.deepcopy(SOURCES), problems + ["no usable source; the built-in ones are in use"]
    return sources, problems


def from_v1(saved):
    """The flat settings of 0.1.0 as the current shape, keeping the voice and key file already chosen."""
    sources = copy.deepcopy(SOURCES)
    by_id = {entry["id"]: entry for entry in sources}
    if saved.get("heygen_voice"):
        by_id["openrouter"]["sets"][0]["voice"] = saved["heygen_voice"]
    if saved.get("openrouter_key_file"):
        by_id["openrouter"]["key"] = "file:" + str(saved["openrouter_key_file"])
    if saved.get("groq_voice"):
        by_id["groq"]["sets"][0]["voice"] = saved["groq_voice"]
    if saved.get("groq_key_file"):
        by_id["groq"]["key"] = "file:" + str(saved["groq_key_file"])
    if saved.get("microsoft_voice"):
        by_id["microsoft"]["sets"][0]["voice"] = saved["microsoft_voice"]
    if saved.get("microsoft_rate"):
        by_id["microsoft"]["rate"] = saved["microsoft_rate"]
    primary = "groq" if saved.get("groq_voice") and not saved.get("heygen_voice") else "openrouter"
    return {"version": SETTINGS_VERSION, "enabled": True, "port": saved.get("port", 47800),
            "primary": primary, "sources": sources}


def _set_name_for(source):
    """The name of the one voice set a version 2 file held: 'OpenRouter · HeyGen' becomes 'HeyGen'."""
    if source.get("engine") == "microsoft":
        return "Microsoft Edge"
    tail = str(source.get("label") or "").split("\u00b7")[-1].strip()
    if tail and len(tail) < 30:
        return tail
    model = str(source.get("model") or "")
    return model.rsplit("/", 1)[-1].split(":")[0].replace("-", " ").title() or "Voice"


def _one_set(source):
    """A version 2 source, whose voice, model and limit sat on the source, as a source with one voice set."""
    if not isinstance(source, dict) or "sets" in source:
        return source
    source = dict(source)
    source["set"] = _set_name_for(source)
    voice_set = {"name": source["set"]}
    for key in ("model", "voice", "voices", "request_limit"):
        if key in source:
            voice_set[key] = source[key]
    source["sets"] = [voice_set]
    return source


def upgrade(saved, version):
    """An older settings.json as the current version, keeping every voice and key file already chosen."""
    if version < 2:
        saved = from_v1(saved)
    elif version < 3:
        saved = dict(saved, sources=[_one_set(spec) for spec in saved.get("sources") or []])
    saved["version"] = SETTINGS_VERSION
    return saved


def key_for(entry):
    """The key a source needs, or ''. `file:` is read every time, so a new key needs no restart."""
    kind, _, where = str(entry.get("key") or "").partition(":")
    if kind == "env":
        return os.environ.get(where, "").strip()
    if kind == "file":
        try:
            return _key_path(entry).read_text(encoding="utf-8").strip()
        except (OSError, AttributeError):
            return ""
    return ""


def can_speak(entry):
    return entry["engine"] == "microsoft" or bool(key_for(entry))


def _key_path(entry):
    """The file a `file:` key is read from, absolute, or None for any other kind."""
    spec = str(entry.get("key") or "")
    if not spec.startswith("file:"):
        return None
    path = Path(spec[len("file:"):])
    return path if path.is_absolute() else HERE / path


# The keys are the user's own, and they belong in one place: secrets/ beside this program, one file per source,
# named after its id. console.py writes them; git never has them. `env:NAME` still works for anyone who prefers it.

def secret_path(source_id):
    """The file a source's key belongs in: secrets/<id>.local."""
    if not SOURCE_ID.match(str(source_id)):
        raise ValueError(f"{source_id!r} is not a source id")
    return SECRETS / f"{source_id}.local"


def save_secret(source_id, key):
    """Write a key into secrets/ and return its path. The value is never logged, returned or echoed."""
    value = str(key or "").strip()
    if not value:
        raise ValueError("that is an empty key")
    if "\n" in value or "\r" in value:
        raise ValueError("a key is one line of text")
    path = secret_path(source_id)
    SECRETS.mkdir(exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    return path


def secret_state(entry):
    """'ready', 'missing' or 'outside': whether the key is there, and whether it sits outside secrets/."""
    if entry.get("engine") == "microsoft":
        return "ready"
    if not key_for(entry):
        return "missing"
    path = _key_path(entry)
    if path is not None and SECRETS not in path.parents:
        return "outside"
    return "ready"


def adopt_key(entry):
    """Copy a key kept outside secrets/ into secrets/ and point the source at the copy; '' when there is none."""
    source_path = _key_path(entry)
    target = secret_path(entry["id"])
    if source_path is None or source_path == target or not source_path.is_file():
        return ""
    save_secret(entry["id"], key_for(entry))
    entry["key"] = f"file:secrets/{target.name}"
    return str(target)


def active_set(entry):
    """The voice set this source speaks with now."""
    return next((item for item in entry["sets"] if item["name"] == entry.get("set")), entry["sets"][0])


def request_entry(entry, voice_set):
    """One speech request: the source's endpoint and key, the set's model, limit and voice."""
    request = {key: entry[key] for key in ("engine", "base_url", "key", "rate") if key in entry}
    request.update(voice=voice_set["voice"], model=voice_set.get("model"))
    if voice_set.get("request_limit"):
        request["request_limit"] = voice_set["request_limit"]
    if voice_set.get("format"):
        request["format"] = voice_set["format"]
    return request


def voice_name(voice):
    """A voice as a person says it: 'en-US-GuyNeural' is Guy, 'nadine' is nadine."""
    short = re.sub(r"(?:Multilingual)?Neural$", "", str(voice))
    return short.rsplit("-", 1)[-1] if short != str(voice) else str(voice)


def speech_order(settings):
    """The primary voice, then the free Windows voice, then the rest in list order.

    The secondary voice is the fallback after the primary one fails, so it is tried before any other keyed source.
    """
    sources = [entry for entry in settings["sources"] if entry.get("enabled", True)]
    primary = settings["primary"]
    return sorted(sources, key=lambda entry: 0 if entry["id"] == primary
                  else (1 if entry["engine"] == "microsoft" else 2))


def voice_menu(settings):
    """The tray's voice menu as data: each keyed source, its voice sets, each set's voices, and whether it can speak.

    Microsoft's voice is not here: it is the backup, the backup is chosen in the console, and a reply that falls back
    to it is a fallback rather than a choice. The tray picks the primary voice only.
    """
    rows = []
    for entry in settings["sources"]:
        if entry.get("engine") == "microsoft":
            continue
        label = entry["label"] if can_speak(entry) else entry["label"] + " (no key)"
        rows.append({"id": entry["id"], "label": label, "enabled": entry.get("enabled", True),
                     "sets": [{"name": item["name"], "voice": item["voice"],
                               "voices": list(dict.fromkeys([*item.get("voices", []), item["voice"]])),
                               "active": entry["set"] == item["name"]}
                              for item in entry["sets"]]})
    return rows


# ── Settings and log ────────────────────────────────────────────────────────────────────────────────

class Settings:
    """settings.json, upgraded and validated on load. A broken file is never fatal."""

    def __init__(self):
        saved, self.problems = {}, []
        if SETTINGS.exists():
            try:
                saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                self.problems.append(f"settings.json unreadable ({e}); the built-in settings are in use")
                try:                       # keep what was there for inspection, then start clean
                    os.replace(SETTINGS, SETTINGS.with_name("settings.bad.json"))
                except OSError:
                    pass
        try:
            version = int(saved.get("version", 1) or 1)
        except (TypeError, ValueError):
            version = 1
        if saved and version < SETTINGS_VERSION:
            saved = upgrade(saved, version)
            self.problems.append(f"settings.json upgraded from version {version} to {SETTINGS_VERSION}")
        sources, problems = clean_sources(saved.get("sources"))
        self.problems += problems
        port = saved.get("port", DEFAULTS["port"])
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            self.problems.append(f"port {port!r} is not usable; using {DEFAULTS['port']}")
            port = DEFAULTS["port"]
        usable = [entry["id"] for entry in sources if entry.get("enabled", True)]
        primary = saved.get("primary")
        if primary not in usable:
            if primary is not None:
                self.problems.append(f"primary {primary!r} cannot speak; using {usable[0] if usable else 'nothing'}")
            primary = usable[0] if usable else None
        self.data = {"version": SETTINGS_VERSION, "enabled": True, "port": port,
                     "primary": primary, "sources": sources}
        self.save()
        for problem in self.problems:
            log("settings: " + problem)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def source(self, source_id=None):
        """The source with this id, or the picked one."""
        wanted = self.data["primary"] if source_id is None else source_id
        for entry in self["sources"]:
            if entry["id"] == wanted:
                return entry
        return self["sources"][0] if self["sources"] else None

    def set_voice(self, source_id, set_name, voice):
        """Give this source this voice from now on, without making it the one that speaks first."""
        entry = self.source(source_id)
        entry["set"] = next((item["name"] for item in entry["sets"] if item["name"] == set_name),
                            entry["set"])
        active_set(entry)["voice"] = voice
        self.save()

    def pick_voice(self, source_id, set_name, voice):
        """Speak with this source, this voice set and this voice from now on."""
        self.set_voice(source_id, set_name, voice)
        entry = self.source(source_id)
        entry["enabled"] = True
        self.data["primary"] = entry["id"]
        self.save()

    def reload(self):
        """Re-read settings.json, keeping whether speaking is on (it is on at every start)."""
        speaking = self.data["enabled"]
        self.__init__()
        self.data["enabled"] = speaking

    def save(self):
        """Write through a staging file and a replace, so no reader ever sees half a file, one writer at a time."""
        text = json.dumps(self.data, indent=2) + "\n"
        with _SAVE_LOCK:
            staging = SETTINGS.with_name("settings.json.tmp")
            staging.write_text(text, encoding="utf-8")
            os.replace(staging, SETTINGS)


def log(line):
    """One line per item or error; never the reply text."""
    if LOG.exists() and LOG.stat().st_size > 200_000:
        os.replace(LOG, LOG.with_name(LOG.name + ".old"))
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")


def _without_reply(line, text):
    """A log line with the reply taken out: talkback.log never holds what was said."""
    return line.replace(text, "[reply text]") if text else line


# ── Voices and playback ────────────────────────────────────────────────────────────────────────────

def _audio_file(suffix):
    """A fresh file in this process's own folder, never a predictable name another program could claim first."""
    global AUDIO
    with _AUDIO_LOCK:
        if AUDIO is None:
            AUDIO = tempfile.mkdtemp(prefix="talkback-audio-")
    handle, path = tempfile.mkstemp(suffix=suffix, prefix="speech-", dir=AUDIO)
    os.close(handle)
    return path


def microsoft_to_file(text, voice, rate, path):
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, voice=voice, rate=rate).save(path))


def _pieces(text, limit=200):
    """Split text an endpoint cannot take in one request: at sentence ends, else commas, else spaces."""
    out, rest = [], text.strip()
    while len(rest) > limit:
        cut = max(rest.rfind(". ", 0, limit), rest.rfind("? ", 0, limit), rest.rfind("! ", 0, limit), rest.rfind(": ", 0, limit))
        if cut < limit // 3:
            cut = rest.rfind(", ", 0, limit)
        if cut < limit // 3:
            cut = rest.rfind(" ", 0, limit)
        out.append(rest[: cut + 1].strip())
        rest = rest[cut + 1:].strip()
    return out + ([rest] if rest else [])


def _speech_request(entry, piece, fmt=None):
    """(url, payload) for any OpenAI-compatible /audio/speech endpoint."""
    return (entry["base_url"] + "/audio/speech",
            {"model": entry["model"], "voice": entry["voice"], "input": piece,
             "response_format": fmt or entry.get("format") or "wav"})


def openai_to_file(text, entry, key, path):
    """Speak through an OpenAI-compatible endpoint and save the audio as one WAV file.

    The reply may come back as a WAV file or as raw PCM with `rate=` in its Content-Type; both are joined
    into `path`. `request_limit` splits longer text into parallel requests (Groq takes 200 characters).
    """
    import io
    import urllib.error
    import urllib.request
    import wave
    from concurrent.futures import ThreadPoolExecutor

    limit = entry.get("request_limit", 0)
    pieces = _pieces(text, limit) if limit and len(text) > limit else [text]

    def ask(piece, fmt=None):
        url, payload = _speech_request(entry, piece, fmt)
        req = urllib.request.Request(url, method="POST", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json", "User-Agent": "talkback"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")

    def fetch(piece):
        """One piece of text; when the endpoint refuses the format, the other one it may speak in."""
        fmt = entry.get("format") or "wav"
        try:
            return ask(piece, fmt)
        except urllib.error.HTTPError as error:
            said = error.read().decode("utf-8", "replace") if error.code == 400 else ""
            if "response_format" not in said:
                raise
            return ask(piece, "pcm" if fmt == "wav" else "wav")

    with ThreadPoolExecutor(min(4, len(pieces))) as pool:
        clips = list(pool.map(fetch, pieces))
    with wave.open(path, "wb") as out:
        for index, (body, content_type) in enumerate(clips):
            if body[:4] == b"RIFF":
                with wave.open(io.BytesIO(body)) as clip:  # its length field can be a streaming placeholder
                    if index == 0:
                        out.setnchannels(clip.getnchannels())
                        out.setsampwidth(clip.getsampwidth())
                        out.setframerate(clip.getframerate())
                    out.writeframes(clip.readframes(clip.getnframes()))
            else:
                if index == 0:
                    rate = int(m.group(1)) if (m := re.search(r"rate=(\d+)", content_type)) else 44100
                    channels = int(m.group(1)) if (m := re.search(r"channels=(\d+)", content_type)) else 1
                    out.setnchannels(channels)
                    out.setsampwidth(2)
                    out.setframerate(rate)
                out.writeframes(body)


def play(path, stop):
    """Play through Windows MCI until done, F9, or Interrupt current in the tray."""
    import ctypes
    user32, winmm = ctypes.WinDLL("user32"), ctypes.WinDLL("winmm")
    kind = "waveaudio" if path.endswith(".wav") else "mpegvideo"
    winmm.mciSendStringW(f'open "{path}" type {kind} alias talkback', None, 0, None)
    winmm.mciSendStringW("play talkback", None, 0, None)
    buf = ctypes.create_unicode_buffer(128)
    why = "ended"
    while True:
        winmm.mciSendStringW("status talkback mode", buf, 127, None)
        if buf.value != "playing":
            break
        if stop.is_set() or user32.GetAsyncKeyState(0x78) & 0x8000:
            why = "stopped"
            break
        time.sleep(0.05)
    winmm.mciSendStringW("close talkback", None, 0, None)
    return why


# ── The speaker: one item at a time ─────────────────────────────────────────────────────────────────

class Speaker(threading.Thread):
    def __init__(self, settings):
        super().__init__(daemon=True)
        self.settings, self.items, self.stop = settings, queue.Queue(), threading.Event()

    def run(self):
        while True:
            item = self.items.get()
            source = item.get("source", "unknown")
            try:
                self.speak(item.get("text", ""), source, item.get("only", ""),
                           item.get("set", ""), item.get("voice", ""))
            except Exception as e:
                log(f"{source}: FAILED {e}")

    def speak(self, message, source, only="", set_name="", voice=""):
        """Speak this reply. `only` is the one source a test line may use; `set_name`/`voice` the one voice in it.

        A test passes a voice without choosing it, so testing a voice changes nothing about what speaks next.
        """
        if not self.settings["enabled"]:
            log(f"{source}: not spoken (speaking is off), {len(message)}-char reply")
            return
        text, how = what_to_say(message)
        if len(text) < 10:
            log(f"{source}: nothing to say ({len(message)}-char reply)")
            return
        self.stop.clear()
        skipped = []
        order = speech_order(self.settings)
        if only:
            order = [entry for entry in order if entry["id"] == only]
        for entry in order:
            key = key_for(entry)
            if not can_speak(entry):
                skipped.append(entry["id"] + " (no key)")
                continue
            voice_set = active_set(entry)
            if voice:                      # a test speaks one voice without making it the chosen one
                chosen = next((item for item in entry["sets"] if item["name"] == set_name), None)
                if chosen is not None:
                    voice_set = dict(chosen, voice=voice)
            request = request_entry(entry, voice_set)
            # edge-tts answers in MP3: the suffix is what tells MCI how to decode it, so it must match the bytes
            path = _audio_file(".mp3" if entry["engine"] == "microsoft" else ".wav")
            try:
                if entry["engine"] == "microsoft":
                    microsoft_to_file(text, request["voice"], request["rate"], path)
                else:
                    openai_to_file(text, request, key, path)
                why = play(path, self.stop)
            except Exception as e:
                log(_without_reply(f"{source}: {entry['id']}/{voice_set['name']} did not speak ({e!r}); "
                                   "the next source is tried", text))
                skipped.append(entry["id"] + " (failed)")
                continue
            finally:
                if os.path.exists(path):
                    os.unlink(path)
            skipped_text = f"; skipped {', '.join(skipped)}" if skipped else ""
            log(f"{source}: spoke {how}, {len(text)} of {len(message)} chars, "
                f"{entry['id']}/{voice_set['name']} {request['voice']}, {why}{skipped_text}")
            return
        log(f"{source}: nothing spoke, {len(message)}-char reply; tried {', '.join(skipped) or 'no source'}")


# ── HTTP: POST /say, GET /status (127.0.0.1 only) ──────────────────────────────────────────────────

def status_view(settings):
    """The settings with every key path and key value taken out: the status page never describes the machine."""
    view = {key: value for key, value in settings.data.items() if key != "sources"}
    view["sources"] = [
        dict(entry, key="missing" if not key_for(entry)
             else "file" if str(entry.get("key") or "").startswith("file:") else "env")
        for entry in settings["sources"]]
    return view


def make_handler(settings, speaker, on_change=None):
    class Handler(BaseHTTPRequestHandler):
        def allowed(self):
            """A local client, never a page: the Host is loopback, and any Origin must be this program's own."""
            port = self.server.server_address[1]
            mine = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if (self.headers.get("Host") or "").strip().lower() not in mine:
                return False
            origin = (self.headers.get("Origin") or "").strip().lower()
            return not origin or origin in {f"http://{name}" for name in mine}

        def posted(self):
            """The JSON body; a page cannot send this shape without a preflight, which this program never grants."""
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                raise ValueError("application/json only")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError(f"body over {MAX_BODY} bytes")
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_POST(self):
            if not self.allowed():
                return self.send_error(403)
            if self.path == "/reload":  # console.py has just written settings.json
                settings.reload()
                log("settings: reloaded on request")
                self.send_response(204)
                self.end_headers()
                return
            if self.path == "/enabled":  # the console's Talkback Voice switch
                try:
                    enabled = bool(self.posted()["enabled"])
                except Exception:
                    return self.send_error(400, 'send JSON {"enabled": true}')
                settings["enabled"] = enabled
                if not enabled:
                    speaker.stop.set()          # what is playing stops now, and nothing queued is spoken
                if on_change is not None:
                    on_change()                 # the tray repaints its icon, title and menu
                log(f"settings: speaking {'on' if enabled else 'off'}, set by the console")
                self.send_response(204)
                self.end_headers()
                return
            if self.path != "/say":
                return self.send_error(404)
            try:
                body = self.posted()
                text = body["text"]
                only = str(body.get("only") or "")     # a test line: this one source, and maybe one voice
                set_name = str(body.get("set") or "")
                voice = str(body.get("voice") or "")
            except Exception:
                return self.send_error(400, "send JSON {\"text\": ..., \"source\": ...}")
            if speaker.items.qsize() >= MAX_QUEUE:
                log(f"{body.get('source', 'unknown')}: dropped, {speaker.items.qsize()} replies were waiting")
                return self.send_error(429)
            speaker.items.put({"text": text, "source": str(body.get("source", "unknown")),
                               "only": only, "set": set_name, "voice": voice})
            self.send_response(202)
            self.end_headers()

        def do_GET(self):
            if not self.allowed():
                return self.send_error(403)
            if self.path != "/status":
                return self.send_error(404)
            out = json.dumps({**status_view(settings), "waiting": speaker.items.qsize()}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *args):
            pass
    return Handler


# ── Tray (the brand's chevron; left-click toggles ON/OFF; right-click menu) ────────────────────────

# The mark is the chevron of alts-agency's brand/logo/svg/mark-white.svg — its own path, not a redrawing of it:
# white on black, with the `\` stroke of that lockup left out, because on its own the chevron is the play sign.
ICON_BLACK = "#000000"
ICON_WHITE = "#FFFFFF"
ICON_OFF = "#75797F"            # the same chevron while Talkback Voice is off
BRAND_PATH = ("M81 60V162L380 305Q400 314 418 320Q436 326 445 328"
              "Q435 330 416.5 336.5Q398 343 380 352L81 495V599L519 386V273Z")
BRAND_SCALE, BRAND_SHIFT = 0.18553, (-15.028, 111.132)   # the transform the brandkit puts on that path


def chevron_points():
    """The brand's chevron as points in the artwork's own coordinates: M, L, V and the Q curves sampled smoothly.

    Kept as a parser rather than a list of numbers so `BRAND_PATH` above stays the path the brandkit ships.
    """
    points, at = [], (0.0, 0.0)
    for command, arguments in re.findall(r"([MLVQZ])([^MLVQZ]*)", BRAND_PATH):
        numbers = [float(number) for number in re.findall(r"-?\d+\.?\d*", arguments)]
        if command in "ML":
            at = (numbers[0], numbers[1])
            points.append(at)
        elif command == "V":
            at = (at[0], numbers[0])
            points.append(at)
        elif command == "Q":              # control, then end: the rounded point of the chevron
            control, end, start = (numbers[0], numbers[1]), (numbers[2], numbers[3]), at
            for step in range(1, 13):
                t = step / 12
                points.append(((1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t * t * end[0],
                               (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t * t * end[1]))
            at = end
    return [(BRAND_SHIFT[0] + BRAND_SCALE * x, BRAND_SHIFT[1] - BRAND_SCALE * y) for x, y in points]


def icon_image(size=256, playing=True, supersample=4, share=0.60):
    """The app mark: the brand's chevron white on black, one geometry for the tray and for assets/.

    Drawn at 4x and scaled down, which is what keeps the 16 px version from going soft.
    """
    from PIL import Image, ImageDraw
    big = size * supersample
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.22, fill=ICON_BLACK)
    points = chevron_points()
    left, right = min(x for x, _ in points), max(x for x, _ in points)
    top, bottom = min(y for _, y in points), max(y for _, y in points)
    scale = big * share / max(right - left, bottom - top)
    middle = ((left + right) / 2, (top + bottom) / 2)
    draw.polygon([(big / 2 + (x - middle[0]) * scale, big / 2 + (y - middle[1]) * scale) for x, y in points],
                 fill=ICON_WHITE if playing else ICON_OFF)
    return img.resize((size, size), Image.LANCZOS)


def tray_menu(settings, actions):
    """The tray menu, in segments: what the program is doing, the primary voice, the actions, then quit.

    The items are made when the menu is opened rather than once at startup (`pystray.Menu` takes the generator), so
    "Running" and every tick say what is true at that moment. Built from data with the handlers passed in, so the
    menu can be checked without a screen.
    """
    import pystray

    def checked_voice(source_id, set_name, voice):
        def check(item):
            entry = settings.source(source_id)
            return (settings["primary"] == source_id and entry["set"] == set_name
                    and active_set(entry)["voice"] == voice)
        return check

    def checked_source(source_id):
        return lambda item: settings["primary"] == source_id

    def groups():
        """One submenu per provider, ticked at its own level as well, so the voice in use is visible from the top."""
        rows = []
        for row in voice_menu(settings):
            sets = []
            for voice_set in row["sets"]:
                voices = [pystray.MenuItem(voice_name(voice),
                                           actions["pick"](row["id"], voice_set["name"], voice),
                                           checked=checked_voice(row["id"], voice_set["name"], voice), radio=True)
                          for voice in voice_set["voices"]]
                sets.append(pystray.MenuItem(voice_set["name"], pystray.Menu(*voices),
                                             checked=checked_source(row["id"])))
            rows.append(pystray.MenuItem(row["label"], pystray.Menu(*sets), checked=checked_source(row["id"])))
        return rows

    def items():
        """The whole menu, built as it is shown."""
        yield pystray.MenuItem("Toggle", actions["toggle"], default=True, visible=False)
        yield pystray.MenuItem("Agent Talkback", actions["toggle"], checked=lambda item: settings["enabled"])
        yield pystray.MenuItem("Running" if settings["enabled"] else "Not running", actions["nothing"],
                               enabled=False)
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Primary Voice", pystray.Menu(*groups()))
        yield pystray.MenuItem("Open console", actions["open"])
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Interrupt current", actions["stop"])
        yield pystray.MenuItem("Reload settings", actions["reload"])
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Quit", actions["quit"])

    return pystray.Menu(items)


def follow_system_theme():
    """Ask Windows to draw this program's menus in the system's light or dark mode.

    A native Win32 menu stays light without this. `uxtheme` ordinals 135 (`SetPreferredAppMode`) and 104
    (`FlushMenuThemes`) are undocumented; where they are missing or refused, the menu keeps whatever Windows gives it.
    """
    try:
        import winreg
        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            dark = winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except (ImportError, OSError):
        return
    try:
        uxtheme = ctypes.WinDLL("uxtheme")
        uxtheme[135](2 if dark else 1)      # 2 forces dark menus, 1 forces light ones
        uxtheme[104]()                      # and redraw any menu already made
    except (AttributeError, OSError, IndexError):
        pass


def tray(settings, speaker, server, wake=None):
    import pystray
    follow_system_theme()                    # the menu is a native one: this is what makes it dark

    def image():
        return icon_image(64, settings["enabled"])

    def title():
        entry, state = settings.source(), "ON" if settings["enabled"] else "OFF"
        if not entry:
            return f"Agent Talkback \u00b7 {state}"
        parts = [entry["label"]] + ([entry["set"]] if len(entry["sets"]) > 1 else []) + [state]
        return "Agent Talkback \u00b7 " + " \u00b7 ".join(parts)

    def set_enabled(value):
        def choose(icon, item):
            settings["enabled"] = value
            icon.icon = image()
            icon.title = title()
            if not value:
                speaker.stop.set()
        return choose

    def toggle(icon, item):
        set_enabled(not settings["enabled"])(icon, item)

    def pick(source_id, set_name, voice):
        def choose(icon, item):
            settings.pick_voice(source_id, set_name, voice)
            icon.title = title()
            icon.update_menu()
        return choose

    def reload_(icon, item):
        settings.reload()
        icon.icon = image()
        icon.title = title()
        icon.update_menu()

    def stop_(icon, item):
        speaker.stop.set()

    def open_(icon, item):
        """Open the setup console beside this process, so the tray keeps running."""
        try:
            subprocess.Popen([sys.executable, str(HERE / "console.py")], creationflags=0x08000000)
        except OSError as e:
            log(f"console did not open: {e!r}")

    def quit_(icon, item):
        speaker.stop.set()
        server.shutdown()
        icon.stop()

    def nothing_(icon, item):
        """The menu's first item: it names the program, and doing nothing when clicked is the whole point."""

    actions = {"nothing": nothing_, "on": set_enabled(True), "off": set_enabled(False), "toggle": toggle,
               "pick": pick, "open": open_, "reload": reload_, "stop": stop_, "quit": quit_}
    icon = pystray.Icon("talkback", image(), title(), tray_menu(settings, actions))

    def repaint():
        """Move the tray to what the console just changed: /enabled reaches here through the wake list."""
        icon.icon = image()
        icon.title = title()
        icon.update_menu()

    if wake is not None:
        wake.append(repaint)
    icon.run()


class LocalServer(ThreadingHTTPServer):
    """The server every copy binds.

    No SO_REUSEADDR: Windows lets two sockets bind the same port when it is set (which HTTPServer does by default),
    so a second copy would start a second tray icon and take half the replies. Turned off, the second bind fails.
    """

    allow_reuse_address = False


def already_answering(port):
    """Whether a copy is already serving on 127.0.0.1:port.

    Asking is the reliable test: a bind is not, because Windows will hand a second socket the same port.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=0.5) as response:
            return response.status == 200
    except OSError:
        return False


def already_running(port):
    """The words the second copy shows. Not an error, so it says where the running copy is instead."""
    return (f"Agent Talkback is already running on port {port}, so this copy stopped.\n\n"
            "Use the tray icon: click it to switch speaking on or off, or right-click it for the menu, "
            "including Open Agent Talkback to change the voices.")


def notice(words):
    """A plain Windows message box: under pythonw there is no console to print to."""
    try:
        ctypes.windll.user32.MessageBoxW(None, words, "Agent Talkback", 0x40)  # 0x40: the information icon
    except (AttributeError, OSError):
        pass                                   # a machine without a window station still exits cleanly


def second_copy(port):
    """The copy that finds one running: it says so in the log, and in a message box unless a sender started it."""
    log(f"a second copy stopped: port {port} is taken by the copy that is already running")
    if "--quiet" not in sys.argv:              # send.py passes it: a reply must never wait on a dialog
        notice(already_running(port))


def main():
    settings = Settings()
    port = settings["port"]
    if already_answering(port):
        return second_copy(port)
    try:
        server = LocalServer(("127.0.0.1", port), None)
    except OSError:
        return second_copy(port)
    speaker = Speaker(settings)
    wake = []                      # tray() puts its repaint here, so the console's switch can move the icon
    server.RequestHandlerClass = make_handler(settings, speaker, lambda: [repaint() for repaint in wake])
    speaker.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log("started")
    tray(settings, speaker, server, wake)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"stopped: {e!r}")
        raise
