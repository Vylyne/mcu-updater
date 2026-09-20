"""One verdict, for every row shape.

`verdict.decide` replaced three functions that disagreed with each other:
`status._device_status` for kconfig boards, `providers.pio.device_status` for
screens, and a fixed `unknown_version` stub for CMake boards. The device-verdict
tests that lived in `tests/test_pio.py` and `tests/test_states.py` moved here
with them, because the whole point of the unification is that there is now one
place to assert against.

The rule every case here defends: **a verdict is never favourable on absent
evidence.** An offline board, an unreadable record, a tree that is not a
checkout - none of them are evidence that a board is current, and saying
otherwise leaves a toolhead running old firmware while the panel says it is
fine. The opposite error is just as real and has its own cases below: a wrong
"behind" sends someone to reflash a healthy board during a print.
"""

from __future__ import annotations

import pytest

from mcu_updater import uf2
from mcu_updater.device_info import SOURCE_KLIPPER, DeviceInfo
from mcu_updater.states import (
    ARTIFACT_CHANGED,
    DEVICE_DIRTY,
    IN_BOOTLOADER,
    OFFLINE,
    PROTOCOL_MISMATCH,
    SOURCE_CHANGED,
    TONE_ATTENTION,
    UNEXPECTED_IMAGE,
    UNKNOWN_VERSION,
    VERSION_ONLY,
    DeviceStatus,
)
from mcu_updater.verdict import STAMP_BUILT, STAMP_TAG, Evidence, Expected, decide

HEAD = "d7cea5bb1aca70849f28d0bb98ab1b96b9f6db65"

#: What `uf2.digest_fields` writes into a build sidecar, and what a board
#: reports back for the same image - the same four key names on both sides, on
#: purpose, so the comparison needs no translation. The numbers are the
#: provenance spec's golden vector.
IMAGE = {
    "digest_algorithm": uf2.DIGEST_CRC32_ISO_HDLC,
    "digest": 0xBBE38AA9,
    "image_start": 0x10000000,
    "image_length": 600,
}


def _reported(version: str | None = "v1.2.0-3-gdeadbee", **overrides) -> DeviceInfo:
    """A board's own report of the image it is running."""
    return DeviceInfo(source=SOURCE_KLIPPER, version=version, **{**IMAGE, **overrides})


# -- the strongest signals, in order ---------------------------------------


def test_a_protocol_mismatch_is_checked_before_anything_else():
    """The device saying it cannot talk to this host's module at all. No
    version comparison has a word for that, so it must not be folded in."""
    status = decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee", protocol_match=False),
        Expected(head="deadbee"),
    )
    assert status.reason == PROTOCOL_MISMATCH
    assert status.needs_flash is True


def test_a_silent_protocol_field_is_not_a_mismatch():
    """None is "it never said". Absence is never mismatch."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0", running_sha=None, protocol_match=None),
        Expected(stamp="v1.2.0"),
    ).reason is not PROTOCOL_MISMATCH


def test_an_offline_board_is_not_an_answer():
    status = decide(Evidence(state="offline", version="v1.2.0-3-gdeadbee"), Expected(head="deadbee"))
    assert status.reason == OFFLINE
    assert status.needs_flash is None


def test_a_board_in_its_bootloader_is_a_strong_yes():
    """It reports no application version at all, which is not "unknown": a
    board waiting in Katapult is the clearest possible signal it wants
    firmware."""
    status = decide(Evidence(state="katapult"), Expected(head=HEAD))
    assert status.reason == IN_BOOTLOADER
    assert status.needs_flash is True


def test_an_offline_board_outranks_a_matching_digest():
    status = decide(
        Evidence(state="offline", version="v1.2.0-3-gdeadbee", info=_reported()),
        Expected(digest=IMAGE),
    )
    assert status.reason == OFFLINE
    assert status.needs_flash is None


def test_a_bootloader_outranks_a_matching_digest():
    status = decide(
        Evidence(state="katapult", version="v1.2.0-3-gdeadbee", info=_reported()),
        Expected(digest=IMAGE),
    )
    assert status.reason == IN_BOOTLOADER
    assert status.needs_flash is True


def test_a_protocol_mismatch_outranks_offline():
    status = decide(
        Evidence(state="offline", protocol_match=False),
        Expected(),
    )
    assert status.reason == PROTOCOL_MISMATCH
    assert status.needs_flash is True


# -- the measurement -------------------------------------------------------


def test_a_digest_that_disagrees_is_an_unexpected_image():
    """The board measured itself and got a different number than the artifact
    we hold. We do not know what is on it, and flashing is what makes it
    known."""
    status = decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee", info=_reported(digest=0x12345678)),
        Expected(head="deadbee", digest=IMAGE),
    )
    assert status.reason == UNEXPECTED_IMAGE
    assert status.needs_flash is True


def test_a_reported_range_that_disagrees_is_a_mismatch_too():
    """The board reports the range and a host must not substitute its own, so
    a board digesting a different extent than the artifact covers is not
    running the artifact - whatever the number came out as."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee", info=_reported(image_length=512)),
        Expected(head="deadbee", digest=IMAGE),
    ).reason == UNEXPECTED_IMAGE


