# CLAUDE.md

Read [NOTES.md](NOTES.md) first, every session — it is Vi's inbox to Claude,
dated entries newest first. Acted-on entries are struck through while they
still carry useful context, and removed once they do not; that file loads into
every session, so it is kept to live work. Removed entries stay in git history.

Project background: [docs/agent-api.md](docs/agent-api.md) (the Moonraker
agent's JSON-RPC contract) and [docs/mainsail-fork.md](docs/mainsail-fork.md)
(how the Mainsail panel fork stays small and rebaseable).

`docs/backlog.md` exists — unscheduled work and upstream issues. **Do not read
it unless I name it.** It is deliberately outside default context; nothing in it
is live.

## Scope discipline
Only read files I explicitly name or point to. Do not read additional files to "get context," "understand the project," or "see how things connect" unless I ask you to.
If you think reading more files would help, ask first. One sentence: "Want me to also read X?" Wait for my answer.
This applies to every task in this project. No exceptions for "just checking" or "quick look."

## Ground rules

Violating any of these produces a bug that tests will not catch.

| Rule | Why |
|---|---|
| **LF line endings everywhere**, repo and working tree | Ships to a Linux printer; a `\r` in a shebang becomes `bad interpreter: python3^M`. `.gitattributes` pins it. Run `python scripts/check_line_endings.py` before every commit. |
| **stdlib only** — never add a dependency | `pyproject.toml` `dependencies = []` is deliberate. The agent talks to Moonraker over a unix socket with nothing but stdlib. |
| **Python 3.9 is the floor** | Raspberry Pi OS. No `match`, no `X \| Y` at runtime, keep `from __future__ import annotations`. `UP007`/`UP045` are ruff-ignored on purpose — leave `Optional[X]` alone. |
| **Never pip-install kconfiglib** | Klipper and Katapult each vendor their own *locally patched* copy. Their sentinels (`MENU`, `BOOL`) are different objects across trees; cross-comparing silently yields `False`. Always load from the tree being configured. |
| **`posix_only` tests silently skip on Windows** | Every flock/signal/`/proc` assertion. A green Windows run proves nothing about locking. CI runs Linux too — trust that. |
| **Use `scripts/mutation_test.py`, never a throwaway** | An inline mutation script once stranded a sabotaged guard on disk. `tests/test_mutation_helper.py` exists because of it. |
| **Run `mutation_test.py` one spec at a time** | Never in parallel, and never a full sweep under a shell timeout shorter than it needs. An interrupted sweep strands a *live* mutation in the source — this happened once, in `firmware.py`, and only `test_no_mutation_is_left_live_in_the_source` caught it. After any interrupted run, read the hygiene test's output; do not settle for "the command finished". |
| **Git Bash mangles `/FI`-style switches** into paths | Silently turns wait-for-process loops into no-ops. Use PowerShell for those. |
| **Never interrupt a firmware write** | Cancellation is checked *between* targets, never inside one. Half an image is a brick. |
| **Bench board only** for any flash test | Never the toolhead. Recovery from a bad flash there is a DFU hunt inside the hotend assembly. |

## Gate

Run before every commit:

```bash
python -m pytest -q
python -m ruff check src tests scripts
python -m mypy src
python scripts/check_line_endings.py
```

## Extending providers or flashers

No plugin auto-discovery (`pkgutil`, entry points) — this process holds the
exclusive lock, writes firmware, and has NOPASSWD `systemctl` for Klipper, so
importing whatever `.py` landed in a directory is privilege escalation. The
extension point is deliberately manual: **one module + one line in the
registry tuple.**

## Commit voice

Conventional-commit prefix, lowercase sentence after it, no trailing period.
`fix(api): bump api_version to 2, and satisfy mypy on the batch hook`.
