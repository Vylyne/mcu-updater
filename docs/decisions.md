# Decisions

Things that look like gaps and are not. Each one was decided deliberately, and
each one has been proposed again at least once — so the reasoning lives here
rather than in a commit message nobody re-reads.

This file is for *standing* decisions. Rules that a change can violate silently
live in [AGENTS.md](../AGENTS.md)'s ground rules instead; the split is
that a ground rule is checked before every commit, and a decision here is
consulted before starting work that would undo it.

## Do not do

### Do not add plugin auto-discovery for providers, flashers or discovery sources

No `pkgutil`, no entry points. This process holds the exclusive lock, writes
firmware, and has NOPASSWD `systemctl` for Klipper — importing whatever `.py`
landed in a directory is privilege escalation, not a plugin system.

The extension point is deliberately manual and documented: **one module + one
line in the registry tuple**. `providers/registry.py`, `flashers/registry.py`
and `discovery/registry.py` each say so in their own docstring.

### CAN discovery and flashing use UUID identity and live interfaces

CAN discovery and flashing are implemented. CAN nodes are identified by their
UUID and tracked in `canbus_uuids`; Linux interface names are never persisted.
Ignored untracked nodes follow the same identity rule: `ignored_canbus_uuids`
stores a UUID once and marks every interface sighting ignored until restored.
Discovery scans every current network device whose sysfs type is `ARPHRD_CAN`,
retains successful results when another interface reports an error, and keeps
non-USB CAN nodes in the result. USB adapter topology metadata is attached when
the shared USB sysfs collector can resolve it.

For an adopted node, Klipper's configured `canbus_interface` is used when
present (defaulting to `can0`); otherwise the updater retries every current CAN
interface. The unified `flashtool` flasher handles both USB serial and CAN
targets, and its `-f` operation performs the bootloader transition itself.

### Do not enable the katapult deployer

It overwrites the bootloader region and is linked against the *currently
installed* bootloader's offset. A wrong guess bricks the board with no software
recovery — the failure mode is a DFU hunt, and on a toolhead that means opening
the hotend assembly.

### Do not "fix" `-dirty` in reported versions

Normal for makefile-patched types: Klipper stamps the version from git while the
patch is applied, so the tree is briefly dirty and
`v0.13.0-712-g6d43f8b3-dirty-...` is the correct output. It must not be
suppressed, because it must not read as out of date.

### Do not synthesize a sha into Cartographer's `CONFIG_VERSION`

Cartographer's fork patches Klipper's `buildcommands.py` to stamp
`CONFIG_VERSION` (a literal from the `.config`) instead of `build_version()`'s
git describe. The describe is still computed, just discarded — so `mcu_version`
carries no commit at all, and `CartographerHelper.running_sha()`
(`helpers/cartographer.py`) correctly returns `None`.

Since `read_config_version()` returns the string verbatim, appending the fork's
HEAD before `make` — `CARTOGRAPHER 6.2.0-gd34db33` — would work:
`device_info.KLIPPER`'s sha regex would match it and the whole existing
sha-comparison path would run unchanged. Rejected anyway, because the cost
lands on things that matter more than the convenience:

- A synthesized value in the saved `.config` differs from the vendor seed, so
  `profiles.status` reports `customised` permanently — destroying the one
  signal that means "the user edited this".
- It moves `config_sha256` on every commit of the fork, so `artifact_status`
  reports `CONFIG_CHANGED` forever.
- Avoiding both means injecting it outside the saved config, which makes our
  builds unreproducible by the vendor's own instructions.
- It does not even remove the need for the sha-less path: a board on the
  official prebuilt binary still reports a bare string, so `states.VERSION_ONLY`
  has to exist regardless. Synthesizing a sha would only move *our own* boards
  onto the good path, at that cost, for no boards we don't already control.

So the sha is gone, and putting one back costs more than it buys. A cartographer
is instead judged by comparing `CONFIG_VERSION` itself against what the built
`.config` carries (`profiles.stamped_version`), backed by our own flash record
the same way the sha path already is — see `states.VERSION_ONLY` and
`FlashLog.entry_for`'s version-based discard clause.

