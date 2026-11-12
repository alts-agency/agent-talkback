# Security

Talkback listens on your own machine, speaks text that other programs hand it, and sends that text to a
text-to-speech provider. That is the whole attack surface.

## Supported versions
The latest release on `main`. Windows 10 and 11; other platforms are not supported.

## Trust model
- The HTTP server binds `127.0.0.1` only and refuses any other bind address.
- Any program already running as you can ask Talkback to speak. That is accepted: such a program could just make
  the sound itself. The line that matters is the **browser**, which is why a request must look like it came from a
  local client, not from a page.
- Reply text leaves the machine to whichever provider is selected: OpenRouter (HeyGen voices), Groq, or Microsoft
  (edge-tts). Your key is the one you configured; nothing is sent anywhere else.
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

Each guarantee above has a test in `tests/test_setup.py` (class `Hardening`), so a change that weakens one fails the
suite.

## Reporting a vulnerability
Open a private security advisory on the GitHub repository (Security → Report a vulnerability). Do not open a public
issue first. Include the version, what you did, and what happened; a reply usually comes within a few days.

If you think a key leaked, rotate it with the provider. Removing it from your machine is not enough on its own.

---
Made by Max Tiong ([github.com/mkraeborn](https://github.com/mkraeborn)) and Alts Agency
([alts.agency](https://alts.agency)).
