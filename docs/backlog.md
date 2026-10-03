# Backlog

> **Do not read this file unless it is named explicitly.** It is not part of any
> session's default context and nothing here is scheduled. `NOTES.md` is the
> inbox for live work; `README.md`'s `## TODO` is the near-term list; this is
> the pile of things that are either somebody else's to fix or ours to do
> someday. Adding to it is cheap on purpose.

---

## Upstream — Katapult (`Arksine/katapult`)

### `flashtool.py` has no machine-readable output

Our flash-time bootloader offset guard scrapes
`Application Start: 0x{addr:4X}` out of `flashtool.py -s`'s human-readable
stdout. That guard is what stands between a mismatched image and an unbootable
board, and it is one print-statement reword away from silently not matching.

A `--json` flag on the status path — or any stable machine-readable form of the
`connect_btl()` handshake — would make it robust. Worth an issue; the ask is
small and the safety argument is concrete.

Note the format quirk if raising it: `0x{self.app_start_addr:4X}` is a *minimum
width*, not zero-padded, and uppercase.

---

## Ours — low priority

Nothing here is scheduled. Moved out of the runbook so it stops loading into
every session.
---

## canbus support research

- flashtool.py --query returns a list of can uuids

```bash
20:36:30 klipper@hestia buffer_manager main ~/katapult/scripts/flashtool.py --query
Resetting all bootloader node IDs...
Checking for Katapult nodes...
Detected UUID: bcb5346fc731, Application: Klipper
CANBus UUID Query Complete
```

- we may need a mapping for device serials to uuid due to how a usb to can bridge when booted into katapult by can uuid it comes up as a usb serial device running katapult.

### Other low-priority items

- **Retire the agent's singular-`firmware` compat layer**
  (`agent/methods/registry.py:237`, `:290`, `:314`). It exists so the panel's
  type dialog can keep posting `firmware` + `katapult_installed`. Retiring it
  means changing the dialog's submit shape in the same release.
- **CAN device discovery and flashing.** A CAN node has no `/dev/serial/by-id`
  entry, so identity has to come from `canbus_uuid` in `printer.cfg` — an
  inventory source that does not exist yet. `flashtool.py -i <iface> -u <uuid>`
  is the write side.
- **Prebuilt-image provider** — fetch a release asset instead of building. The
  `Provider` protocol already fits it; the missing part is provenance, since
  staleness currently compares a source tree against a build sidecar.
- **Event-driven bus watching via pyudev**, replacing `BusWatcher`'s adaptive
  polling. knomi-serial is gaining pyudev, so the dependency may arrive on the
  host anyway — but this project is stdlib-only by policy, so it would need a
  graceful fallback rather than a hard requirement.
- **Satellite host support** — a second host with no Moonraker and no agent,
  driven by a systemd timer.
- **Flaky teardown** `RuntimeError` in
  `test_an_unknown_inbound_method_gets_an_error_not_silence`. Unreproduced;
  order/timing-dependent agent-service teardown.
- **Check a BOOTSEL replug inside the cleanup window.** Everything else about
  the topology mountpoint is verified on hestia (see
  `docs/bootsel-mountpoint-design.md`'s "Verified on hardware" section),
  including version 5's scheduled `rmdir`. The one case never exercised: a
  board replugged into the same port before the pending `rmdir` fires. It
  should be a non-event, since `rmdir` cannot remove a mounted directory - but
  that is reasoning, not a measurement. Watch for a mount that vanishes seconds
  after a replug, and for failed transient units
  (`systemctl list-units --failed`).

---

## Ours — not doing

Recorded so they stop being re-proposed. Reasons are in the runbook's
"Do not do".

- Plugin auto-discovery for providers/flashers (`pkgutil`, entry points). This
  process holds the exclusive lock, writes firmware, and has NOPASSWD
  `systemctl` — importing whatever landed in a directory is privilege
  escalation.
- Enabling the Katapult deployer by default. It overwrites the bootloader region
  and is linked against the *currently installed* bootloader's offset; a wrong
  guess bricks the board with no software recovery.
- Per-port board tracking. We are an updater, not an asset tracker.