### Do not reintroduce per-port board tracking

Removed deliberately in `9ebbaef`. This is an updater, not an asset tracker —
which port a board sat on last time is not a fact worth persisting, and keeping
it invites writes addressed to a remembered port rather than a confirmed one.

### Do not rename `needs_ports_free`

`discovery.spec.Source.needs_ports_free` is a distinct question from
`flashers.spec.Flasher.needs_services_stopped` - one asks whether a discovery
pass needs the bus quiet, the other whether a write needs its device's
holders released - and the two must not be renamed to match each other. The
flasher side generalised to a per-type `stop_services` list in the
`stop_services` module; this one did not need the same treatment and is
unrelated to it.

### Do not rename `STATE_KLIPPER`

It means "running an application", not "running Klipper" — the bootloader
predicate in `discovery/spec.py` reads any non-Katapult firmware name as this
state, so a Cartographer sights as `STATE_KLIPPER`. That inversion is the point:
it stops every vendor fork being a case to handle.

The constant is what every flasher's `states` tuple matches on, so per the
`needs_services_stopped` precedent (it was `needs_klipper_stopped` until the
`stop_services` list landed with it) the rename lands with the thing that
makes it true, or not at all. The meaning is documented where it is defined.

### Do not revert `is_mcu` to a firmware-name allowlist

