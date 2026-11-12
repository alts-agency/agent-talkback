# Agent Talkback

![Agent Talkback: speaks your agents' replies on Windows](assets/social-preview.png)

Agent Talkback reads your AI agents' replies out loud on Windows 10 and 11. Your agent sends the reply text when it
finishes a turn. Talkback picks the part worth hearing, picks the voice, and speaks it. You switch it off from the tray
when you are in a meeting.

It works with Claude Code, Codex, Hermes Agent and any script that can run a command or send an HTTP request. Your
reply text goes only to the text-to-speech provider you choose.

Both providers it ships with have free voice models: HeyGen Voice on OpenRouter and Orpheus on Groq. One key from
either is enough. With no key at all, Microsoft Edge's voice speaks for free.

## Install

You need Windows 10 or 11, [Git](https://git-scm.com/download/win) and
[Python 3.10 or newer](https://www.python.org/downloads/windows/) from python.org (its `tkinter` draws the setup
window). In PowerShell or Command Prompt:

```bat
git clone https://github.com/alts-agency/agent-talkback.git C:\tools\talkback
cd C:\tools\talkback
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python console.py
```

The examples below use `C:\tools\talkback`. If you clone somewhere else, change the paths to match.

The last command opens the setup window:

1. Under the providers, click **Edit** on OpenRouter (or Groq) and paste your key from
   [openrouter.ai](https://openrouter.ai/keys) (or [console.groq.com](https://console.groq.com/keys)). The key is
   saved in `secrets\`, never in `settings.json`. Skip this step to use only the free Microsoft voice.
2. Under **Primary voice**, click **Change** and **Test** a few voices. Click a voice to use it.
3. Turn on **Start with Windows** if you want Talkback running after every login.
4. Close the window. Talkback stays in the tray. Its menu item **Open console** brings the window back.

To start Talkback without the window, run `.venv\Scripts\pythonw talkback.py`.

## Use with your agent

Your agent runs `send.py` when it finishes a reply and hands it the text. `send.py` posts the text to Talkback, starts
Talkback if it is not running, and exits. It never blocks your agent and never makes it fail. If speaking is off,
the reply is dropped.

Always call `send.py` with the Python inside `.venv`. That Python has the packages installed, and `send.py` uses the
`pythonw.exe` next to it to start Talkback.

`send.py` takes the reply in three ways:

- `--text "the reply"` on the command line
- hook JSON on stdin, with the reply in `last_assistant_message`, `response`, `message` or `extra.assistant_response`
- hook JSON as the last argument, with the reply in `last-assistant-message` (what Codex sends)

### Claude Code

Add a Stop hook to `%USERPROFILE%\.claude\settings.json`, or to a project's `.claude\settings.json`. Claude Code sends
the reply on stdin as `last_assistant_message`.

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "C:\\tools\\talkback\\.venv\\Scripts\\python.exe",
            "args": ["C:\\tools\\talkback\\send.py", "--source", "claude"],
            "timeout": 15,
            "async": true
          }
        ]
      }
    ]
  }
}
```

If the file already has a `hooks` section, add the `Stop` entry to it.

### Codex

Add this line to `%USERPROFILE%\.codex\config.toml`, above any `[section]`. Codex runs the `notify` command after each
turn and passes the reply as a JSON argument. It allows one `notify` command, so this replaces any you already have.

```toml
notify = ['C:\tools\talkback\.venv\Scripts\python.exe', 'C:\tools\talkback\send.py', '--source', 'codex']
```

### Hermes Agent

[Hermes Agent](https://github.com/NousResearch/hermes-agent) runs
[shell hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks) from the `hooks:` block in
`%USERPROFILE%\.hermes\config.yaml`. Its `post_llm_call` event fires once per finished turn and pipes the reply to the
hook as `extra.assistant_response`.

```yaml
hooks:
  post_llm_call:
    - command: 'C:\tools\talkback\.venv\Scripts\python.exe C:\tools\talkback\send.py --source hermes'
      timeout: 15
```

The first time `hermes chat` sees a new hook it asks you to approve it. The gateway never asks, so if you only run
the gateway, start it once with `--accept-hooks`.

### Any other agent or script

Run `send.py` with the text:

```bat
C:\tools\talkback\.venv\Scripts\python.exe C:\tools\talkback\send.py --source myagent --text "Build finished, all tests pass."
```

Or post to Talkback directly. It answers `202` when the reply is queued:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:47800/say -ContentType application/json `
  -Body '{"text": "Build finished, all tests pass.", "source": "ci"}'
```

### Choose what is spoken

Talkback speaks from a `**In short:**` paragraph to the end of the `**From you:**` paragraph after it. A reply
without that header is spoken whole when it is short and plain. Otherwise Talkback speaks its first sentence or two.
Replies are spoken one at a time.

To get the short version every time, add one line to your agent's instructions file (`AGENTS.md`, `CLAUDE.md` or
similar):

> Start every reply with a one-paragraph `**In short:**` summary. Add a `**From you:**` paragraph when you need
> something from me.

## The tray icon

The icon is the Alts Agency chevron: white when Talkback speaks, grey when it is off.

- Left-click switches speaking on or off at once. While it is off, replies are dropped, not saved for later. It is on
  again after every restart. Hover over the icon to see the voice in use and whether it is on.
- Right-click opens the menu. **Agent Talkback** is ticked while it speaks; click it to switch. Below it are
  `Running` or `Not running`, **Primary Voice** (provider, voice set, voice), **Open console**,
  **Interrupt current**, **Reload settings** and **Quit**. The provider and voice in use are ticked. The Microsoft
  backup voice is chosen in the console, not in the tray.
- If you start Talkback while it is already running, the second copy says so and points to the tray icon. A copy
  started by `send.py` stays silent (`--quiet`), so your agent never waits on a message box.

## The setup window

`console.py` follows Windows' light or dark mode.

- **Primary voice** is tried first for every reply. **Change** lists its voices. Click a voice to use it; **Test**
  speaks a line in a voice without choosing it.
- **Backup voice** is Microsoft Edge's voice engine. It needs no key and speaks when the primary voice fails.
  **Change** lists Microsoft's English (`en-`) voices.
- The provider table lists every provider by name. **Edit** opens its name, endpoint, model, voice, key and request
  limit in one dialog. **Set as primary** makes a provider speak first. **+ Add provider** adds any OpenAI-compatible
  speech endpoint.
- **Talkback Voice** switches speaking on and off, the same as the tray icon. **Start with Windows** starts Talkback
  when you log in.
- **alts.agency** at the top right opens our website.

The window has a fixed size. Accent colours were checked for contrast in light and dark mode.

## Keys

Keys live in `secrets\`, one file per provider, named after it (`openrouter.local`, `groq.local`). The folder is in
`.gitignore`, so no key is in this repository. Keys never go into `settings.json`, the log or the status page.
Talkback reads the key file at every reply, so a new key works without a restart. To keep a key in an environment
variable instead, set the provider's `key` to `env:NAME`.

## Voices in settings.json

`settings.json` is created on first start. Its `sources` list holds the providers. A source is one endpoint and one
key. Its `sets` are voice sets: one model each, with the voice in use and the voices the tray offers.

```json
{"id": "groq", "label": "Groq", "engine": "openai", "set": "Orpheus",
 "base_url": "https://api.groq.com/openai/v1", "key": "file:secrets/groq.local",
 "sets": [{"name": "Orpheus", "model": "canopylabs/orpheus-v1-english", "voice": "autumn",
           "voices": ["autumn", "diana", "hannah", "austin", "daniel", "troy"], "request_limit": 200}]}
```

- The primary voice speaks first. If it has no key or fails, the free Microsoft voice speaks, then the other
  providers in the order they appear in `settings.json`. The setup window lists them alphabetically and only changes
  which one is primary. A new install tries `OpenRouter > HeyGen`, then `Free (for Windows) > Microsoft Edge`, then
  `Groq > Orpheus`.
- `set` is the voice set in use. One source can hold several sets with different models, so one OpenRouter key can
  speak HeyGen and another model, and the tray switches between them.
- `request_limit` belongs to a voice set. It is the most characters that model takes in one request. Longer text is
  split and fetched in parallel.
- `format` is optional and also belongs to a voice set: `wav` (the default) or `pcm`. HeyGen Voice speaks only `pcm`,
  and its shipped set says so. If a provider refuses the format, Talkback asks once more in the other format.
- `key` points to the key: `file:secrets/openrouter.local` (what the setup window writes) or
  `env:OPENROUTER_API_KEY`. A relative path is read from the Talkback folder.
- `engine` is `openai` for any OpenAI-compatible `/audio/speech` endpoint (OpenRouter, Groq or your own) and
  `microsoft` for edge-tts.
- A source without a key is skipped. The setup window shows its key as `Not set` and the tray says `(no key)`.
- Talkback reads at most 5 sources and 8 voice sets per source. Raise `MAX_SOURCES` and `MAX_SETS` at the top of
  `talkback.py` to use more. Anything past the limit is ignored with one line in the log.
- `settings.json` also holds `enabled`, `port` and `primary` (the id of the source that speaks first). Files from
  older versions are upgraded on start and keep their voices and key files.

## HTTP interface

Talkback listens on `http://127.0.0.1:47800`.

| Request | What it does |
|---|---|
| `POST /say` with `{"text": "...", "source": "..."}` | queues a reply (`202`). `"only": "<source id>"` speaks it with that one source, which is how **Test** works |
| `GET /status` | the settings without keys, and how many replies are waiting |
| `POST /reload` | reads `settings.json` again |
| `POST /enabled` with `{"enabled": false}` | switches speaking off or on and updates the tray |

## Files

| File | What it is |
|---|---|
| `talkback.py` | the program (run it with `pythonw`) |
| `console.py` | the setup window |
| `send.py` | the sender your agent calls |
| `settings.json` | your voices (created on first start, not in git) |
| `secrets\` | your keys, one file per provider, not in git |
| `talkback.log` | one line per reply: who sent it, which part was spoken, which voice. Never the text |
| `social/preview.html` | the source of the GitHub preview image |

The program file is still called `talkback.py`, so hooks set up for earlier versions keep working.

## Security

The server listens on `127.0.0.1` only. It refuses a foreign `Host` or `Origin`, accepts only `application/json`, caps
a request at 100 KB and the queue at 24 replies, and never returns a key or its path. See [SECURITY.md](SECURITY.md)
for the trust model and how to report a problem.

## Credits

Made by [Max Tiong](https://github.com/mkraeborn) and [Alts Agency](https://alts.agency). The chevron is the Alts
Agency mark. The setup window, icons and preview image were made for this release and are under the MIT licence.

## Licence

MIT. See [LICENSE](LICENSE).
