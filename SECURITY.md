# Security

Talkback listens on your own machine, speaks text that other programs hand it, and sends that text to a
text-to-speech provider.

## Supported versions
The latest release on `main`. Windows 10 and 11; other platforms are not supported.

## Trust model
- The HTTP server binds `127.0.0.1` only and refuses any other bind address.
- Any program already running as you can ask Talkback to speak. We accept that, because such a program could make
  sound itself. A web page in your **browser** must not be able to, so a request has to look like it came from a
  program on your machine.
- The words being spoken go to your primary provider. That is OpenRouter unless you change it to Groq, Cartesia, an
  endpoint you added or Microsoft (edge-tts). If it fails, they go to the next provider in the order. For a folder with
  a custom voice, they go to that voice's provider first. Each key goes only to its own provider.
- `send.py` writes one file, `.talkback.json` (a random id and the folder's name), in the folder an agent runs in.
  `settings.json` keeps each agent's name and the last two parts of its folder path; `GET /status` does not return them.
- The setup window gets Microsoft's voice list from Microsoft when you open the backup voice.
- `send.py` reads Claude Code's job files in `%USERPROFILE%\.claude\jobs` and looks for a marker file in
  `%USERPROFILE%\.cchelper`, so that helper sessions are not spoken.
- Keys stay in your own files or environment. They are never in this repository, the log, the settings that
  `GET /status` returns, or an error message.

## Guarantees the release must meet
These are verified before every release:

1. `/say` accepts `application/json` only, so a cross-origin page cannot post to it without a preflight, and
   requests carrying a foreign `Origin` are refused.
2. A request body is capped (100 KB) and the queue is bounded; excess is dropped and logged, never buffered forever.
3. Audio is written to a private temporary directory, not a predictable filename in `%TEMP%`.
4. `GET /status` reports settings with key paths and values redacted.
5. `settings.json` is written atomically; a corrupt file is kept as `settings.bad.json`, logged, and replaced with
   defaults instead of preventing startup.
6. `talkback.log` never contains reply text, not even inside a logged exception. A test proves it with a canary.
7. No shell command is built by pasting a path into a string; the auto-start call passes an encoded command.
8. Runtime dependencies are pinned, and `pip-audit` runs in CI.

Tests cover guarantees 1, 2, 4, 5 and 6 (class `Hardening` in `tests/test_setup.py`) and the encoded auto-start call in
7 (class `StartedWith` in `tests/test_send.py`). CI checks guarantee 8. Guarantee 3 has no test.

## Reporting a vulnerability
Open a private security advisory on the GitHub repository (Security → Report a vulnerability). Do not open a public
issue first. Include the version, what you did, and what happened; a reply usually comes within a few days.

If you think a key leaked, rotate it with the provider. Removing it from your machine is not enough on its own.

---
Made by Max Tiong ([github.com/mkraeborn](https://github.com/mkraeborn)) and Alts Agency
([alts.agency](https://alts.agency)).
