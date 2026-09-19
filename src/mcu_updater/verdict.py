"""One verdict for every device row: `decide(evidence, expected)`.

Three functions used to answer "does this device want firmware, and why?" and
they disagreed: `agent.methods.status._device_status` for kconfig boards,
`providers.pio.device_status` for screens, and a fixed `unknown_version` stub
for CMake boards - which is why a Roadrunner's panel row never said anything at
all. They are one function now, pure over its two inputs so that every row
shape is testable without hardware.

The rules are the provenance spec's
(`docs/superpowers/specs/2026-09-11-cmake-provenance-design.md`), unchanged:

- **Klipper first.** What the device says it is running outranks what we infer
  from a file on disk.
- **Absence is never mismatch.** No version, no head, no record, no digest are
  all "cannot tell", never "behind". A wrong "behind" sends someone to reflash
  a healthy board during a print; a wrong "current" leaves a toolhead on old
  firmware while the panel says it is fine. Neither is acceptable, and every
  step below picks the answer that claims the least.
- **A measurement outranks a claim.** A reported digest is the running bytes;
  a version string is a story about them. When both sides have measured the
  same image, nothing weaker gets to overturn the result - not the version,
  not our own flash record, not a dirty tree.
- **The board's reported range is authoritative.** Start and length are
  compared field to field with the artifact's, and this host never substitutes
  its own.

`Evidence` is everything the device said. `Expected` is everything we hold for
it. Nothing in here opens a port, reads a file or runs git.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from .device_info import DeviceInfo

# The definition site rather than `devices`, which re-exports the BOOTSEL, DFU
# and USB scanners too - none of which this module has any business importing.
from .discovery.byid import STATE_KATAPULT, STATE_OFFLINE
from .states import (
    ARTIFACT_CHANGED,
    DEVICE_DIRTY,
    IN_BOOTLOADER,
    OFFLINE,
    PROTOCOL_MISMATCH,
    SOURCE_CHANGED,
    UNEXPECTED_IMAGE,
    UNKNOWN_VERSION,
    VERSION_ONLY,
    DeviceStatus,
)

#: The stamp is a version string our own build wrote down, so a match means "we
#: built what it reports" and nothing weaker. Cartographer's `CONFIG_VERSION`
#: and the Roadrunner's repo-wide `git describe` both land here.
STAMP_BUILT = "built"
#: The stamp is the source tree's own version, so a match only means anything
#: while the tree is still sitting on that tag, clean. The screens' rule.
STAMP_TAG = "tag"

#: The four INFO fields that make a digest comparable. Named once: the board's
#: report, the build sidecar and `uf2.digest_fields` all spell them this way on
#: purpose, so the comparison needs no translation in between.
_DIGEST_FIELDS = ("digest_algorithm", "digest", "image_start", "image_length")


@dataclasses.dataclass(frozen=True)
class Evidence:
    """What the device itself said. Nothing here was read off a disk."""

    #: A `discovery.byid` state - "klipper", "katapult", "offline". None where
    #: the caller has no liveness answer to offer, which is not the same as
    #: offline and must not read as one.
    state: str | None = None
    #: Exactly what the device reported, unparsed.
    version: str | None = None
    #: The commit inside `version` per the family's own reader, or None when
    #: the version carries none - normal for a release build, and permanent
    #: for a tree that stamps a hand-maintained literal.
    running_sha: str | None = None
    #: The device reports a build from an uncommitted tree. Per-family: a
    #: makefile-patched klipper is `-dirty` by construction, and its reader
    #: says False so those boards do not ask forever for a flash that cannot
    #: satisfy them.
    dirty: bool = False
    #: False only when the device says it cannot talk to this host's module.
    #: None is "it never said", which is not a mismatch.
    protocol_match: bool | None = None
    #: The image the board measured of itself, where it can measure one.
    info: DeviceInfo | None = None


@dataclasses.dataclass(frozen=True)
class Expected:
    """What we hold for it: the tree, the artifact, and our own ledger."""

    #: The source tree commit a running commit is compared against. None
    #: disables the comparison rather than failing it - see `decide`.
    head: str | None = None
    #: The version string to compare when there is no commit to compare.
    stamp: str | None = None
    stamp_kind: str = STAMP_BUILT
    #: STAMP_TAG only: the tree is still on the tag it stamped, and clean.
    tag_clean: bool = False
    #: No tree at all is "cannot tell" for this caller, whatever else it has.
    #: The screens' rule, and theirs alone.
    require_head: bool = False
    #: `bin_sha256` of the artifact on disk now.
    artifact_sha: str | None = None
    #: Our own `FlashLog` entry for this device - already discarded by
    #: `entry_for` if it disagrees with what the device reports running, so
    #: anything that arrives here is a record we still believe.
    record: Mapping[str, Any] | None = None
    #: The artifact's own digest fields, exactly as `uf2.digest_fields` writes
    #: them into a build sidecar. A caller passes the whole sidecar.
    digest: Mapping[str, Any] | None = None


def decide(evidence: Evidence, expected: Expected) -> DeviceStatus:
    """Does this device want firmware, and why?

    Ordered strongest evidence first. Every step that can only say "cannot
    tell" says so, rather than falling through to a favourable answer.
    """
    if evidence.protocol_match is False:
        # The device saying it cannot talk to this host's module at all. No
        # version comparison has a word for that, so it is asked first rather
        # than folded in.
        return DeviceStatus(PROTOCOL_MISMATCH)
    if evidence.state == STATE_OFFLINE:
        return DeviceStatus(OFFLINE)
    if evidence.state == STATE_KATAPULT:
        # Sitting in its bootloader, so it reports no application version at
        # all. Not "unknown" - the clearest possible signal that it wants
        # firmware.
        return DeviceStatus(IN_BOOTLOADER)

    measured = _image_verdict(evidence.info, expected.digest)
    if measured is not None:
        return measured

    if not evidence.version:
        # Empty string as well as None: a device that answered with nothing
        # said nothing, whichever shape its module used to say it.
        return DeviceStatus(UNKNOWN_VERSION)
    if expected.require_head and not expected.head:
        return DeviceStatus(UNKNOWN_VERSION)
    if evidence.dirty:
        # Built from uncommitted changes. The commit may well match head, but
        # the working tree it was built from is not recoverable, so "current"
        # is unprovable rather than merely unknown - and it is not evidence of
        # being behind either, hence a None verdict rather than True.
        return DeviceStatus(DEVICE_DIRTY)

    if evidence.running_sha and expected.head:
        # Short shas differ in length between builds and in case between the
        # tools that print them; compare over the shorter of the two.
        running = evidence.running_sha.lower()
        head = expected.head.lower()
        size = min(len(running), len(head))
        if running[:size] != head[:size]:
            return DeviceStatus(SOURCE_CHANGED)
        # The commit matches, so only our own record can tell two builds of it
        # apart. Used to *add* doubt and never to remove it: with no record the
        # commit match stands, rather than degrading every board to unknown.
        return _record_verdict(expected, absent_is=None)

    # A commit with no tree to compare it against falls through rather than
    # bailing out: our own build record still says which release we produced,
    # and that is more than nothing. It reaches `unknown_version` below anyway
    # when there is no stamp either, which is the old answer.
    if not expected.stamp:
        return DeviceStatus(UNKNOWN_VERSION)

    if expected.stamp_kind == STAMP_TAG:
        # The tree's own version, not ours. It means "current" only while the
        # tree is still on that tag with nothing uncommitted - the same release
        # built from a moved tree is a different build, and there is no record
        # on this path to tell them apart.
        if evidence.version.strip() == expected.stamp and expected.tag_clean:
            return DeviceStatus()
        return DeviceStatus(SOURCE_CHANGED)

    if evidence.version.strip() != expected.stamp:
        return DeviceStatus(SOURCE_CHANGED)
    # The stamp matches. Unlike the commit path there is no commit match to
    # stand on when the record is absent: a hand-maintained literal is
    # identical in anyone's build of that release, so here the absence of a
    # record is the difference between green and amber rather than a no-op.
    return _record_verdict(expected, absent_is=VERSION_ONLY)


def _image_verdict(
    info: DeviceInfo | None, expected: Mapping[str, Any] | None
) -> DeviceStatus | None:
    """The measurement, when both sides have one. None means "no evidence".

    Decisive in both directions, which is the point of measuring. A match is
    the running bytes against the bytes on disk, so nothing weaker overturns
    it - not the version string, not our own ledger, not a dirty tree. Our
    ledger in particular is a *record of what we last wrote*, which is the same
    question one step less directly and one flash out of date; asking it to
    overrule a measurement would report "newer build available" for a board
    provably holding the newest build.
    """
    if info is None or not info.has_digest() or not expected:
        return None
    ours = tuple(expected.get(field) for field in _DIGEST_FIELDS)
    if None in ours:
        # A sidecar from before these fields existed, or an artifact that could
        # not be parsed. Absence is never mismatch - comparing None to a real
        # number would be a permanent mismatch no flash could clear.
        return None
    theirs = tuple(getattr(info, field) for field in _DIGEST_FIELDS)
    if theirs != ours:
        return DeviceStatus(UNEXPECTED_IMAGE)
    return DeviceStatus()


def _record_verdict(expected: Expected, *, absent_is: str | None) -> DeviceStatus:
    """What our own flash record adds once the version already agrees.

    `absent_is` is what a missing record means on this path, and the two paths
    differ: a commit match stands on its own, a bare release string does not.
    """
    if expected.record is None:
        return DeviceStatus(absent_is)
    flashed = expected.record.get("bin_sha256")
    if flashed and expected.artifact_sha and flashed != expected.artifact_sha:
        # Same commit, different binary: an edited makefile patch or a changed
        # .config builds differently from an identical tree, and only our own
        # record knows which build the board actually got.
        return DeviceStatus(ARTIFACT_CHANGED)
    return DeviceStatus()


__all__ = ["STAMP_BUILT", "STAMP_TAG", "Evidence", "Expected", "decide"]
