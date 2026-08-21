# NOTES.md

Vi's inbox to Claude. Dated entries, newest first. Entries are read at session
start.

Acted-on entries are **struck through** while they still carry context worth
having to hand, and **removed** once they do not — this file loads into every
session, so it is kept to live work. Removed entries stay in git history
(`git log -p NOTES.md`).

---

**The inbox is empty.** Cleared 2026-08-21, when the schema-first rebuild was
retired. The four entries that were here are all either fixed or promoted to a
durable home:

- The fork's `FW_SUPPORTED_API_VERSION` regression — **fixed**, fork commit
  `9ccdcbe2`. Promoting `v2.18.4-vylyne.20` off the beta channel is now a
  [README](README.md) TODO item.
- Two Mainsail-fork bugs found during the Step 16b migration — **fixed**. The
  `.vue` type-checking gap they exposed is written up in
  [docs/decisions.md](docs/decisions.md), which records why `vue-tsc` cannot
  close it at any version.
- The phantom `FwConfig` slots in `config.py` — **fixed** in `726f31c`, with
  the non-mutating `fw_get()` accessor that stops the read-with-a-write-side-
  effect shape recreating it.
- The RP2040 BOOTSEL flasher shipping untested — **still true**, now a
  [README](README.md) TODO item so it is tracked where the other open work is.