def test_a_digest_match_outranks_an_older_commit():
    """The measurement beats the claim. The board's version string says it is
    behind; the bytes say it is running exactly what is on disk, and a flash
    would write the same image back."""
    status = decide(
        Evidence(state="klipper", version="v0.9.0-1-gaaaaaaa", running_sha="aaaaaaa", info=_reported()),
        Expected(head="deadbee", digest=IMAGE),
    )
    assert status.reason is None
    assert status.needs_flash is False


def test_a_digest_match_with_an_empty_reported_version_is_up_to_date():
    """A board too old to stamp a version, or a module that reported none, is
    still a board we have measured. Falling through to `unknown_version` here
    would throw away the strongest evidence in the whole function."""
    status = decide(
        Evidence(state="klipper", version="", info=_reported(version="")),
        Expected(digest=IMAGE),
    )
    assert status.reason is None
    assert status.needs_flash is False


def test_a_digest_match_outranks_a_dirty_build():
    """`device_dirty` means "cannot be shown current". The digest shows it
    current. Which tree produced it is a fact about the artifact, and it is
    already carried there as `built_dirty`."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee-dirty", running_sha="deadbee", dirty=True, info=_reported()),
        Expected(head="deadbee", digest=IMAGE),
    ).reason is None


def test_a_digest_match_never_consults_the_flash_record():
    """The record is a weaker, older witness to the same fact. A record saying
    we last wrote a different binary cannot overrule a measurement of the one
    that is running now - and `artifact_changed` would send someone to reflash
    a board holding the newest image."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee", info=_reported()),
        Expected(
            head="deadbee",
            digest=IMAGE,
            artifact_sha="new" + "0" * 61,
            record={"bin_sha256": "old" + "0" * 61},
        ),
    ).reason is None


