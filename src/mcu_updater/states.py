"""One vocabulary for the two questions this tool actually answers.

Four vocabularies grew up saying overlapping things about firmware currency:
``build.staleness()`` returned ``stale_reason``, ``displays.artifact_state()``
returned ``ART_*``, ``displays.firmware_state()`` returned ``FW_*``, and
``Api.flash_state()`` returned its own ``reason``. Two of them disagreed about
what "unknown" meant, and a fifth was about to arrive with cartographer.

They are not four questions. They are two, asked about different subjects:

**Q1 - is the built image current with respect to its inputs?**
    A ``.config`` hash, a source-tree commit, a sidecar written at build time.
    Answered by :class:`ArtifactStatus`. This is a question about a *file we
    produced*.

**Q2 - is the device running that image?**
    A version string reported over USB, a bus state, a flash record. Answered by
    :class:`DeviceStatus`. This is a question about *hardware*.

The MCU side had both and named them differently; the display side had both and
named them differently again. Nothing here is a new concept - it is the two that
were already there, spelled once.

**The reason is the fact; everything else is a view of it.** ``state`` and
``needs_flash`` are derived from ``reason`` rather than stored alongside it, so
an inconsistent pair - "current, because the source changed" - cannot be
constructed at all. That mattered: the old code carried ``(stale, reason)`` as
two independent values, and the only thing keeping them in step was that every
return statement remembered to.

Legacy strings are *not* produced here. Each module adapts its own public
surface, because the wire format and the constants are that module's contract,
not this one's.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

# --------------------------------------------------------------------------
# how a verdict is meant to read
# --------------------------------------------------------------------------

#: Three buckets, and the traffic light a panel would paint them:
#:
#:   TONE_OK        green   - provably fine, nothing to do
#:   TONE_UNKNOWN   amber   - we cannot vouch for this either way
#:   TONE_ATTENTION red     - something needs doing
#:
#: Named semantically rather than "green"/"amber"/"red" for one reason: colour
#: is not the only way this gets rendered, and it must not be the only way it is
#: understood. A chip, an icon and a screen reader all need the `label`; the
#: colour is one presentation of the tone, not the tone itself.
TONE_OK = "ok"
TONE_UNKNOWN = "unknown"
TONE_ATTENTION = "attention"

# --------------------------------------------------------------------------
# Q1: the built image, against the inputs that produced it
# --------------------------------------------------------------------------

#: Provably matches its inputs.
ARTIFACT_CURRENT = "current"
#: Provably does not.
ARTIFACT_STALE = "stale"
#: Nothing on disk to flash.
ARTIFACT_ABSENT = "absent"
#: An image exists, but "current" cannot be *shown*. Distinct from stale: a
#: rebuild resolves it, whereas a stale image is known to be wrong.
ARTIFACT_UNPROVABLE = "unprovable"

#: No image on disk, or no record that we ever built one.
NEVER_BUILT = "never_built"
#: The saved .config hash moved since the build.
CONFIG_CHANGED = "config_changed"
#: The source tree's HEAD moved since the build. Also used for Q2, where it
#: means the same thing about a different subject - the device is behind the
#: tree rather than the artifact being behind it.
SOURCE_CHANGED = "source_changed"
#: Built from a tree with uncommitted changes. The tree it came from is not
#: recoverable, so "current" is unprovable rather than merely unknown - and a
#: rebuild from the same working copy would not settle it either.
BUILT_DIRTY = "built_dirty"
#: We have provenance, and the file on disk no longer matches it. Positive
#: evidence that somebody else rebuilt behind us.
FOREIGN_BUILD = "foreign_build"
#: No provenance to check: no sidecar, an unreadable one, or not a git checkout.
#: Absence of evidence, which is a different thing to say than FOREIGN_BUILD and
#: wants different words in the UI.
NO_PROVENANCE = "no_provenance"

_ARTIFACT_STATE: dict[Optional[str], str] = {
    None: ARTIFACT_CURRENT,
    NEVER_BUILT: ARTIFACT_ABSENT,
    CONFIG_CHANGED: ARTIFACT_STALE,
    SOURCE_CHANGED: ARTIFACT_STALE,
    BUILT_DIRTY: ARTIFACT_UNPROVABLE,
    FOREIGN_BUILD: ARTIFACT_UNPROVABLE,
    NO_PROVENANCE: ARTIFACT_UNPROVABLE,
}

ARTIFACT_REASONS = tuple(r for r in _ARTIFACT_STATE if r is not None)

_ARTIFACT_TONE: dict[str, str] = {
    ARTIFACT_CURRENT: TONE_OK,
    # Absent and stale are one bucket on purpose. They differ in cause and not
    # at all in what the user does about it: press build.
    ARTIFACT_ABSENT: TONE_ATTENTION,
    ARTIFACT_STALE: TONE_ATTENTION,
    ARTIFACT_UNPROVABLE: TONE_UNKNOWN,
}

#: Plain words, because the reason codes are for the panel to switch on and are
#: not fit to show anybody. "no_provenance" is precise and unreadable; the point
#: of keeping the precise code was never to make a human read it.
#:
#: These live here rather than in the panel so that one wording serves every
#: front end - the CLI, the Mainsail component, and whatever renders a
#: cartographer probe later - instead of three that drift.
_ARTIFACT_LABEL: dict[Optional[str], str] = {
    None: "Up to date",
    NEVER_BUILT: "Never built",
    CONFIG_CHANGED: "Config changed - rebuild",
    SOURCE_CHANGED: "Source updated - rebuild",
    BUILT_DIRTY: "Built from unsaved changes",
    FOREIGN_BUILD: "Rebuilt outside this tool",
    NO_PROVENANCE: "Unverified build",
}


@dataclasses.dataclass(frozen=True)
class ArtifactStatus:
    """Whether a built image still matches what produced it."""

    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.reason not in _ARTIFACT_STATE:
            raise ValueError(
                f"unknown artifact reason {self.reason!r}; "
                f"expected None or one of {', '.join(ARTIFACT_REASONS)}"
            )

    @property
    def state(self) -> str:
        return _ARTIFACT_STATE[self.reason]

    @property
    def tone(self) -> str:
        return _ARTIFACT_TONE[self.state]

    @property
    def label(self) -> str:
        return _ARTIFACT_LABEL[self.reason]

    @property
    def is_current(self) -> bool:
        return self.reason is None

    @property
    def can_flash(self) -> bool:
        """Is there an image on disk at all? Staleness is the caller's call."""
        return self.state != ARTIFACT_ABSENT


