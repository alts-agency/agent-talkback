# secrets
Your keys live in this folder: one file per source, named after the source's id (`openrouter.local`, `groq.local`).
Each file holds one key and nothing else.

`console.py` writes them here when you paste a key, so you never need to open these files by hand. The program reads
a key at every reply, which means a new key works without a restart.

This folder is in `.gitignore`: only this README is in the repository. A key is sent only to the provider it belongs
to, and never to `settings.json`, `talkback.log` or the status page.

To keep a key somewhere else instead, point the source's `key` at it: `file:C:\some\folder\openrouter.local`, or
`env:OPENROUTER_API_KEY` when it comes from the environment.

---
Made by Max Tiong ([github.com/mkraeborn](https://github.com/mkraeborn)) and Alts Agency
([alts.agency](https://alts.agency)).
