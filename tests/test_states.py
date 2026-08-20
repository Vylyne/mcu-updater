"""The shared currency vocabulary that replaced four dialects.

Four vocabularies used to answer overlapping questions - ``stale_reason`` on the
MCU side, ``ART_*`` and ``FW_*`` on the display side, and ``flash_state``'s own
``reason``. They are collapsed into two questions here, one enum each: Q1 (the
built artifact) is ``ArtifactStatus``, Q2 (the device) is ``DeviceStatus``.

**Information was gained, not lost.** ``ART_FOREIGN`` used to mean four
different things spelled "unknown"; two of them are now distinguishable, and the
MCU and display sides finally agree about what a missing sidecar means. The old
wire words (``stale``/``stale_reason``, ``firmware_state``, ``artifact_state``)
were retired once this vocabulary existed to say the same things - see
docs/rebuild-plan.md's Step 14 log for that call.
"""

from __future__ import annotations

import json
import os

import pytest

from mcu_updater import build, states
from mcu_updater.providers import pio
from mcu_updater.providers.pio import PioType, SourceState
from mcu_updater.states import ArtifactStatus, DeviceStatus

TREE = SourceState(head="d34db33", version="0.4.0", dirty=False, on_tag=False)


# --------------------------------------------------------------------------
# the model itself
# --------------------------------------------------------------------------


def test_a_reason_determines_its_state_so_the_two_cannot_disagree():
    """The old code carried (stale, reason) as two independent values, kept in
    step only by every return statement remembering to."""
    assert ArtifactStatus(states.SOURCE_CHANGED).state == states.ARTIFACT_STALE
    assert ArtifactStatus(states.NEVER_BUILT).state == states.ARTIFACT_ABSENT
    assert ArtifactStatus().state == states.ARTIFACT_CURRENT


#: The whole vocabulary, spelled out. A loose assertion here - "the state is
#: one of the three" - let a mutation swapping True for None survive, so these
#: are pinned individually and exhaustively.
ARTIFACT_VERDICTS = {
    None: states.ARTIFACT_CURRENT,
    states.NEVER_BUILT: states.ARTIFACT_ABSENT,
    states.CONFIG_CHANGED: states.ARTIFACT_STALE,
    states.SOURCE_CHANGED: states.ARTIFACT_STALE,
    states.BUILT_DIRTY: states.ARTIFACT_UNPROVABLE,
    states.FOREIGN_BUILD: states.ARTIFACT_UNPROVABLE,
    states.NO_PROVENANCE: states.ARTIFACT_UNPROVABLE,
}

DEVICE_VERDICTS = {
    None: False,
    states.IN_BOOTLOADER: True,
    states.SOURCE_CHANGED: True,
    states.ARTIFACT_CHANGED: True,
    states.PROTOCOL_MISMATCH: True,
    states.DEVICE_DIRTY: None,
    states.OFFLINE: None,
    states.UNKNOWN_VERSION: None,
}


@pytest.mark.parametrize(("reason", "state"), sorted(ARTIFACT_VERDICTS.items(), key=str))
def test_each_artifact_reason_has_exactly_this_state(reason, state):
    assert ArtifactStatus(reason).state == state


def test_no_artifact_reason_is_left_unpinned():
    assert set(ARTIFACT_VERDICTS) == set(states.ARTIFACT_REASONS) | {None}


@pytest.mark.parametrize(("reason", "verdict"), sorted(DEVICE_VERDICTS.items(), key=str))
def test_each_device_reason_has_exactly_this_verdict(reason, verdict):
    assert DeviceStatus(reason).needs_flash is verdict


def test_no_device_reason_is_left_unpinned():
    assert set(DEVICE_VERDICTS) == set(states.DEVICE_REASONS) | {None}


def test_a_board_in_its_bootloader_definitely_wants_firmware():
    """Not "cannot tell". A board sitting in Katapult reports no application
    version at all, and degrading that to None would make every bulk flash -
    which filters on `needs_flash is True` - skip the boards most obviously
    waiting for firmware."""
    assert DeviceStatus(states.IN_BOOTLOADER).needs_flash is True


