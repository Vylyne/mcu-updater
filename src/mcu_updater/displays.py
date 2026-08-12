"""ESP32 displays: PlatformIO builds, esptool uploads.

Different enough from an MCU to live apart. There is no Kconfig, no Katapult, no
chipset to reason about - a PlatformIO env already names the board, the partition
table and the build flags, so the env *is* the type. Adding the second display
is another `[display <env>]` section and nothing structural.

The device list is not here either: `[knomi_serial T0_knomi]` in Klipper's config
already names how to find its port - directly with `serial:`, or by chip identity
with `device_id:`, in which case Klipper's own discovery resolves it and reports
the result back through `get_status()`. Either way, a second copy here would only
be something to disagree with.

**Nothing here ever lets PlatformIO choose a port.** Its auto-detect picks one
device arbitrarily when several match, and every display on this printer is an
indistinguishable CH340 - so an upload without an explicit port writes firmware
to whichever one answered first. See `upload()`.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import threading
import time
from typing import Optional

from .build import Reporter, null_reporter, run_streamed, sha256_file
from .cfgdoc import CfgDocument
from .errors import BuildError, ConfigError, FlashError, SourceTreeMissingError, ToolMissingError
from .paths import Paths
from .settings import Settings
from .states import (
    BUILT_DIRTY,
    CONFIG_CHANGED,
    DEVICE_DIRTY,
    FOREIGN_BUILD,
    NEVER_BUILT,
    NO_PROVENANCE,
    SOURCE_CHANGED,
    UNKNOWN_VERSION,
    ArtifactStatus,
    DeviceStatus,
)

SECTION_PREFIX = "display"

#: Where PlatformIO puts itself. `pio` on PATH first, because that is what a
#: user's own symlinks give; the venv path is the fallback for a service whose
#: PATH is systemd's rather than a login shell's.
PIO_CANDIDATES = (
    "pio",
    os.path.expanduser("~/.platformio/penv/bin/pio"),
    "/usr/local/bin/pio",
)

#: From esptool's own banner, e.g. `MAC: cc:ba:97:19:aa:38`. The one piece of
#: durable identity a display has: it is in efuse, so it survives reflashing, and
#: the CH340 in front of it has no serial of its own to offer.
_MAC_RE = re.compile(r"^MAC:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\s*$", re.MULTILINE)

#: `Chip is ESP32-S3 (QFN56) (revision v0.2)`
_CHIP_RE = re.compile(r"^Chip is (.+?)\s*$", re.MULTILINE)

#: PlatformIO giving up inside WaitForNewSerialPort. The board manifest for a
#: native-USB ESP32-S3 tells it to reset the board and then adopt whatever *new*
#: serial port appears. A display wired through a CH340 keeps the same port -
#: the CH340 is a separate always-powered chip and never leaves the bus - so no
#: new port ever appears and it times out on a perfectly healthy screen.
#:
#: Matched so the failure can explain itself. It cannot be fixed from here:
#: board_upload.* is settable only in platformio.ini, and `pio run` has no
#: option to override it.
_WAITING_FOR_PORT_RE = re.compile(r"Couldn't find a board on the selected port", re.I)


@dataclasses.dataclass
class DisplayType:
    """One PlatformIO env, and where its devices are declared in printer.cfg."""

    name: str
    env: str = ""
    source: str = ""
    #: The Klipper section prefix whose entries are displays of this type.
    #: `[knomi_serial T0_knomi]` -> `knomi_serial`. A second display with its own
    #: klippy module would set this differently; one sharing the module leaves it.
    klipper_section: str = "knomi_serial"

    def __post_init__(self) -> None:
        # The env is the type, so the section name is the env unless overridden.
        if not self.env:
            self.env = self.name

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "env": self.env,
            "source": self.source,
            "klipper_section": self.klipper_section,
        }


def load(paths: Paths, default_source: str = "") -> dict[str, DisplayType]:
    """Read `[display <name>]` sections from the shared config file."""
    try:
        with open(paths.main_config, encoding="utf-8") as fh:
            doc = CfgDocument(fh.read())
    except OSError:
        return {}

    out: dict[str, DisplayType] = {}
    for section in doc.section_names(SECTION_PREFIX):
        name = section[len(SECTION_PREFIX) :].strip()
        if not name:
            continue
        out[name] = DisplayType(
            name=name,
            env=(doc.get(section, "env") or "").strip(),
            source=(doc.get(section, "source") or default_source).strip(),
            klipper_section=(doc.get(section, "klipper_section") or "knomi_serial").strip(),
        )
    return out


def find_pio(settings: Settings) -> str:
    """The PlatformIO launcher, or a clear error naming what to install."""
    configured = settings.platformio_bin
    candidates = ([configured] if configured else []) + list(PIO_CANDIDATES)
    for candidate in candidates:
        found = shutil.which(candidate) if os.path.basename(candidate) == candidate else candidate
        if found and os.path.exists(found):
            return found
    raise ToolMissingError(
        "PlatformIO not found. Install it, or symlink its launcher onto PATH: "
        "~/.platformio/penv/bin/pio",
        tool="pio",
    )


def _source_dir(display: DisplayType) -> str:
    path = os.path.expanduser(display.source)
    if not path:
        raise ConfigError(
            f"display '{display.name}' has no source tree configured. Set 'source:' "
            f"in its [display] section, or 'display_source' in [updater].",
            type=display.name,
        )
    if not os.path.isdir(path):
        raise SourceTreeMissingError(
            f"source directory {path} not found for display '{display.name}'.",
            fw=display.env,
            path=path,
        )
    return path


# --------------------------------------------------------------------------
# is the screen running the current source tree
# --------------------------------------------------------------------------

#: The git short sha inside a reported firmware version. knomi-serial's
#: `scripts/version.py` appends semver build metadata to the VERSION file:
#:
#:     0.4.0                    clean tree sitting exactly on tag v0.4.0
#:     0.4.0+3.gd34db33         three commits past the tag
#:     0.4.0+3.gd34db33.dirty   ...with uncommitted changes
#:     0.4.0+gd34db33           the tag does not exist yet
#:
#: Only the sha is read back out, deliberately. Reimplementing that whole string
#: here would be a second copy of somebody else's rule, and it would drift the
#: first time they changed it - whereas a commit id either matches HEAD or does
#: not, and that question survives any change to how the string is assembled.
_FW_SHA_RE = re.compile(r"\+(?:\d+\.)?g([0-9a-f]{6,40})", re.IGNORECASE)

#: A build from a tree with uncommitted changes. Never reproducible, so it can
#: never be *shown* to match - saying "up to date" about one would be a lie.
_FW_DIRTY_RE = re.compile(r"\.dirty\b", re.IGNORECASE)

#: How stale a screen's firmware is relative to the source tree.
FW_CURRENT = "current"
FW_BEHIND = "behind"
FW_DIRTY = "dirty"
FW_UNKNOWN = "unknown"


@dataclasses.dataclass(frozen=True)
class SourceState:
    """What the display source tree would build right now."""

    head: Optional[str] = None
    version: Optional[str] = None
    dirty: bool = False
    #: HEAD is exactly the `v<VERSION>` tag, which is the one case the firmware
    #: reports a bare version with no sha to compare against.
    on_tag: bool = False


def _git(directory: str, *args: str) -> Optional[str]:
    import subprocess

    try:
        out = subprocess.check_output(
            ("git",) + args, cwd=directory, stderr=subprocess.DEVNULL, timeout=10
        )
    except Exception:  # noqa: BLE001 - not a git checkout, no git, or a timeout
        return None
    return out.decode("utf-8", "replace").strip()


def source_state(source: str) -> SourceState:
    """Read the display source tree's identity. Everything optional."""
    path = os.path.expanduser(source or "")
    if not path or not os.path.isdir(path):
        return SourceState()

    head = _git(path, "rev-parse", "--short", "HEAD") or None
    if head is None:
        return SourceState()

    version = None
    try:
        with open(os.path.join(path, "VERSION"), encoding="utf-8") as fh:
            version = fh.read().strip() or None
    except OSError:
        version = None

    dirty = bool(_git(path, "status", "--porcelain"))
    on_tag = False
    if version:
        behind = _git(path, "rev-list", "--count", f"v{version}..HEAD")
        on_tag = behind == "0"

    return SourceState(head=head, version=version, dirty=dirty, on_tag=on_tag)