def test_an_artifact_we_could_not_describe_is_never_a_mismatch():
    """`uf2.digest_fields` returns {} for a file it cannot parse. A build whose
    artifact cannot be described still built, and the comparison falls through
    to the version exactly as it does for a board that reports no digest."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee", info=_reported()),
        Expected(head="deadbee", digest={}),
    ).reason is None


def test_a_half_described_artifact_is_absence_not_mismatch():
    """A sidecar written before the digest fields existed carries some of the
    keys and not others. Comparing None to a real number would be a permanent
    mismatch no flash could ever clear."""
    assert decide(
        Evidence(state="klipper", version="v0.9.0-1-gaaaaaaa", running_sha="aaaaaaa", info=_reported()),
        Expected(head="deadbee", digest={**IMAGE, "image_length": None}),
    ).reason == SOURCE_CHANGED


def test_a_board_that_reports_no_digest_falls_through_to_the_version():
    """Only the Roadrunner reports one today. Every other row must reach the
    version comparison untouched."""
    assert decide(
        Evidence(state="klipper", version="v0.9.0-1-gaaaaaaa", running_sha="aaaaaaa", info=None),
        Expected(head="deadbee", digest=IMAGE),
    ).reason == SOURCE_CHANGED


def test_a_digest_the_board_could_not_compute_is_absence():
    """Algorithm 0 is a current board saying "I cannot", which `has_digest`
    reports as no digest - distinct from a board too old for the fields."""
    assert decide(
        Evidence(state="klipper", version="v0.9.0-1-gaaaaaaa", running_sha="aaaaaaa", info=_reported(digest_algorithm=0)),
        Expected(head="deadbee", digest=IMAGE),
    ).reason == SOURCE_CHANGED


# -- absence ---------------------------------------------------------------


@pytest.mark.parametrize("version", [None, ""])
def test_a_device_that_said_nothing_is_unknown(version):
    """Both shapes: a module that omits the field and one that sends an empty
    string said the same thing. The kconfig original only tested `is None`, so
    an empty string took the sha path with no sha in it."""
    status = decide(Evidence(state="klipper", version=version), Expected(head=HEAD, stamp="v1.2.0"))
    assert status.reason == UNKNOWN_VERSION
    assert status.needs_flash is None


def test_a_screen_with_no_tree_to_compare_against_is_unknown():
    """The screens' rule, kept: no git checkout means no verdict at all, even
    though the tree's VERSION file would still give a stamp."""
    assert decide(
        Evidence(state="klipper", version="0.4.0"),
        Expected(head=None, stamp="0.4.0", stamp_kind=STAMP_TAG, tag_clean=True, require_head=True),
    ).reason == UNKNOWN_VERSION


def test_a_board_with_no_tree_falls_back_on_what_we_built():
    """A board's own row does not require a head: with no checkout to compare
    a commit against, our own build record still says which release we
    produced, and that is more than nothing."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee"),
        Expected(head=None, stamp="v1.2.0-3-gdeadbee", record={"bin_sha256": "aa" * 32}, artifact_sha="aa" * 32),
    ).reason is None


def test_a_commit_with_no_tree_and_nothing_built_is_unknown():
    """Today's answer for a kconfig board, unchanged: nothing on either side
    to compare."""
    assert decide(
        Evidence(state="klipper", version="v1.2.0-3-gdeadbee", running_sha="deadbee"),
        Expected(head=None, stamp=None),
    ).reason == UNKNOWN_VERSION


# -- a build from an uncommitted tree --------------------------------------


def test_a_dirty_build_is_unprovable_rather_than_behind():
    """The sha may well match head, but the working tree it was built from is
    not recoverable, so "current" is unprovable - and it is not evidence of
    being behind either, hence None rather than True."""
    status = decide(
        Evidence(state="klipper", version="0.4.0+3.gd34db33.dirty", running_sha="d34db33", dirty=True),
        Expected(head="d34db33", require_head=True),
    )
    assert status.reason == DEVICE_DIRTY
    assert status.needs_flash is None


def test_a_reader_that_never_calls_a_build_dirty_is_believed():
    """Klipper's reader always answers False, because a makefile-patched type
    is `-dirty` by construction - the patch is in place while klipper stamps
    its version. Those boards would want a flash no flash could satisfy."""
    assert decide(
        Evidence(state="klipper", version="v0.13.0-712-g6d43f8b3-dirty", running_sha="6d43f8b3", dirty=False),
        Expected(head="6d43f8b3ddbfab679d1a64cb6f9f7adbe851ee82"),
    ).reason is None


# -- the commit comparison -------------------------------------------------


def test_an_older_commit_is_source_changed():
    status = decide(
        Evidence(state="klipper", version="v0.13.0-623-gaea1bcf5", running_sha="aea1bcf5"),
        Expected(head=HEAD),
    )
    assert status.reason == SOURCE_CHANGED
    assert status.needs_flash is True


def test_a_matching_commit_with_no_record_is_taken_at_face_value():
    """The flash log only ever *adds* doubt. Degrading every board that
    predates the log to "unknown" would be noise, not caution."""
    assert decide(
        Evidence(state="klipper", version="v0.13.0-711-gd7cea5bb", running_sha="d7cea5bb"),
        Expected(head=HEAD, artifact_sha="aa" * 32, record=None),
    ).reason is None


def test_the_same_commit_with_a_different_binary_is_artifact_changed():
    """The case a version comparison structurally cannot see: edit the buffer
    patch, rebuild, and the boards still report the same klipper commit while
    holding last week's firmware."""
    assert decide(
        Evidence(state="klipper", version="v0.13.0-711-gd7cea5bb", running_sha="d7cea5bb"),
        Expected(head=HEAD, artifact_sha="new" + "0" * 61, record={"bin_sha256": "old" + "0" * 61}),
    ).reason == ARTIFACT_CHANGED