def test_only_a_reasonless_status_is_current():
    """`current` is a positive claim. Anything with a reason attached failed to
    prove itself, and must not read as up to date."""
    for reason in states.ARTIFACT_REASONS:
        assert not ArtifactStatus(reason).is_current


def test_needs_flash_is_never_false_on_absent_evidence():
    """The rule this whole area exists to enforce. An offline board or an
    unreadable version is not evidence that a board is current."""
    for reason in states.DEVICE_REASONS:
        assert DeviceStatus(reason).needs_flash is not False
    assert DeviceStatus().needs_flash is False


def test_only_an_absent_artifact_has_nothing_to_flash():
    assert not ArtifactStatus(states.NEVER_BUILT).can_flash
    for reason in (states.SOURCE_CHANGED, states.BUILT_DIRTY, states.FOREIGN_BUILD, None):
        assert ArtifactStatus(reason).can_flash


def test_an_unknown_reason_is_refused_rather_than_silently_carried():
    """A typo'd reason would otherwise produce a status that renders as neither
    current nor stale, and no test would notice."""
    with pytest.raises(ValueError, match="unknown artifact reason"):
        ArtifactStatus("probably_fine")
    with pytest.raises(ValueError, match="unknown device reason"):
        DeviceStatus("probably_fine")


def test_the_two_questions_share_the_one_reason_that_means_the_same_thing():
    """`source_changed` is the only overlap, and it is deliberate: the tree
    moved. The subject differs - an artifact behind it, or a device behind it."""
    shared = set(states.ARTIFACT_REASONS) & set(states.DEVICE_REASONS)
    assert shared == {states.SOURCE_CHANGED}


# --------------------------------------------------------------------------
# how a verdict reads
# --------------------------------------------------------------------------


@pytest.mark.parametrize("reason", (None,) + states.ARTIFACT_REASONS)
def test_every_artifact_reason_has_words_for_a_human(reason):
    """A reason with no label would KeyError in the panel. The precise codes
    exist for switching on, not for reading."""
    assert ArtifactStatus(reason).label


@pytest.mark.parametrize("reason", (None,) + states.DEVICE_REASONS)
def test_every_device_reason_has_words_for_a_human(reason):
    assert DeviceStatus(reason).label


@pytest.mark.parametrize("reason", states.ARTIFACT_REASONS + states.DEVICE_REASONS)
def test_no_label_is_just_the_reason_code_wearing_a_hat(reason):
    """`no_provenance` is precise and unreadable. Guards against someone
    "adding a label" by handing back the code with the underscores swapped."""
    labels = [
        ArtifactStatus(r).label for r in (None,) + states.ARTIFACT_REASONS
    ] + [DeviceStatus(r).label for r in (None,) + states.DEVICE_REASONS]
    for label in labels:
        assert "_" not in label
        assert label != reason
        assert label[0].isupper()


def test_up_to_date_is_the_only_green():
    assert ArtifactStatus().tone == states.TONE_OK
    assert DeviceStatus().tone == states.TONE_OK
    for reason in states.ARTIFACT_REASONS:
        assert ArtifactStatus(reason).tone != states.TONE_OK
    for reason in states.DEVICE_REASONS:
        assert DeviceStatus(reason).tone != states.TONE_OK


def test_nothing_we_cannot_vouch_for_is_painted_green():
    """The whole point of the amber bucket. An unverifiable image reading as
    up to date is how somebody ships a print on firmware from before the fix."""
    for reason in (states.BUILT_DIRTY, states.FOREIGN_BUILD, states.NO_PROVENANCE):
        assert ArtifactStatus(reason).tone == states.TONE_UNKNOWN
    for reason in (states.DEVICE_DIRTY, states.OFFLINE, states.UNKNOWN_VERSION):
        assert DeviceStatus(reason).tone == states.TONE_UNKNOWN


def test_a_missing_image_and_a_stale_one_read_the_same_because_the_fix_is_the_same():
    """They differ in cause and not at all in what the user does: press build."""
    assert (
        ArtifactStatus(states.NEVER_BUILT).tone
        == ArtifactStatus(states.SOURCE_CHANGED).tone
        == states.TONE_ATTENTION
    )