def device_status(running: Optional[str], state: SourceState) -> DeviceStatus:
    """Compare what a screen reports running against what the tree would build.

    Stronger than the artifact check, which compares a built artifact against
    its source. This compares what is *actually on the device*, so a screen
    flashed by hand months ago cannot report itself up to date.

    Verdicts are withheld generously. Every input here is optional - no git
    checkout, no VERSION file, a module too old to report a version - and a
    wrong "behind" sends someone to reflash a healthy display during a print.
    """
    if not running or state.head is None:
        return DeviceStatus(UNKNOWN_VERSION)

    if _FW_DIRTY_RE.search(running):
        # Built from uncommitted changes. The sha may well match HEAD, but the
        # working tree it was built from is not recoverable, so "current" is
        # unprovable rather than merely unknown - and it is not evidence of
        # being behind either, hence a None verdict rather than True.
        return DeviceStatus(DEVICE_DIRTY)

    match = _FW_SHA_RE.search(running)
    if match:
        # Short shas can differ in length between builds; compare on the shorter.
        built, head = match.group(1).lower(), state.head.lower()
        size = min(len(built), len(head))
        return DeviceStatus() if built[:size] == head[:size] else DeviceStatus(SOURCE_CHANGED)

    # No sha at all means a clean build sitting exactly on the version tag. It
    # is current only if the tree is still there - same version, still on the
    # tag, still clean.
    if state.version and running.strip() == state.version and state.on_tag and not state.dirty:
        return DeviceStatus()
    if state.version and state.on_tag and not state.dirty:
        # A release build of a different version than the tree holds.
        return DeviceStatus(SOURCE_CHANGED)
    return DeviceStatus(SOURCE_CHANGED if state.version else UNKNOWN_VERSION)