# --------------------------------------------------------------------------
# Q2: the device, against the image we hold for it
# --------------------------------------------------------------------------

#: Sitting in Katapult, so it reports no application version at all. Not
#: "unknown" - a board waiting in its bootloader is the strongest possible
#: signal that it wants firmware.
IN_BOOTLOADER = "in_bootloader"
#: Same commit, different binary. The one a version comparison structurally
#: cannot see: an edited makefile-patch source or a changed .config produces a
#: different build from an identical commit. Only our own flash record knows.
ARTIFACT_CHANGED = "artifact_changed"
#: The device speaks a protocol version this host does not expect.
PROTOCOL_MISMATCH = "protocol_mismatch"
#: The device reports a build from an uncommitted tree. Cannot be shown current,
#: but is not evidence it is behind either - hence None rather than True.
DEVICE_DIRTY = "device_dirty"
#: Not on the bus. Never evidence of being up to date.
OFFLINE = "offline"
#: Reachable, but said nothing we can compare - no version, or no tree to
#: compare against.
UNKNOWN_VERSION = "unknown_version"

_NEEDS_FLASH: dict[Optional[str], Optional[bool]] = {
    None: False,
    IN_BOOTLOADER: True,
    SOURCE_CHANGED: True,
    ARTIFACT_CHANGED: True,
    PROTOCOL_MISMATCH: True,
    DEVICE_DIRTY: None,
    OFFLINE: None,
    UNKNOWN_VERSION: None,
}

DEVICE_REASONS = tuple(r for r in _NEEDS_FLASH if r is not None)

#: The tri-state answer, coloured. Nothing else to decide: "wants flashing" is
#: the action, "cannot tell" is the caveat, "up to date" is the all-clear.
_DEVICE_TONE: dict[Optional[bool], str] = {
    False: TONE_OK,
    True: TONE_ATTENTION,
    None: TONE_UNKNOWN,
}

_DEVICE_LABEL: dict[Optional[str], str] = {
    None: "Up to date",
    IN_BOOTLOADER: "Waiting in bootloader",
    SOURCE_CHANGED: "Update available",
    ARTIFACT_CHANGED: "Newer build available",
    PROTOCOL_MISMATCH: "Firmware too old for this host",
    DEVICE_DIRTY: "Unverified build",
    OFFLINE: "Not connected",
    UNKNOWN_VERSION: "Version unknown",
}


@dataclasses.dataclass(frozen=True)
class DeviceStatus:
    """Whether a physical device wants flashing, and why."""

    reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.reason not in _NEEDS_FLASH:
            raise ValueError(
                f"unknown device reason {self.reason!r}; "
                f"expected None or one of {', '.join(DEVICE_REASONS)}"
            )

    @property
    def tone(self) -> str:
        return _DEVICE_TONE[self.needs_flash]

    @property
    def label(self) -> str:
        return _DEVICE_LABEL[self.reason]

    @property
    def needs_flash(self) -> Optional[bool]:
        """True, False, or None for "cannot tell".

        Never False on absent evidence. An offline board or an unreachable
        Klippy is not evidence that a board is current, and saying otherwise is
        the bug this whole area exists to fix.
        """
        return _NEEDS_FLASH[self.reason]

    @property
    def is_known(self) -> bool:
        return self.needs_flash is not None