def test_a_device_tone_is_just_its_verdict_coloured():
    for reason in (None,) + states.DEVICE_REASONS:
        status = DeviceStatus(reason)
        expected = {False: states.TONE_OK, True: states.TONE_ATTENTION, None: states.TONE_UNKNOWN}
        assert status.tone == expected[status.needs_flash]


# --------------------------------------------------------------------------
# the display side, in the shared vocabulary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("running", "reason"),
    [
        ("0.4.0+3.gd34db33", None),
        ("0.4.0+1.gbadc0de", states.SOURCE_CHANGED),
        ("0.4.0+3.gd34db33.dirty", states.DEVICE_DIRTY),
        ("", states.UNKNOWN_VERSION),
    ],
)
def test_the_same_answers_in_the_shared_vocabulary(running, reason):
    assert pio.device_status(running, TREE).reason == reason


def test_a_dirty_screen_is_not_reported_as_wanting_a_flash():
    """It cannot be shown current, but it is not evidence of being behind
    either - which is what the old FW_DIRTY meant and must keep meaning."""
    assert pio.device_status("0.4.0+3.gd34db33.dirty", TREE).needs_flash is None


# --------------------------------------------------------------------------
# the split: what "unknown" used to hide
# --------------------------------------------------------------------------


@pytest.fixture
def display(tmp_path):
    source = tmp_path / "knomi_serial"
    (source / ".pio" / "build" / "knomi").mkdir(parents=True)
    return PioType(name="knomi", env="knomi", source=str(source))


def _bin(display, content=b"\x00firmware"):
    path = pio.firmware_bin(display)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def test_an_image_that_is_not_the_one_we_recorded_cannot_be_vouched_for(paths, display):
    """Knowing *that* an image changed says nothing about *what it now
    contains*, and only the second question matters before writing it to six
    screens. Attestation by another tool is what would earn a better answer."""
    _bin(display)
    pio.record_build(paths, display, TREE)
    _bin(display, b"\x00different and longer")

    assert pio.artifact_status(paths, display, TREE).reason == states.NO_PROVENANCE


def test_a_rebuild_producing_the_same_bytes_is_still_our_build(paths, display):
    """The false positive the content hash removes. Judging by mtime called a
    byte-identical rebuild somebody else's work - and the bytes are the only
    thing that reaches the screen."""
    _bin(display)
    pio.record_build(paths, display, TREE)

    path = pio.firmware_bin(display)
    os.utime(path, (os.stat(path).st_atime + 120, os.stat(path).st_mtime + 120))

    assert pio.artifact_status(paths, display, TREE).is_current


def test_an_untouched_image_is_judged_without_hashing_it(paths, display, monkeypatch):
    """The fast path, and it is the answer almost every time. Measured on the
    printer, hashing the 770 KiB knomi image costs 5.0 ms against 57 us for the
    stat that already answers it - and the stat happens regardless, so this
    costs nothing to keep."""
    _bin(display)
    pio.record_build(paths, display, TREE)

    def boom(_path):
        raise AssertionError("hashed an image that had not changed")

    monkeypatch.setattr(pio, "sha256_file", boom)
    assert pio.artifact_status(paths, display, TREE).is_current


def test_a_record_from_before_hashing_still_judges_by_size_and_mtime(paths, display):
    """An existing install keeps its verdict until its next build, instead of
    being told once that everything it has is suddenly unverifiable."""
    _bin(display)
    pio.record_build(paths, display, TREE)

    sidecar = paths.display_sidecar(display.env)
    with open(sidecar, encoding="utf-8") as fh:
        record = json.load(fh)
    del record["bin_sha256"]
    with open(sidecar, "w", encoding="utf-8") as fh:
        json.dump(record, fh)

    assert pio.artifact_status(paths, display, TREE).is_current