#: DeviceStatus reason -> the FW_* word this module has always reported.
#:
#: Deliberately *not* total, unlike the artifact adapter. FW_* has four words
#: and no honest one for `in_bootloader`, `offline` or `artifact_changed` - a
#: screen has no bus state to be in a bootloader, and no flash record to
#: contradict. Inventing a word for a state this module cannot reach would be
#: worse than the KeyError, which would at least be true.
_LEGACY_FW_STATE = {
    None: FW_CURRENT,
    SOURCE_CHANGED: FW_BEHIND,
    DEVICE_DIRTY: FW_DIRTY,
    UNKNOWN_VERSION: FW_UNKNOWN,
}


def firmware_state(running: Optional[str], state: SourceState) -> str:
    """`device_status()` in the FW_* words. See that function for the reasoning."""
    return _LEGACY_FW_STATE[device_status(running, state).reason]


# --------------------------------------------------------------------------
# is the BUILT IMAGE current
#
# Separate from firmware_state, which asks about the screens. This asks about
# the .bin, and it earns its place because `fw.display.flash` uploads whatever
# is in .pio/build without building first - so a source tree that has moved
# since the last build writes old firmware to every screen, silently.
# --------------------------------------------------------------------------

ART_CURRENT = "current"
ART_STALE = "source_changed"
ART_NEVER = "never_built"
ART_DIRTY = "dirty"
#: An image exists that we did not build, so there is no provenance for it.
ART_FOREIGN = "unknown"


