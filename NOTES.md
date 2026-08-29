# NOTES.md

Vi's inbox to Claude. Dated entries, newest first. Entries are read at session
start.

Acted-on entries are **struck through** while they still carry context worth
having to hand, and **removed** once they do not — this file loads into every
session, so it is kept to live work. Removed entries stay in git history
(`git log -p NOTES.md`).

---

- ~~save service state before attempting to stop the restore same state, (dont start the service if it was already stopped.)~~ shipped in `d2f7ee4` — `services_stopped()` (`service.py`) already checks `is_active()` before stopping and leaves an already-stopped service stopped.
- ~~since we track what we flask i may want to and an option to also track a secondary folder's git sha, so we can report a version needs to be rebuilt and flashed if the either the main source orthe secondary folder has been updated, may be worth using a list extra_repos. this would be useful in situations like our buffer_manager devices, which flash klipper with an extra src file added as a makefile patch.~~ tracking itself shipped in `0d45e0a`; documented in README.md's `<fw>_extra_repos` bullet all along. What was actually missing — the standalone UI's edit view couldn't set it, and the silent-failure mode (a typo'd path never warns, staleness for it just never fires) went undocumented — is now fixed: `fw.type.add`/`.update` accept `<fw>_extra_repos`/`<fw>_makefile_patches`, warn (don't refuse) on a path with no git HEAD yet, and TypeDialog's Advanced section edits both.

canbus support research

- flashtool.py --query returns a list of can uuids

```bash
20:36:30 klipper@hestia buffer_manager main ~/katapult/scripts/flashtool.py --query
Resetting all bootloader node IDs...
Checking for Katapult nodes...
Detected UUID: bcb5346fc731, Application: Klipper
CANBus UUID Query Complete
```

