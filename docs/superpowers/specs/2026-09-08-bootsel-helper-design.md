# Closed-loop BOOTSEL flashing through firmware helpers

## Purpose

Allow `fw.flash` to update a configured, running Roadrunner with its staged
UF2 without asking an operator to hold BOOTSEL or unplug other RP2040 boards.
The mechanism must be reusable by another firmware only through an explicit,
reviewed implementation; configuration must never select arbitrary Python.

## Configuration and registration

`[firmware ...]` gains an optional `helper:` key. Its value names one member
of a static helper registry. The first entry is `roadrunner`:

```ini
[firmware roadrunner]
source: ~/roadrunner/rp2040
builder: cmake
helper: roadrunner
```

`helper:` names a registered capability rather than a module path. There is no
`helper_module:`, dynamic import, entry point, or directory scan: this process
can stop services and write firmware, so config-controlled code loading would
be privilege escalation. Adding a helper remains one module plus one registry
entry.

Helpers live in `src/mcu_updater/helpers/`: `spec.py` owns the narrow protocol,
`registry.py` owns the static tuple, and `roadrunner.py` owns the first
implementation. They are separate from both discovery (which finds devices)
and flashers (which write images).

The key belongs to the firmware family because the application firmware owns
the reboot protocol. A type resolves its application family as it already does
for artifact selection, then obtains that family's helper. Firmware with no
`helper:` remains on its existing routes.

## Helper boundary

The initial protocol has exactly one optional operation:

```python
request_bootsel(bench, target, ctx) -> BootselHandoff
```

It is invoked only for a configured application device that the agent has
already resolved by durable identity. It may validate and communicate with the
application, but it never writes a UF2. It returns the transient topology
evidence needed to select one BOOTSEL mount. No provisioning, discovery, or
general hook interface is moved into this abstraction.

Roadrunner's implementation reuses its existing confirmed-device and INFO
validation routines. It captures the application's USB topology, sends the
Roadrunner `REBOOT_BOOTSEL` admin request, and waits for the old CDC device to
disappear. The direct USB bridge gains only the matching `bootsel` operation.

## Flash flow

`fw.flash` accepts a configured CMake type when its application family
declares a helper that supports the closed-loop BOOTSEL operation. It uses the
provider-staged UF2 for that application and selects a dedicated helper-backed
BOOTSEL flasher. Katapult-backed types retain the existing flashtool route;
the new route is chosen from the declared helper, not from a chipset prefix or
a Roadrunner serial pattern.

The helper-backed flasher requires services stopped. The normal print-idle gate
and `stop_services` resolution occur before the CDC request, so Klipper cannot
hold the port during the handoff.

After the requester returns a handoff, shared BOOTSEL logic:

1. waits for exactly one mounted volume whose USB topology matches the
   application port;
2. requires `INFO_UF2.TXT` on that mount;
3. copies the selected UF2; and
4. waits non-fatally for the configured device to return, before normal
   Klipper readiness handling.

Topology is a single-operation correlation key, never a stored board identity.
Matching normalizes the measured serial and disk `by-path` forms, including the
`usb`/`usbv2` alias and trailing mass-storage/interface components. Zero or
multiple matching mounts refuse the write. A bystander BOOTSEL volume on a
different topology is ignored.

The existing generic `Bootsel` flasher stays responsible for factory-bare
boards already in BOOTSEL. It retains the current zero/multiple-volume refusal
when it receives no selected topology. Shared mount validation/copy code is
extracted only as necessary so both routes have identical UF2 safety checks.

## Errors and reporting

The job reports a distinct actionable failure for each boundary: Roadrunner
confirmation/admin failure, application port that did not disappear, no
matching BOOTSEL device, matching device not mounted, ambiguous matching
mounts, missing `INFO_UF2.TXT`, and missing UF2 artifact. It never falls back
to another mounted RPI-RP2 volume or to a stale application port.

The post-copy application re-enumeration wait is non-fatal, matching existing
flash behavior: a successful write is not retroactively reported as failed
solely because readiness was slow. The normal service restart/readiness path
remains the final operational check.

## Tests and documentation

Tests cover static helper lookup and unknown-helper config rejection;
Roadrunner requester confirmation, command issuance, and disappearance wait;
topology normalization and exact-mount selection in the presence of another
BOOTSEL board; marker, mount, timeout, and ambiguity failures; no regression to
the bare-board BOOTSEL flow; and `fw.flash` routing of a configured CMake
Roadrunner.

Update `README.md`, `docs/layout.md`, `docs/agent-api.md`, and the CMake and
BOOTSEL design/status documents to document `helper:`, CMake flashability, the
closed-loop safety conditions, and the retained manual bare-board flow.