def record_build(paths: Paths, display: DisplayType, state: SourceState) -> None:
    """Note which commit produced the image now sitting in .pio/build.

    Records a hash of the binary itself, which is what makes "is this still our
    build?" answerable. Size and mtime are kept too, but only to read records
    written before the hash existed - judging by them alone was too weak in both
    directions. A rebuild producing a byte-identical image moves the mtime and
    would have read as somebody else's work; and two different images can share
    a size.

    Without this, claiming "up to date" about a binary we know nothing about is
    the failure - it flashes every screen of a type with firmware from before
    the fix you just made.
    """
    path = firmware_bin(display)
    try:
        stat = os.stat(path)
    except OSError:
        return

    record = {
        "sha": state.head,
        "version": state.version,
        "dirty": state.dirty,
        "at": time.time(),
        "bin_sha256": sha256_file(path),
        "bin_size": stat.st_size,
        "bin_mtime": stat.st_mtime,
    }
    sidecar = paths.display_sidecar(display.env)
    os.makedirs(os.path.dirname(sidecar), exist_ok=True)
    tmp = sidecar + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
    os.replace(tmp, sidecar)


def _is_our_image(record: dict, path: str, stat: os.stat_result) -> bool:
    """Are the bytes on disk the bytes we recorded?

    Two tiers, because this runs on the `fw.status` poll path:

    **Size and mtime are the fast path.** Untouched since we wrote it means
    ours, for the cost of a stat. This is the answer almost every time.

    **The content hash is the fallback**, and only runs when something looks
    changed - which is rare, and is exactly when the question is worth paying
    for. It exists because mtime alone was wrong in both directions: a rebuild
    producing a byte-identical image moves the mtime and would have been called
    somebody else's work, and two genuinely different images can share a size.
    The bytes are the only thing that reaches the screen, so the bytes decide.

    Measured on a BTT Pi 2 running from eMMC: a 770 KiB knomi image hashes in
    5.0 ms at 159 MB/s, against 57 us for the stat. So the gate saves about
    5 ms per poll, which is not much - and on slower storage or a larger image
    it is more. It is kept mainly because the stat happens regardless (we need
    it to know the file exists at all), which makes the fast path free rather
    than merely cheap.

    A record written before the hash existed has only the fast path, so a
    changed file reads as not-ours - the old behaviour exactly. It self-heals
    on the next build.
    """
    if record.get("bin_size") == stat.st_size and record.get("bin_mtime") == stat.st_mtime:
        return True

    recorded = record.get("bin_sha256")
    if not recorded:
        return False
    return sha256_file(path) == recorded


def artifact_status(paths: Paths, display: DisplayType, state: SourceState) -> ArtifactStatus:
    """Does the built image match the source tree?

    Never a guess when the provenance cannot be trusted - no sidecar, a binary
    someone else rebuilt, or no git checkout to compare against. The cost of a
    wrong "current" here is flashing six screens with firmware from before the
    fix you just made.

    The bar for `current` is that the bytes on disk are the bytes we recorded.
    Anything else is `no_provenance` - not because nothing happened, but because
    knowing *that* an image changed says nothing about *what it now contains*,
    and only the second question matters before flashing six screens with it.

    `foreign_build` is reserved for an image some *other* tool can vouch for -
    PlatformIO knows whether .pio/build is current against its own dependency
    graph, and `make -q` answers the same for klipper. Nothing here produces it
    yet: that check costs a subprocess, and this function is on the `fw.status`
    poll path, so attestation belongs behind an explicit request rather than
    being paid for every few seconds.
    """
    path = firmware_bin(display)
    try:
        stat = os.stat(path)
    except OSError:
        return ArtifactStatus(NEVER_BUILT)

    try:
        with open(paths.display_sidecar(display.env), encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError):
        return ArtifactStatus(NO_PROVENANCE)
    if not isinstance(record, dict):
        return ArtifactStatus(NO_PROVENANCE)

    if not _is_our_image(record, path, stat):
        return ArtifactStatus(NO_PROVENANCE)

    if record.get("dirty"):
        # Same reasoning as a dirty firmware: the tree it came from is gone.
        return ArtifactStatus(BUILT_DIRTY)

    built, head = record.get("sha"), state.head
    if not built or not head:
        return ArtifactStatus(NO_PROVENANCE)
    size = min(len(built), len(head))
    if built[:size].lower() == head[:size].lower():
        return ArtifactStatus()
    return ArtifactStatus(SOURCE_CHANGED)


