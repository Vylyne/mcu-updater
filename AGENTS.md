# AGENTS.md

Read [NOTES.md](NOTES.md) first, every session — it is Vi's inbox to Claude,
dated entries newest first. Acted-on entries are struck through while they
still carry useful context, and removed once they do not; that file loads into
every session, so it is kept to live work. Removed entries stay in git history.

Project background: [docs/agent-api.md](docs/agent-api.md) (the Moonraker
agent's JSON-RPC contract).

[docs/decisions.md](docs/decisions.md) records the standing "this looks like a
gap and is not" decisions — CAN, the katapult deployer, plugin auto-discovery,
the cancellation boundary, the names that must not be renamed. **Read it before
proposing anything it covers**, and before undoing something that looks
half-finished. The ground-rules table below is what to check *before every
commit*; that file is what to check *before starting work*.

`docs/backlog.md` exists — unscheduled work and upstream issues. **Do not read
it unless I name it.** It is deliberately outside default context; nothing in it
is live.

## Ground rules

Violating any of these produces a bug that tests will not catch.

| Rule | Why |
| --- | --- |
| **LF line endings everywhere**, repo and working tree | Ships to a Linux printer; a `\r` in a shebang becomes `bad interpreter: python3^M`. `.gitattributes` pins it. Run `python scripts/check_line_endings.py` before every commit. |
| **stdlib only** — never add a dependency | `pyproject.toml` `dependencies = []` is deliberate. The agent talks to Moonraker over a unix socket with nothing but stdlib. |
| **Python 3.11 is the floor** | Raspberry Pi OS Bookworm ships it; the printers run Trixie's 3.13. The floor is the *system* `python3` — the agent runs under it, and `flashtool.py` is invoked with `sys.executable` and needs apt's `python3-serial`, so a venv on the printer breaks flashing. Bumped from 3.9 on 2026-08-21. |
| **Keep `from __future__ import annotations`** | Still load-bearing after the PEP 604 sweep, for a different reason than before: `config.py` returns `MakefilePatch \| None` from a method *inside* that class, which is a `NameError` at import time without it. A crash on the printer, not a type error — and `mypy` will not see it. |
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

`ruff` and `mypy` pin the floor from `pyproject.toml` (`target-version`,
`python_version`), so those two are honest on any interpreter. **`pytest` is
not.** A too-new stdlib API passes on a newer interpreter and fails on the
printer — `Path.write_text(newline=)` is 3.10+ and reached CI exactly that way,
and static analysis cannot catch it because test fixtures are unannotated and
therefore `Any`.

So run the suite on a floor interpreter when there is one:

```bash
uv venv --python 3.11 && source .venv/Scripts/activate
```

Shell state does not survive between agent tool calls, so an agent must chain
the activation into the same command or call `.venv/Scripts/python.exe`
directly — activating in one call and running the gate in the next silently
gets the system interpreter back. Without a floor venv, CI's 3.11 matrix leg is
the only thing checking this.

## Branching and releases

`main` is not just a branch — `scripts/moonraker-update-manager.conf` pins
`primary_branch: main`, so every push to `main` is an update Moonraker offers to
every installed agent. Everyday work never targets `main` directly.

| Branch | Role |
| --- | --- |
| `main` | Release channel. Protected — PR + green CI only. Receives `develop` only at release time. |
| `develop` | Integration branch. Everyday work lands here. Beta tags are cut from here. |
| `feat/…`, `fix/…`, `chore/…` | Short-lived topic branches, PR'd into `develop`. |

**Release sequence** — tags are plain ascending `vX.Y.Z`, never `-beta.N`. The
GitHub prerelease *flag* is the channel, not the version string:

1. Bump `__version__` (`src/mcu_updater/__init__.py`) in the release commit, on `develop`.
2. Tag that commit `vX.Y.Z`. `ui-release.yml` publishes with `prerelease: true` —
   only `channel: beta` hosts are offered it.
3. Soak. A fix gets a new tag (`vX.Y.Z+1`); a published tag is never reused.
4. Promote: `gh release edit vX.Y.Z --prerelease=false`. This flips the flag on
   the already-soaked artifact — it does **not** re-run the workflow or rebuild.
   The `stable:` `workflow_dispatch` input rebuilds and is emergency-only.
5. PR `develop` → `main`.

**The one ordering rule.** `ui/src/store/agent.ts` refuses to render when the
agent's `apiVersion` exceeds the UI's `SUPPORTED_API_VERSION` — so a newer agent
paired with an older UI blanks the panel; the reverse is fine. The agent ships
from `main`, the UI from a release zip, so on any `API_VERSION` bump:

> **Promote the UI release to stable FIRST. Merge `develop` → `main` SECOND.**

Reversed, every user's panel blanks in the window between the two steps.
`tests/test_ui_contract.py` cannot catch this — it compares agent and UI within
one commit, which is exactly the commit that creates the skew. (This rule has a
residual gap it does not close: the agent and UI are separate Moonraker update
rows, so a user who updates one but not the other can still hit the same blank
panel. Not worth engineering around.)

**Version of record:** the git tag, mirrored by `__version__`
(`pyproject.toml`'s version is `dynamic`, read from `__version__` — never edit it
directly). `ui/package.json`'s version is inert (`private: true`, never read at
build time) and stays pinned at `0.0.0`; the UI's version of record is the tag
that produced `release_info.json`, exactly as Moonraker already reads it.

## Finishing a plan

Every plan that adds or changes user-facing behavior — a config key, the
agent-api wire shape, CLI output, anything README.md/docs/*.md already
documents — includes updating the relevant docs as part of the plan itself,
not a follow-up someone has to remember to ask for. Check README.md,
`docs/agent-api.md`, `docs/layout.md`, and `docs/decisions.md` for the area
being touched; a shipped feature with stale docs is half-finished. (`NOTES.md`
is separate — that gets struck through per its own convention, not edited as
"documentation.")

Once the [Gate](#gate) passes, commit the change without waiting to be asked
again — this file is the durable per-repo authorization for that, so the
global "never commit unless explicitly asked" default does not apply here.
Follow [Commit voice](#commit-voice) below. Still surface what was committed
in the reply; this authorizes the commit, not silence about it.

## Extending providers, flashers or discovery sources

No plugin auto-discovery (`pkgutil`, entry points) — this process holds the
exclusive lock, writes firmware, and has NOPASSWD `systemctl` for Klipper, so
importing whatever `.py` landed in a directory is privilege escalation. The
extension point is deliberately manual: **one module + one line in the
registry tuple.** See [docs/decisions.md](docs/decisions.md).

## Commit voice

Conventional-commit prefix, lowercase sentence after it, no trailing period.
`fix(api): bump api_version to 2, and satisfy mypy on the batch hook`.