def test_a_record_with_no_binary_recorded_invents_no_mismatch():
    """A record written before `bin_sha256` existed. Absence is never
    mismatch, here as everywhere."""
    assert decide(
        Evidence(state="klipper", version="v0.13.0-711-gd7cea5bb", running_sha="d7cea5bb"),
        Expected(head=HEAD, artifact_sha="new" + "0" * 61, record={"bin_sha256": None}),
    ).reason is None


@pytest.mark.parametrize(
    ("running", "head"),
    [
        ("d34db33", "d34db3399aa"),
        ("d34db3399aa", "d34db33"),
        # Case is not dependable across the tools that produce these strings,
        # and the kconfig original compared with a case-sensitive startswith.
        ("D34DB33", "d34db3399aa"),
    ],
)
def test_short_shas_are_compared_on_the_shorter_of_the_two(running, head):
    """Different builds abbreviate to different lengths. Requiring one to be a
    prefix of the other in the recorded case was a mismatch that no flash
    could clear."""
    assert decide(Evidence(state="klipper", version=f"v1-1-g{running}", running_sha=running), Expected(head=head)).reason is None


def test_a_different_commit_of_the_same_length_still_differs():
    """The guard on the comparison above: shortening must not make everything
    match."""
    assert decide(
        Evidence(state="klipper", version="v1-1-gbadc0de", running_sha="badc0de"),
        Expected(head="d34db3399aa"),
    ).reason == SOURCE_CHANGED


# -- the stamp comparison, built ------------------------------------------


CARTO = "CARTOGRAPHER 6.2.0"


def test_a_stamped_version_with_nothing_built_is_unknown():
    """No built artifact to compare the stamp against, so there is nothing to
    say."""
    assert decide(Evidence(state="klipper", version=CARTO), Expected(head=HEAD, stamp=None)).reason == UNKNOWN_VERSION


def test_a_differing_stamp_is_source_changed():
    """CARTOGRAPHER 6.2.0 on the board, CARTOGRAPHER v4 6.2.0 out of the build
    - genuinely not our binary."""
    assert decide(
        Evidence(state="klipper", version=CARTO),
        Expected(head=HEAD, stamp="CARTOGRAPHER v4 6.2.0"),
    ).reason == SOURCE_CHANGED


def test_a_development_build_against_a_built_stamp_is_source_changed():
    """A Roadrunner built outside a tagged checkout stamps `dev`, which carries
    no commit. It is not unknown - we know what we built, and `dev` is not
    it."""
    assert decide(
        Evidence(state="klipper", version="dev"),
        Expected(stamp="v1.2.0-3-gdeadbee"),
    ).reason == SOURCE_CHANGED


def test_a_matching_stamp_with_no_record_is_version_only():
    """The honest amber: the release is recognised and the binary is not -
    distinct from `unknown_version`, which means nothing was recognised at
    all. A hand-maintained literal is identical in anyone's build of that
    release."""
    status = decide(Evidence(state="klipper", version=CARTO), Expected(head=HEAD, stamp=CARTO))
    assert status.reason == VERSION_ONLY
    assert status.needs_flash is None