#: ArtifactStatus reason -> the ART_* word this module has always reported.
#: Both untrustworthy reasons collapse back to a single "unknown" here; the
#: distinction is available from `artifact_status()` for anything that wants it.
#:
#: Total over every artifact reason, including `config_changed`, which a
#: PlatformIO env cannot currently produce - it has no saved .config to compare.
#: Kept anyway: the cost is one line, and the alternative is a KeyError in front
#: of a user the day that stops being true.
_LEGACY_ART_STATE = {
    None: ART_CURRENT,
    NEVER_BUILT: ART_NEVER,
    CONFIG_CHANGED: ART_STALE,
    SOURCE_CHANGED: ART_STALE,
    BUILT_DIRTY: ART_DIRTY,
    FOREIGN_BUILD: ART_FOREIGN,
    NO_PROVENANCE: ART_FOREIGN,
}


def artifact_state(paths: Paths, display: DisplayType, state: SourceState) -> str:
    """`artifact_status()` in the ART_* words. See that function for the reasoning."""
    return _LEGACY_ART_STATE[artifact_status(paths, display, state).reason]


def resolve_port(port: str) -> str:
    """Follow a udev symlink to the device PlatformIO can actually see.

    `pio device list` enumerates through pyserial, which reports real devices -
    `/dev/ttyUSB0` - and never the `/dev/knomi_t0` symlink pointing at one. Hand
    PlatformIO the symlink and it looks for a board on a port that is not in its
    list, which is why an upload to a perfectly healthy display failed with
    "Couldn't find a board on the selected port".

    Resolved here, at the moment of the write, rather than in the config: the
    stable name is the whole reason the udev rule exists, and `/dev/ttyUSB0`
    depends on plug order. Klipper is stopped by the time this runs, so nothing
    is re-enumerating in the gap between resolving and writing.

    A broken or absent symlink resolves to itself; PlatformIO then reports a
    missing port, which is a better message than anything invented here.
    """
    try:
        return os.path.realpath(port)
    except OSError:
        return port


def firmware_bin(display: DisplayType) -> str:
    """Where PlatformIO leaves the image for this env."""
    return os.path.join(
        os.path.expanduser(display.source), ".pio", "build", display.env, "firmware.bin"
    )


def build(
    paths: Paths,
    settings: Settings,
    display: DisplayType,
    *,
    reporter: Reporter = null_reporter,
    cancel: Optional[threading.Event] = None,
) -> str:
    """Compile one env. Returns the path to the image it produced."""
    source = _source_dir(display)
    pio = find_pio(settings)

    reporter("info", f"Building {display.env} in {source}...")
    rc = run_streamed(
        [pio, "run", "-e", display.env],
        cwd=source,
        reporter=reporter,
        cancel=cancel,
        dry_run=settings.dry_run,
    )
    if rc != 0:
        raise BuildError(
            f"PlatformIO build failed for display '{display.name}': pio exited {rc}.",
            type=display.name,
            fw=display.env,
            returncode=rc,
        )

    # After the build, so it describes the image that now exists. Read here
    # rather than passed in: this is the commit the binary was actually built
    # from, and taking it from before the build would be a different question.
    if not settings.dry_run:
        record_build(paths, display, source_state(source))
    return firmware_bin(display)


