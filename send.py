"""Send a reply to Talkback; for agents' turn-end hooks. Never blocks or fails the agent.

Hook use: the hook's JSON arrives on stdin (Claude Code, Hermes) or as the last argument (Codex notify).
By hand:  python send.py --source myagent --text "reply text"
"""
import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
PORT = 47800


def post(port, text, source, only=None, set_name=None, voice=None):
    """Queue one reply. `only` names the one source, `voice` the one voice, which is how the console tests."""
    body = {"text": text, "source": source}
    if only:
        body["only"] = only
    if voice:
        body.update(voice=voice, set=set_name or "")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/say", method="POST",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=1).read()


def port_from_settings():
    """The port in settings.json; a missing or broken file never stops a reply."""
    try:
        return json.loads((HERE / "settings.json").read_text(encoding="utf-8")).get("port", PORT)
    except (OSError, ValueError):
        return PORT


def start_talkback():
    """Start Talkback through WMI so it is not a child of the hook: some agents end the hook's process tree.

    It is started with `--quiet`: if another copy already has the port, that copy says nothing rather than waiting
    on a message box, because this runs while an agent is finishing a reply.
    """
    command = f'"{Path(sys.executable).with_name("pythonw.exe")}" "{HERE / "talkback.py"}" --quiet'
    script = ("Invoke-CimMethod Win32_Process -MethodName Create -Arguments @{CommandLine="
              + "'" + command.replace("'", "''") + "'}")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", _encoded(script)],
                   capture_output=True, creationflags=0x08000000)


def _encoded(script):
    """PowerShell's -EncodedCommand: base64 UTF-16, so no quoting rule ever sees a path."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def deliver(text, source="agent", port=None, only=None, set_name=None, voice=None):
    """Queue this text, starting Talkback if it is not running. True when the program took it."""
    port = port or port_from_settings()
    try:
        post(port, text, source, only, set_name, voice)
        return True
    except OSError:
        pass
    start_talkback()
    for _ in range(50):
        time.sleep(0.1)
        try:
            post(port, text, source, only, set_name, voice)
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


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--source", default="agent")
    args.add_argument("--text")
    args.add_argument("payload", nargs="?", help="hook JSON as an argument (Codex notify passes it here)")
    a = args.parse_args()
    text = a.text
    if text is None:
        text = reply_text(a.payload or sys.stdin.buffer.read().decode("utf-8"))
    if text:
        deliver(text, a.source)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
