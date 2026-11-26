"""Send a reply to Talkback; for agents' turn-end hooks. Never blocks or fails the agent.

Hook use: the hook's JSON arrives on stdin (Claude Code, Hermes) or as the last argument (Codex notify).
By hand:  python send.py --source myagent --text "reply text"

Each folder an agent runs in gets an identity, `.talkback.json`, on its first send: Talkback gives it its own voice.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).parent
PORT = 47800
ID_FILE = ".talkback.json"


def post(port, text, source, only=None, set_name=None, voice=None, talker=None):
    """Queue one reply. `only` names the one source, `voice` the one voice, which is how the console tests."""
    body = {"text": text, "source": source}
    if only:
        body["only"] = only
    if voice:
        body.update(voice=voice, set=set_name or "")
    if talker:
        body["talker"] = talker
    req = urllib.request.Request(f"http://127.0.0.1:{port}/say", method="POST",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=1).read()


def saved_setting(name, default):
    """One value from settings.json; a missing or broken file never stops a reply."""
    try:
        return json.loads((HERE / "settings.json").read_text(encoding="utf-8")).get(name, default)
    except (OSError, ValueError):
        return default


def agent_folder(raw=None):
    """The folder the agent runs in. For a hook: Claude Code's project folder (its hook `cwd` follows every `cd`),
    else the hook JSON's `cwd`. By hand: the current folder."""
    if raw:
        return os.environ.get("CLAUDE_PROJECT_DIR") or json.loads(raw).get("cwd") or os.getcwd()
    return os.getcwd()


def talker_for(folder):
    """{"id", "name", "folder"} from <folder>/.talkback.json, written if there is none; None when that cannot be done.

    An existing file is never rewritten, so the id never changes. The name is only the first suggestion: the console's
    wins. `folder` is the last two parts of the path, a hint for telling two names apart.
    """
    try:
        folder = Path(folder).resolve()
        path = folder / ID_FILE
        try:
            with open(path, "x", encoding="utf-8") as f:      # exclusive: two sends at once keep one id
                f.write(json.dumps({"id": str(uuid.uuid4()), "name": folder.name}, indent=2) + "\n")
        except FileExistsError:
            pass
        saved = json.loads(path.read_text(encoding="utf-8-sig"))
        return {"id": str(uuid.UUID(saved["id"])), "name": str(saved.get("name") or folder.name),
                "folder": "/".join(folder.parts[-2:])}
    except Exception:                                         # the reply is still spoken, in the primary voice
        return None


def pythonw():
    """The Python that runs Talkback, as it stored it; before its first start, the one beside the caller's."""
    stored = saved_setting("pythonw", "")
    if stored and Path(stored).is_file():
        return stored
    return str(Path(sys.executable).with_name("pythonw.exe"))


def start_talkback():
    """Start Talkback through WMI so it is not a child of the hook: some agents end the hook's process tree.

    It is started with `--quiet`: if another copy already has the port, that copy says nothing rather than waiting
    on a message box, because this runs while an agent is finishing a reply.
    """
    command = f'"{pythonw()}" "{HERE / "talkback.py"}" --quiet'
    script = ("Invoke-CimMethod Win32_Process -MethodName Create -Arguments @{CommandLine="
              + "'" + command.replace("'", "''") + "'}")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", _encoded(script)],
                   capture_output=True, creationflags=0x08000000)


def _encoded(script):
    """PowerShell's -EncodedCommand: base64 UTF-16, so no quoting rule ever sees a path."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def deliver(text, source="agent", port=None, only=None, set_name=None, voice=None, talker=None):
    """Queue this text, starting Talkback if it is not running. True when the program took it."""
    port = port or saved_setting("port", PORT)
    try:
        post(port, text, source, only, set_name, voice, talker)
        return True
    except OSError:
        pass
    start_talkback()
    for _ in range(50):
        time.sleep(0.1)
        try:
            post(port, text, source, only, set_name, voice, talker)
            return True
        except OSError:
            continue
    return False


def reply_text(raw):
    """The reply in a hook's JSON: Claude Code's last_assistant_message, Codex's last-assistant-message,
    or Hermes' extra.assistant_response (its post_llm_call shell hook)."""
    data = json.loads(raw or "{}")
    return (data.get("last_assistant_message") or data.get("last-assistant-message")
            or (data.get("extra") or {}).get("assistant_response")
            or data.get("response") or data.get("message") or "")


def is_helper(raw, jobs=Path.home() / ".claude" / "jobs", terminal=Path.home() / ".cchelper"):
    """True for a Claude Code session an agent started as its helper: it never speaks.

    Claude Code keeps `jobs/<id>/state.json` for each background session (`claude --bg`), with its session id in it.
    A helper started in a terminal has no job folder, so the tool that starts it marks it with
    `~/.cchelper/<session id>.json`.
    """
    session = json.loads(raw or "{}").get("session_id")
    if not session:
        return False
    if re.fullmatch(r"[0-9A-Za-z-]{8,64}", str(session)) and (terminal / f"{session}.json").exists():
        return True
    for state in jobs.glob("*/state.json"):
        try:
            job = json.loads(state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if session in (job.get("sessionId"), job.get("resumeSessionId")):
            return True
    return False


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--source", default="agent")
    args.add_argument("--text")
    args.add_argument("payload", nargs="?", help="hook JSON as an argument (Codex notify passes it here)")
    a = args.parse_args()
    text, raw = a.text, None
    if text is None:
        raw = a.payload or sys.stdin.buffer.read().decode("utf-8")
        if is_helper(raw):
            return
        text = reply_text(raw)
    if text:
        deliver(text, a.source, talker=talker_for(agent_folder(raw)))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