def upload(
    paths: Paths,
    settings: Settings,
    display: DisplayType,
    port: str,
    *,
    reporter: Reporter = null_reporter,
    cancel: Optional[threading.Event] = None,
) -> dict[str, Optional[str]]:
    """Write this env's firmware to the display at `port`.

    **`port` is required and is never inferred.** PlatformIO auto-detects an
    upload port when none is given, and with several identical CH340s attached it
    picks whichever it finds first - observed doing exactly that on this printer,
    choosing between two displays with no way for the user to know which. An
    upload that guesses its target writes firmware to the wrong screen.

    esptool's ROM handshake is what verifies the target: it refuses to write to
    anything that is not an ESP32, so the check is inherent rather than a step
    that could be skipped. Its banner also carries the MAC, which is the only
    durable identity a display has - returned here so a caller can record it.
    """
    if not port:
        raise FlashError(
            "refusing to upload without an explicit port: PlatformIO would pick a "
            "device on its own, and every display here is an identical CH340.",
            type=display.name,
        )

    source = _source_dir(display)
    pio = find_pio(settings)

    transcript: list[str] = []

    def capture(stream: str, line: str) -> None:
        transcript.append(line)
        reporter(stream, line)

    target = resolve_port(port)
    reporter("info", f"Uploading {display.env} to {port}...")
    if target != port:
        # Say which real device is about to be written. The stable name is what
        # the config and the MAC record use; this is the only place the two are
        # visibly tied together.
        reporter("info", f"{port} -> {target}")

    rc = run_streamed(
        [
            pio,
            "run",
            "-e",
            display.env,
            "-t",
            "upload",
            "--upload-port",
            target,
            # Nothing else goes here. `pio run` takes no --project-option - that
            # belongs to `pio ci` and `pio project init` - and an invalid flag
            # makes pio exit before it touches the board, wasting a whole
            # Klipper stop/start cycle. board_upload.* can only be set in
            # platformio.ini; see _WAITING_FOR_PORT_RE for the failure that
            # causes and the message that explains it.
        ],
        cwd=source,
        reporter=capture,
        cancel=cancel,
        dry_run=settings.dry_run,
    )

    text = "\n".join(transcript)
    mac = _MAC_RE.search(text)
    chip = _CHIP_RE.search(text)

    if rc != 0:
        if _WAITING_FOR_PORT_RE.search(text):
            raise FlashError(
                f"upload failed for display '{display.name}' on {port}: PlatformIO reset "
                f"the board and then waited for a *new* serial port to appear. One never "
                f"will - this display talks through a CH340, which stays on the bus and "
                f"keeps the same port.\n"
                f"Add this to the [env:{display.env}] section of "
                f"{os.path.join(source, 'platformio.ini')}:\n"
                f"    board_upload.wait_for_upload_port = no\n"
                f"It has to go there: board_upload.* is a platformio.ini setting and "
                f"'pio run' has no command-line option for it.",
                type=display.name,
                port=port,
                returncode=rc,
                remedy="board_upload.wait_for_upload_port = no",
            )
        raise FlashError(
            f"upload failed for display '{display.name}' on {port}: pio exited {rc}.",
            type=display.name,
            port=port,
            returncode=rc,
        )
    return {
        "port": port,
        "mac": mac.group(1).lower() if mac else None,
        "chip": chip.group(1) if chip else None,
    }


# --------------------------------------------------------------------------
# remembering which display sat on which port
# --------------------------------------------------------------------------


def read_macs(paths: Paths) -> dict[str, dict]:
    """port -> {mac, env, at}. Empty when the file is missing or unreadable."""
    try:
        with open(paths.display_macs_file, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_mac(paths: Paths, port: str, mac: Optional[str], env: str) -> Optional[str]:
    """Note the display seen on `port`, and report a MAC that has changed.

    Returns the *previous* MAC when it differs - which is the swap signal. A
    tophat board plugged into the other socket moves every display on it at once,
    and this is the only thing that would notice.

    Absent MAC writes nothing: esptool did not report one, and overwriting a good
    record with a blank would destroy the very history this exists for.
    """
    if not mac:
        return None
    data = read_macs(paths)
    previous = (data.get(port) or {}).get("mac")
    now = time.time()
    entry: dict = {"mac": mac, "env": env, "at": now}
    if previous and previous != mac:
        # Kept in the record, not just returned, so the panel can still say so
        # tomorrow. The swap is only ever detectable during a flash - esptool
        # needs the port, which needs Klipper stopped - so if it were not
        # persisted here the one moment it is knowable would be the only moment
        # it could be shown. Absent on the next flash that finds the same MAC,
        # which is what clears the warning.
        entry["moved_from"] = previous
        entry["moved_at"] = now
    data[port] = entry

    os.makedirs(os.path.dirname(paths.display_macs_file), exist_ok=True)
    tmp = paths.display_macs_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, paths.display_macs_file)

    return previous if previous and previous != mac else None
