# NOTES.md

Vi's inbox to Claude. Dated entries, newest first. Entries are read at session
start.

Acted-on entries are **struck through** while they still carry context worth
having to hand, and **removed** once they do not — this file loads into every
session, so it is kept to live work. Removed entries stay in git history
(`git log -p NOTES.md`).

---

## 2028.08.29-0227 - UI improvements

### theming

- ~~apply theme to dropdown/select boxes.~~ Done — global rule on
  `select`/`input`/`textarea` in `style.css`, plus a KconfigDialog layout pass
  added mid-plan from a screenshot comparison (ragged control column, bool
  checkboxes now `.switch` toggles, help text no longer orphaned, dividers
  dropped, "USB ids" no longer reads as a link).
- ~~restore green device checks at beginning of the device rows.~~ Done —
  `UiIcon` gained a `tone` prop bound to its own `data-tone`, so the leading
  status icon matches the tone rule directly instead of relying on inheritance
  `4068a92` correctly cut off.

Known gap, not fixed here: `style.css` still decides light/dark from
`prefers-color-scheme` alone, not from Mainsail's actual runtime theme — an
embedded panel goes dark whenever the *OS* is dark regardless of what Mainsail
is showing. Needs a `data-theme` hook plus a host channel; separate feature.

### untracked devices on bus

- ~~add option ignore device~~ Done — `fw.bus.ignore`/`fw.bus.unignore`
  RPCs, persisted as `ignored_serials` in `[updater]`, flagged not filtered
  (still shows up, just marked) so a mis-tap is recoverable from an "Ignored"
  disclosure in the panel.
- ~~move to the icon buttons like untrack button, +(track) and x(ignore)~~
  Done — `+` opens a menu (existing type, or "New type from this…" into
  `TypeDialog`), `×` ignores. Non-MCU bus entries (USB adapters etc, `is_mcu:
  false`) now render muted with no `+`, since the agent refuses adopting them
  anyway.

### settings panel

- ~~settings panel can default collapsed.~~ Done. Note: the panel-collapse
  localStorage key was bumped (`panel` → `panel2`) so the new default actually
  takes for browsers that already had state saved — every other collapsible
  panel's remembered state (JobPanel, debug panels) reset once as a side
  effect.

### joblog

- ~~we are moving down to the joblog when a new job starts, but... we don't
  get it fully scrolled into view.~~ Done — the scroll now re-fires once the
  log block has actually rendered with content, instead of racing the 250ms
  batch that used to grow the panel out from under it.
- ~~joblog is not rendering coloured text.~~ Done — build/flash tool output is
  now classified (`stdout_error`/`stdout_warn`) by the agent as it streams, and
  the joblog colours by `data-stream`. Agent-only messages (`info`/`warn`/
  `error`/`cmd`) were already distinct streams and are now coloured too.