def test_foreign_build_is_reserved_and_nothing_claims_it_yet(paths, display):
    """It means "another tool vouches for this image" - PlatformIO knows whether
    .pio/build is current against its own dependency graph. That costs a
    subprocess, and this runs on the fw.status poll path, so it belongs behind
    an explicit request. Until then nothing may produce it."""
    scenarios = []

    _bin(display)
    scenarios.append(pio.artifact_status(paths, display, TREE))

    pio.record_build(paths, display, TREE)
    scenarios.append(pio.artifact_status(paths, display, TREE))

    _bin(display, b"\x00different")
    scenarios.append(pio.artifact_status(paths, display, TREE))

    scenarios.append(pio.artifact_status(paths, display, SourceState()))

    assert states.FOREIGN_BUILD not in [s.reason for s in scenarios]


def test_no_record_at_all_is_the_other_kind_of_unknown(paths, display):
    _bin(display)
    assert pio.artifact_status(paths, display, TREE).reason == states.NO_PROVENANCE


def test_a_corrupt_record_is_absence_of_evidence_not_evidence_of_a_rebuild(paths, display):
    _bin(display)
    sidecar = paths.display_sidecar(display.env)
    os.makedirs(os.path.dirname(sidecar), exist_ok=True)
    with open(sidecar, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert pio.artifact_status(paths, display, TREE).reason == states.NO_PROVENANCE


def test_the_two_unknowns_are_genuinely_different_statuses(paths, display):
    """The whole point of the split - two reasons that used to render as one
    word, ``unknown``, are now distinguishable in the model itself."""
    assert states.FOREIGN_BUILD != states.NO_PROVENANCE


# --------------------------------------------------------------------------
# the payoff: both sides now say the same thing about the same situation
# --------------------------------------------------------------------------


def _mcu_artifact(paths, mcu_type="bttebb36", fw="klipper", sidecar=None):
    binary = paths.bin_file(mcu_type, fw)
    os.makedirs(os.path.dirname(binary), exist_ok=True)
    with open(binary, "wb") as fh:
        fh.write(b"\x00firmware")
    if sidecar is not None:
        with open(paths.sidecar_file(mcu_type, fw), "w", encoding="utf-8") as fh:
            json.dump(sidecar, fh)
    return binary


def test_a_binary_with_no_record_means_the_same_on_both_sides(paths, display):
    """An MCU binary with no sidecar used to report "never_built" on the wire;
    a display binary with no sidecar used to report "unknown". Same situation,
    opposite legacy words - the model agrees now that both retired."""
    _mcu_artifact(paths)
    _bin(display)

    assert build.artifact_status(paths, "bttebb36", "klipper").reason == states.NO_PROVENANCE
    assert pio.artifact_status(paths, display, TREE).reason == states.NO_PROVENANCE


def test_a_genuinely_absent_mcu_artifact_is_still_never_built(paths):
    assert build.artifact_status(paths, "bttebb36", "klipper").reason == states.NEVER_BUILT


def test_an_unprovable_mcu_artifact_reports_stale_rather_than_current(paths):
    """The only safe collapse of a four-state answer into a boolean."""
    _mcu_artifact(paths)
    assert not build.artifact_status(paths, "bttebb36", "klipper").is_current


def test_a_matching_mcu_artifact_is_current(paths, monkeypatch):
    monkeypatch.setattr(build, "git_head", lambda _: "abc1234")
    monkeypatch.setattr(build, "sha256_file", lambda _: "cfghash")
    _mcu_artifact(paths, sidecar={"fw_sha": "abc1234", "config_sha256": "cfghash"})

    assert build.artifact_status(paths, "bttebb36", "klipper").is_current


@pytest.mark.parametrize(
    ("sidecar", "expected"),
    [
        ({"fw_sha": "abc1234", "config_sha256": "moved"}, states.CONFIG_CHANGED),
        ({"fw_sha": "moved", "config_sha256": "cfghash"}, states.SOURCE_CHANGED),
    ],
)
def test_the_mcu_reasons_survive_verbatim(paths, monkeypatch, sidecar, expected):
    monkeypatch.setattr(build, "git_head", lambda _: "abc1234")
    monkeypatch.setattr(build, "sha256_file", lambda _: "cfghash")
    _mcu_artifact(paths, sidecar=sidecar)

    assert build.artifact_status(paths, "bttebb36", "klipper").reason == expected