def test_a_matching_stamp_backed_by_a_record_is_up_to_date():
    assert decide(
        Evidence(state="klipper", version=CARTO),
        Expected(head=HEAD, stamp=CARTO, artifact_sha="aa" * 32, record={"bin_sha256": "aa" * 32}),
    ).reason is None


def test_a_matching_stamp_with_a_stale_binary_is_artifact_changed():
    """Same release, different build - only the record can see it, exactly as
    on the commit path."""
    assert decide(
        Evidence(state="klipper", version=CARTO),
        Expected(head=HEAD, stamp=CARTO, artifact_sha="new" + "0" * 61, record={"bin_sha256": "old" + "0" * 61}),
    ).reason == ARTIFACT_CHANGED


def test_surrounding_whitespace_in_a_report_is_not_a_difference():
    assert decide(Evidence(state="klipper", version=f"  {CARTO}\n"), Expected(stamp=CARTO, record={})).reason is None


# -- the stamp comparison, tag --------------------------------------------


def test_a_release_build_on_a_clean_tag_is_current():
    """No sha at all means a clean build sitting exactly on the version tag. It
    is current only if the tree is still there - same version, still on the
    tag, still clean."""
    assert decide(
        Evidence(state="klipper", version="0.4.0"),
        Expected(head="d34db33", stamp="0.4.0", stamp_kind=STAMP_TAG, tag_clean=True, require_head=True),
    ).reason is None


def test_a_release_build_off_the_tag_is_source_changed():
    """The tree has moved past the tag the screen reports, so the same version
    string no longer means the same build."""
    assert decide(
        Evidence(state="klipper", version="0.4.0"),
        Expected(head="d34db33", stamp="0.4.0", stamp_kind=STAMP_TAG, tag_clean=False, require_head=True),
    ).reason == SOURCE_CHANGED


def test_a_release_build_of_another_version_is_source_changed():
    assert decide(
        Evidence(state="klipper", version="0.3.0"),
        Expected(head="d34db33", stamp="0.4.0", stamp_kind=STAMP_TAG, tag_clean=True, require_head=True),
    ).reason == SOURCE_CHANGED


def test_a_tree_with_no_version_of_its_own_says_nothing():
    assert decide(
        Evidence(state="klipper", version="0.4.0"),
        Expected(head="d34db33", stamp=None, stamp_kind=STAMP_TAG, require_head=True),
    ).reason == UNKNOWN_VERSION


def test_a_tag_match_needs_no_record_behind_it():
    """`version_only` is a board's word. It exists because a hand-maintained
    literal is identical in anyone's build of that release; a tag match is
    against a tree we can still see, so a record has nothing to add and a
    screen - which never has one - must not go amber for its absence."""
    assert decide(
        Evidence(state="klipper", version="0.4.0"),
        Expected(head="d34db33", stamp="0.4.0", stamp_kind=STAMP_TAG, tag_clean=True, require_head=True, record=None),
    ).reason is None


def test_the_built_stamp_is_the_default_kind():
    """Boards outnumber screens, and a caller that forgets the kind gets the
    comparison that consults our own build record rather than one that assumes
    a tree is sitting on a tag."""
    assert Expected().stamp_kind == STAMP_BUILT


# -- the new reason --------------------------------------------------------


def test_an_unexpected_image_asks_for_attention():
    """It has to be all three: `DEVICE_REASONS` derives from `_NEEDS_FLASH`, so
    a reason registered there alone constructs fine and raises on `.label`."""
    status = DeviceStatus(UNEXPECTED_IMAGE)
    assert status.needs_flash is True
    assert status.tone == TONE_ATTENTION
    assert status.label == "Unexpected firmware"


def test_the_default_verdict_is_still_nothing_to_do():
    """`decide` returns `DeviceStatus()` on every favourable path, and every
    one of them must land on the same object a caller tests with `is None`."""
    assert DeviceStatus().reason is None
    assert DeviceStatus().needs_flash is False