Inverted to a denylist on 2026-08-29: `is_klipper or is_katapult` refused
every board this tool didn't already know the firmware name of, which
blocked legitimate custom-firmware boards (a Raspberry Pi Pico was the
motivating case) from ever being tracked. `is_mcu` now denies a short list
of known USB-serial-bridge chip identifiers (`KNOWN_SERIAL_BRIDGE_NAMES` in
discovery/byid.py) instead - the actual thing it exists to protect against
(a Knomi's CH340 offered as if it were a trackable board). This matches the
`STATE_KLIPPER` decision above: any firmware name this tool doesn't
recognise is presumed to plausibly be a board, not refused for being
unfamiliar.

### Do not delete `src/updatefw.py` or 'mcu-updater.py'

they are the documented entry points.

### The USB topology collector is shared by discovery and diagnostics

USB sysfs collection lives in `discovery/usb.py` and is shared by topology,
by-id discovery, watcher support, and CAN adapter metadata. The standalone
`scripts/usb_topology.py` remains a human diagnostic CLI, while the package
collector supplies the reusable data path; the old split is no longer a reason
to defer topology or CAN support.

The standalone UI runs `fw.status` and the explicit `fw.canbus.scan` together
on refresh. Their results are stored independently, and a generation guard
prevents an older overlapping CAN scan from replacing a newer result.

### New firmware-specific code goes behind the helper seam

`helpers/` exists because of Cartographer. Its one oddity - a hand-maintained
`CONFIG_VERSION` literal with no commit in it - was squeezed into the build and
status paths as special cases, and it is still there, spread across files that
have no other reason to know that vendor's name. The seam was added so the next
such oddity would have somewhere to go that is not "inside whichever generic
path noticed it first".

So, going forward:

- **Providers are generic.** A provider answers questions about a *build
  system* - PlatformIO, CMake, kconfig+make. Nothing in `providers/` should
  name a vendor or a board.
- **Flashers are generic.** A flasher answers questions about a *transport* -
  flashtool over USB/CAN, esptool, DFU, BOOTSEL mass-storage. `dfu` and
  `bootsel` are the shape to copy: they describe a mechanism, not a product.
- **Discovery may be firmware-specific, and legitimately is.** How a board
  announces itself is a property of its firmware, so `discovery/roadrunner.py`
  and `discovery/knomi_serial/` are in the right place. `byid` is generic.
  `canbus`, as built, is Klipper/Katapult-specific - that is a known
  inaccuracy of the current shape, not a licence to add more.
- **Everything else vendor-shaped goes in `helpers/`.** A one-off protocol, a
  provisioning step, a version string only one firmware stamps: a helper, named
  in config on the `[firmware ...]` family, reached through `helpers/spec.py`.

This is a rule about *new* code. Migrating what already exists - Cartographer's
version handling in particular - needs its own design and plan; see the
`## TODO` entry in [README.md](../README.md). The seam answers a narrower
question today than discovery and version reporting need, so it likely has to
widen before anything moves.

### Auto-provisioning is opt in, watcher-inline, and deployment-gated

Spec section 10, Ruling 14. The klippy extra provisions on `klippy:connect`,
which reaches only boards already configured in Klipper; the host's watcher is
the component that sees a board plugged in and configured nowhere.

`auto_provision: true` is per-family opt-in because a `[firmware]` section
should not write to hardware nobody mentioned. It is not sufficient authority
on its own: the deployment-wide hardware-write predicate that controls the
advertised maintenance methods also gates this unattended irreversible write,
and defaults closed at the provisioning function's boundary. Late adoption is
not gated because it is a registry write completing an operation the operator
already requested.

Provisioning runs inline on the watcher thread, with no job: it is a sub-second
helper call, and a job entry would appear in the panel for something nobody
asked for. A held operation lock is the one failure worth retrying because the
unchanged bus would not otherwise prompt another attempt. Deployment-policy
refusals and non-busy updater errors are not retries. A handler retry does not
re-emit the `bus` event because its payload is identical.

Within one watcher sweep, a serial reaches the provisioning write path at most
once, even if multiple opted-in families claim it. A failed or busy attempt is
also spent for that sweep; the next poll is the retry boundary.

### Do not give `Confidence` a fourth degree of certainty

Three tones and a tri-state `safe_to_write`, built the way `states.py` is. A
"probably" bucket is one more thing for two call sites to disagree about.

`safe_to_write` is never `True` on absent evidence, for the reason
`DeviceStatus.needs_flash` already enforces: absence of evidence is not
evidence.

### Config keys borrow the upstream tool's own vocabulary

When a key names a concept some external tool already owns, spell it that
tool's way rather than inventing a house word for it. `cmake_target:` names
what `add_executable()` creates and what `make <target>` / `cmake --build
--target <name>` address; an earlier draft called it `variant:`, which was
wrong twice — it is not CMake's word, and CMake Tools already uses "variant"
for the *build type* (Debug, Release, MinSizeRel), so a user would reasonably
have put `Release` in it. `platformio_env:` follows the same rule: `env` is
what `platformio.ini` calls the section, and the `platformio_` prefix says
which module reads it.

The user reading the key has the upstream tool's documentation open, not ours.
A house synonym means they have to learn a mapping, and a synonym that
*collides* with a real upstream term means they learn the wrong one first.

Namespace it when the bare word is already loaded here. `cmake_target:` rather
than `target:`, because `BuildTarget`, `FlashTarget` and the `targets[]` wire
shape are three different things a bare `target:` would sit ambiguously
beside. The prefix also says which vocabulary the word belongs to, which is
the point.

A key read by one seam module is spelled `<seam module>_<param>`:
`platformio_env`, `cmake_target`, `kconfig_make_profile`,
`knomi_serial_device_map`. The param keeps the upstream word; the prefix says
which builder or helper reads it. Keys every type has (`firmware`, `chipset`,
`serials`, `canbus_uuids`, `stop_services`) and keys every family can have
(`source`, `builder`, `helper`, `flashers`, `submodules`) stay unprefixed, and
per-family kconfig keys keep their family prefix (`klipper_extra_args`). An
old spelling is refused with the new one named, never read under both.

### Do not spell the stop-list key `managed_services:`

`docs/backlog.md` sketched `managed_services:`, borrowed from Moonraker's
`[update_manager] managed_services:` next door in the same file. Rejected when
the list actually landed, for two reasons.

It drops the false cognate: Moonraker's key accepts only a restricted
vocabulary (the section's own name, `klipper`, or `moonraker`), while ours
takes arbitrary systemd unit names - two files, open in the same Mainsail
editor, using the same word for different rules is the kind of thing a user
copies from one into the other and gets a silent refusal or a silent no-op.

And `stop_services:` is imperative rather than declarative: *stop these*
reads as an instruction, and instructions replace - which is what makes
`stop_services.py`'s override-never-merges resolution rule self-evident
instead of a rule to memorise. A declarative name invites the merge the
design specifically rejects.

### Do not widen sudoers for an arbitrary `stop_services` unit

`scripts/sudoers.d-mcu-updater` grants NOPASSWD for exactly three commands on
the literal unit `klipper` - "three exact commands for one unit, no
wildcards," deliberately. A `stop_services` list can name any unit, and
`install.sh` does not go editing that file to widen it for one.

A unit missing from sudoers (systemd backend) or `moonraker.asvc` (moonraker
backend) hard-fails instead: `service.services_stopped` verifies every stop
and raises `ServiceControlError`, naming the exact sudoers lines to add,
before any write happens. This is deliberately not best-effort the way
`service.paused()` is - a firmware write racing a service that still holds
the port is not a clean failure, it is a corrupted flash. Widening the
allowlist for a third-party unit (a display's own watcher, say) is that
project's own installer's job, the same way `knomi_serial`'s would be -
not ours to do on their behalf, and not `install.sh`'s to guess at.

### Do not serve the standalone UI from inside the agent's git checkout

`~/mcu-updater-ui` (`UI_PATH`), never `~/mcu-updater/ui/dist`. Moonraker's
`type: web` update manager (`net_deploy.py`, `_validate_release_info`) refuses
a `path` inside a git repository — the install is marked invalid and never
updates. See [docs/standalone-ui.md](standalone-ui.md).

### Do not move the standalone UI under `~/printer_data/mcu-updater/`

It would technically work — Moonraker does not forbid it — but
`_extract_release()` `rmtree()`s a `type: web` path before every update, and
one directory up from there is `.updater.state`, the flash-recovery journal.
A Moonraker-managed directory that gets wiped on a schedule has no business
sharing a parent with state that must survive every update. `UI_PATH`
defaults to `~/mcu-updater-ui`, a sibling of `~/mcu-updater` itself, matching
what `~/mainsail` and `~/fluidd` already do.

### Do not edit Mainsail's own nginx site file to add the standalone UI as a subpath

nginx has no Caddy-style `import` that lets one server block inject a
`location` into another from the outside. The only way to run the standalone
UI as a Mainsail subpath is hand-editing the file KIAUH/mainsail-config owns
and rewrites on update — fragile, and not something `install.sh` does on a
user's behalf. The supported path is the UI's own nginx site on its own port
or FQDN (`scripts/nginx.sites-available-mcu-updater`); an iframe embed
(planned) is the supported way to fold it back into Mainsail visually.

### Do not move the cancellation boundary

It stays *between* targets, in `flashers/batch.py`. Cancellation is never
checked inside a single write, because half an image is a brick.

### Do not revert Kconfig bools to a bare checkbox

Tried, reversed on 2026-08-29. A bare `<input type="checkbox">` was meant to
read as menuconfig's `[*]` symbol rather than a preference toggle, but the
standalone UI is not menuconfig - visual consistency with the rest of the
panel's `.switch` toggles won. Tristate nodes are unaffected: they render
through a `<select>` (y/n/m), not a checkbox, so no third state is lost by
this.

## Conclusions that close an avenue

### Historical Mainsail-fork decisions

The former fork and its release channel are retired. This historical decision
is superseded; the supported client is the standalone UI documented in
`docs/mainsail-fork.md`.

### One walk over `[type]` sections

`typelist.py` is the only code that decides which builder owns a `[type]`
section. `Registry.load`, `pio.load` and `cmake.load` are views that filter its
list by builder, until their callers read the list directly. Three private
walks were how a Roadrunner type existed for the agent and not for the CLI. A
new reader of type sections reads the list; it does not open the file.

`Registry`'s `declared_*` methods (`declared_type_names`,
`find_declared_types_for_serial`, and friends) still call `sections.read`
directly - deliberately, since they answer "what's in the file, whoever builds
it", the one place ownership does not apply. That walk names sections; it
never decides who builds them.

`typelist.read` never raises and `typelist.validate` is strict. Anything that
answers a question about one name (`providers.selection`) uses the lenient
half, so one malformed section cannot break another type.

### Presence comes from the inventory

`inventory.py` joins the declared identities from the one type list with one
injected sweep. A status path, the CLI or anything else that asks "is this
board plugged in" reads a row; it does not scan and match on its own. The rule
is exact serial, exactly one sighting. The by-id chipset segment is not a
filter: it is the firmware's choice of name, not the board's identity.

### A family declares its flashers and its helper

`flashers:` is required on every `[firmware ...]` section. Neither it nor
`helper:` is inferred: an inferred flasher list is the chipset-and-state guess
the one-pipeline design removes. The known names are static tuples in
`firmware.py` held equal to the registries by tests, so `typelist` never
imports hardware code.

The two keys are refused in different places, on purpose. A missing or
misspelt `flashers:` is refused when the config loads, which `fw.status`
reaches too - so a printer upgrading past this shows a config error naming the
line to edit instead of a panel, and the hand edit is the migration. A
misspelt `helper:` raises from `helpers.for_name`, where a capability is
actually asked for. That difference is the asymmetry it looks like: `flashers:`
is required of every section, so a config missing it cannot flash anything,
while `helper:` is optional and a typo in it should cost one family rather
than every row in the panel.

### A family's list picks the flasher

`flashers.select` walks the family's `flashers:` list and takes the first
flasher whose `supports(device, helper)` says yes. There is no global
chipset-and-state table: the former global selector made an RP2040 reach exactly
one flasher whatever it ran. `helper_bootsel` is folded into `bootsel` because a flasher
describes a mechanism, and "ask the firmware to enter BOOTSEL first" is a step
of that mechanism the helper supplies, not a second product-named flasher.
`needs_services_stopped` can therefore differ per target, and a target's own
value wins over its flasher's. Do not turn it back into a class-only
attribute: a board already in BOOTSEL would then stop Klipper for nothing, or a
helper-requested one would write under a running Klipper.

### A device nothing can write is a failure, not an abort

Spec §8 step 1. `flashers.select_each` turns a `NoFlasherError` into a
`failures[]` entry with `"flasher": null`, and `write_all` reports it with the
writes that failed. A single-device RPC raises instead, before a job exists.
First install asks `[firmware katapult]` the same question and keeps its
`unsupported_chipset` refusal. The serial `fw.flash` job collects its write's
exception (`write_all(errors=...)`) and re-raises it, because the job's error
code was already on the wire.

### The batch loop is the only writer of the flash ledger

Ruling 12. `Flasher.record` describes what was written; `flashers.batch.
write_all` files it, right after the write and before `settled`, the service
restart, or a later device's failure. Four writers each carried their own
dry-run guard and their own idea of which sidecar schema to read, and the
CMake one filed after the batch returned — so a failed service restart lost
the record of a copy that had already landed.

A flasher still reads its own builder's sidecar, because the two schemas in
this tree spell the tree commit differently (`fw_sha`, `sha`) and the flasher
that wrote the image is the one side that knows which it is reading. What a
flasher no longer decides is whether the run was a rehearsal, where the ledger
lives, or whether losing it is worth failing a good write over.

### Device info is read through the family's helper

What a board is running - its commit, whether it was dirty, its image digest -
is read by the handler its family's helper supplies (`helpers.DeviceInfoReader`,
`helpers.ImageReporter`), and by `device_info.KLIPPER` for a family with none.
There used to be a sha regex in `status.py` for boards and another in `pio.py`
for screens, and a Roadrunner's describe went through whichever the caller
reached for. One reader per firmware means one answer per board. Cartographer
has a helper for exactly one reason: to say its version has no sha, rather
than leave that to a regex that happens not to match. A Roadrunner reports the
same device info on usbserial, i2c and uart; a field firmware does not report
is absence, never mismatch, whatever the transport.

### A digest match is the end of the question

`verdict.decide` checks the board's reported image digest before it looks at
any version string, and the check is decisive in both directions. A mismatch is
`unexpected_image`; a match is up to date, and neither a version that reads as
older, nor a dirty tree, nor this tool's own flash record can overturn it.

The tempting extra caution - "the digest matches, but our record says we last
wrote a different binary, so call it `artifact_changed`" - is wrong, and
`artifact_changed`'s own definition says why. It exists because a *commit*
match is a weak proxy: same commit, edited makefile patch, different bytes. The
record is what makes that case visible. A digest match is not a proxy for
anything; it is the running bytes measured against the bytes on disk, and the
sidecar computes `bin_sha256` and the digest fields from the same file in the
same write, so they cannot disagree about which image they describe. Letting
the weaker witness overrule the stronger one would report "newer build
available" for a board provably holding the newest build - and send someone to
reflash it mid-print.

The same reasoning puts the digest ahead of `device_dirty`, which is
`needs_flash: null`, "cannot be shown current". The digest shows it current.
The unrecoverable-tree worry behind `device_dirty` is real, but it is a fact
about the artifact and is already reported there as `built_dirty`; carrying it
on the device axis as well double-counts one doubt as two.

### Trackability owns identity durability, not registry uniqueness

Spec section 11, Ruling 13. A firmware helper's optional `Trackable` capability
judges one serial string: whether it is a durable identity and, if not, the
operator-facing reason and a machine-readable remedy. It performs no I/O and
receives no paths or registry. A helper without the capability makes every
serial trackable.

`tracking.add_serial` understands the `provision` remedy: when the same helper
also offers `Provisioner` and caller policy permits the irreversible write, it
provisions under the op lock and tracks what came back. A missing provisioner,
withheld write, or unknown remedy is a refusal carrying the helper's reason; an
unknown token is never silently treated as trackable. Registry uniqueness stays
in `tracking.py` because "already tracked under another type" is one shared fact
about the registry, not firmware knowledge. Helpers are not passed `Paths` or
taught to read the registry to answer trackability.

The refusal remains `UnprovisionedSerialError` with wire code
`roadrunner_unprovisioned`. That firmware-named code is a legacy contract a
panel may branch on; renaming it is a separate wire decision. Only the message
now comes from the helper. A held lock still refuses rather than waits: the
write is irreversible, and a caller queued behind a flash would perform it at
a moment nobody chose.

### The provisioning gate lives on the branch, not the method

`fw.serial.add` performs the same irreversible hardware write
`fw.roadrunner.provision` does whenever the type it is asked to track under
has an unprovisioned board and a family that can provision it - reached from
ordinary tracking rather than the dedicated maintenance call. Left alone,
that write would sit in the ungated `METHODS` table: a read-only agent, or
one with `enable_flashing` off, already cannot reach `fw.roadrunner.provision`
for exactly this reason, and would otherwise reach the identical write through
`fw.serial.add` regardless.

`fw.serial.add` is not moved into `HARDWARE_METHODS`, and is not itself
withheld: every other outcome it can produce - tracking a serial that needs no
provisioning, refusing one already tracked elsewhere - has nothing to do with
hardware and must keep working under any deployment. Instead
`tracking.add_serial` takes a `may_provision` keyword the caller answers for
itself, and the agent passes `_hardware_writes_allowed()` - the same
expression `available_methods` already uses to decide whether
`HARDWARE_METHODS` is advertised, shared rather than re-derived so the two
cannot drift. Withheld, the write refuses with the pre-Task-6 code,
`roadrunner_unprovisioned`, exactly as a family with no provisioner would -
not a new code for what is, from the caller's side, the same "I can't do
that here" answer. The CLI passes no such gate and provisions unconditionally
by default: `enable_flashing` is documented as an agent-only safety gate the
CLI has always ignored (`Settings.enable_flashing`), and grepping `cli.py`
confirms it never consults it anywhere today.

### One selection per identity, every builder

A fleet flash's selection is one list per kind of identity a type can declare -
a by-id `serials:` entry, a `canbus_uuids:` entry, a cmake type's `serials:` -
and the callers concatenate them. Adding a builder adds a selection beside the
others; it never adds a branch to a caller, and it never adds a refusal saying
this operation does not serve that builder. `type_not_bulk_flashable` and the
CLI's `-t`-only CMake refusal were both honest while the selection did not
exist, and both became the only remaining way to leave a board behind once it
did.

The verdict behind each selection is the verdict the panel shows.
`_cmake_devices` is the one judgement both read, which makes "the panel says
this board is behind" and "the fleet flash writes this board" the same claim.

Whether a host has any types at all is `providers.Install.empty`, not a list of
section maps at the call site. The list was `registry` and `platformio`, and it
told a Roadrunner-only host it had nothing configured.

### Identity is a helper capability, not a discovery source

`discovery`'s sources — `byid`, `dfu`, `bootsel`, the knomi listen pass, the
knomi watcher map — all answer the same question: *which of these sightings do
I trust?* They exist because a board can be seen twice, differently, and
something has to rank the answers. Every one of them is about an identity the
host can already read off the bus.

A KNOMI screen has no such identity. The CH340K in front of it reports no USB
serial at all, so `/dev/serial/by-id` has nothing to say and neither does
anything else the host can do on its own. The only stable name the screen has
is one its *firmware* knows and will state if asked — which makes "what is this
thing" a question about the firmware, not about the host, and therefore a
`helpers.Identifier` rather than a `discovery.Source`.

Both seams stay, and they compose: the handler's answer is a sighting like any
other, and `confirm()` still decides what to trust at write time.

The capability carries `ask` as a required keyword because the two sources cost
three orders of magnitude apart — reading `devices.json` versus opening every
free serial port for six seconds — and only the caller knows whether it has
stopped the services holding those ports. `fw.device.list` passes False and
takes the remembered answer or nothing; the CLI passes True from inside
`_ports_free` and takes the authoritative one. There is no default, so that
cost cannot be acquired by omission.

One consequence worth stating: `providers.pio` no longer re-exports
`read_device_map`, `discover` or `device_map_path`. A provider is handed its
configuration and builds from it; going looking for devices was never its job,
and the shim that made it look like it was is gone.

### One loop per operation, and handlers for everything else

Ten changes, one rule: a caller never branches on which firmware, which
builder or which flasher it is holding. The branch becomes a capability
somebody registered by hand.

What that looks like in practice. `fw.status` joins one inventory against one
type list and produces one verdict per device, and a firmware with an odd
version string answers through a `DeviceInfoReader` rather than being special-
cased in the join. `fw.flash_all` walks every provider's boards through one
selection, and a family that needs a particular tool says so in `flashers:`
rather than being routed by a name comparison. A board that has to be talked
into its bootloader first has a `BootselRequester`; a screen whose hardware
carries no name has an `Identifier`; a board that needs an identity written to
it before it can be tracked has a `Provisioner`. `update-all` is build-all then
flash-all, over the same lists, for every provider there is.

The cost is real and worth stating. There are now four capability Protocols and
a registry of helpers, where before there were `if` statements - more
indirection to read through, and a new firmware means writing a module and
adding two lines to two registries rather than one branch in one function.
That trade was taken because the branches did not stay in one function: the
same "is this a display?" question was being asked in `status.py`, `flash.py`,
`bulk.py` and `cli.py`, and the four answers drifted. A Roadrunner tracked in
the UI and invisible to the CLI was that drift, reported as a bug.

Configuration never chooses which Python module gets imported. Every helper is
named in `helpers.registry.HELPERS` and every flasher in the flashers registry,
by hand, because a helper can stop services and write firmware. A misspelt
`helper:` or `flashers:` refuses the config when it loads, naming the known
values - the one place in this design where the answer to a wrong name is a
refusal rather than a fallback.
