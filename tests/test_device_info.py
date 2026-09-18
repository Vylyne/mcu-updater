"""What a board is running, read through the handler its firmware names.

Before this, `status.py` owned one sha regex for boards, `pio.py` another for
screens, and a Roadrunner's `git describe` went through whichever the caller
happened to use. Each firmware now answers through its family's helper, and a
family with no reader goes through the built-in Klipper one - so the question
"which regex applies here?" has one place to be answered.
"""

from __future__ import annotations

import pytest

from mcu_updater import device_info, firmware, helpers
from mcu_updater.agent.methods import Api
from mcu_updater.config import Registry
from mcu_updater.device_info import KLIPPER, DeviceInfo
from mcu_updater.helpers.cartographer import CartographerHelper
from mcu_updater.helpers.knomi_serial import KnomiSerialHelper
from mcu_updater.helpers.roadrunner import RoadrunnerHelper
from mcu_updater.providers import pio

from .conftest import make_device

HEAD = "d7cea5bb1aca70849f28d0bb98ab1b96b9f6db65"
OLD_VERSION = "v0.13.0-623-gaea1bcf5"


class _StubReader:
    """A reader that claims every board runs HEAD.

    Wiring tests use it because its answer differs from the Klipper reader's
    on OLD_VERSION: a caller that dropped the family's reader and fell back to
    KLIPPER turns "current" into "source_changed", which a test can see.
    """

    name = "stub"
    klipper_prefix = "stub_screen"

    def running_sha(self, version):
        return HEAD[:8]

    def is_dirty(self, version):
        return False


# -- DeviceInfo ------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"digest_algorithm": 1, "digest": 5, "image_start": 0, "image_length": 600}, True),
        # DIGEST_NONE is 0: a board that says "no algorithm" reported no digest.
        ({"digest_algorithm": 0, "digest": 5, "image_start": 0, "image_length": 600}, False),
        ({"digest_algorithm": None, "digest": 5, "image_start": 0, "image_length": 600}, False),
        ({"digest_algorithm": 1, "digest": None, "image_start": 0, "image_length": 600}, False),
        ({"digest_algorithm": 1, "digest": 5, "image_start": None, "image_length": 600}, False),
        ({"digest_algorithm": 1, "digest": 5, "image_start": 0, "image_length": None}, False),
    ],
)
def test_a_digest_is_only_comparable_with_its_algorithm_and_range(fields, expected):
    """A digest with no range cannot be checked against a file, so it is not one.

    `image_start` of 0 is a real start, which is why the check is `is not None`.
    """
    assert DeviceInfo(source="klipper", version="v1", **fields).has_digest() is expected


# -- the built-in Klipper reader -------------------------------------------


@pytest.mark.parametrize(
    ("version", "sha"),
    [
        ("v0.13.0-711-gd7cea5bb", "d7cea5bb"),
        # A makefile-patched build is always -dirty, so that must not defeat the
        # match or those types would report needing a flash forever.
        ("v0.13.0-712-g6d43f8b3-dirty", "6d43f8b3"),
        ("v0.12.0", None),
        ("unknown", None),
        ("", None),
        (None, None),
    ],
)
def test_the_klipper_reader_extracts_the_commit_from_a_git_describe(version, sha):
    assert KLIPPER.running_sha(version) == sha


def test_the_klipper_reader_never_calls_a_board_dirty():
    """`-dirty` is normal for a makefile-patched Klipper; it is not evidence."""
    assert KLIPPER.is_dirty("v0.13.0-712-g6d43f8b3-dirty") is False


# -- helper readers --------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "sha"),
    [
        ("v1.2.0-3-gdeadbee", "deadbee"),
        ("v1.2.0-3-gdeadbee-dirty", "deadbee"),
        # `git describe --always` in a repo with no tags is the bare sha.
        ("deadbee", "deadbee"),
        ("deadbee-dirty", "deadbee"),
        # Sitting exactly on a tag: no commit in the string.
        ("v1.2.0", None),
        # A development build that stamped no describe at all.
        ("dev", None),
        ("", None),
        (None, None),
    ],
)
def test_the_roadrunner_reader_reads_every_describe_form(version, sha):
    assert RoadrunnerHelper().running_sha(version) == sha


@pytest.mark.parametrize(
    ("version", "dirty"),
    [
        ("v1.2.0-3-gdeadbee-dirty", True),
        ("deadbee-dirty", True),
        ("v1.2.0-3-gdeadbee", False),
        ("dev", False),
        (None, False),
    ],
)
def test_the_roadrunner_reader_knows_a_dirty_build(version, dirty):
    assert RoadrunnerHelper().is_dirty(version) is dirty


def test_the_knomi_reader_is_the_platformio_one():
    """Two callers read a screen's version; they must not disagree."""
    reader = KnomiSerialHelper()
    assert reader.running_sha("0.4.0+3.gd34db33") == pio.running_sha("0.4.0+3.gd34db33")
    assert reader.running_sha("0.4.0+3.gd34db33") == "d34db33"
    assert reader.is_dirty("0.4.0+3.gd34db33.dirty") is True
    assert reader.is_dirty("0.4.0+3.gd34db33") is False


def test_the_cartographer_reader_never_invents_a_sha():
    """docs/decisions.md: "Do not synthesize a sha into Cartographer's
    `CONFIG_VERSION`". Even a string that looks like a describe is not one."""
    reader = CartographerHelper()
    assert reader.running_sha("v0.13.0-711-gd7cea5bb") is None
    assert reader.running_sha("CARTOGRAPHER 6.2.0") is None
    assert reader.klipper_prefix == "mcu"


# -- choosing the reader ---------------------------------------------------


def test_no_family_reads_through_klipper():
    assert device_info.reader_for(None) is KLIPPER


def test_a_family_with_no_helper_reads_through_klipper():
    assert device_info.reader_for(firmware.FirmwareFamily(name="klipper")) is KLIPPER


@pytest.mark.parametrize(
    ("helper", "kind"),
    [
        ("knomi_serial", KnomiSerialHelper),
        ("roadrunner", RoadrunnerHelper),
        ("cartographer", CartographerHelper),
    ],
)
def test_a_family_reads_through_its_helper(helper, kind):
    family = firmware.FirmwareFamily(name="x", helper=helper)
    assert isinstance(device_info.reader_for(family), kind)


def test_a_helper_without_a_reader_reads_through_klipper(monkeypatch):
    """A firmware without a capability is an ordinary firmware (spec §6)."""

    class _NameOnly:
        name = "name_only"

    monkeypatch.setattr(helpers, "for_name", lambda name, *, family: _NameOnly())
    family = firmware.FirmwareFamily(name="x", helper="name_only")

    assert device_info.reader_for(family) is KLIPPER


def test_the_capability_accessors_answer_none_for_what_a_helper_lacks():
    assert helpers.device_info_reader(None) is None
    assert helpers.image_reporter(None) is None
    assert helpers.image_reporter(KnomiSerialHelper()) is None
    assert helpers.image_reporter(CartographerHelper()) is None
    roadrunner = RoadrunnerHelper()
    assert helpers.device_info_reader(roadrunner) is roadrunner
    assert helpers.image_reporter(roadrunner) is roadrunner


# -- callers use the family's reader ---------------------------------------


def test_a_screen_types_klipper_section_comes_from_its_reader(paths, monkeypatch):
    """The prefix was a hardcoded default on `PioType`; it is the helper's now."""
    from .conftest import with_base_firmwares, write_main_config

    write_main_config(
        paths,
        with_base_firmwares(
            "[firmware knomi]\nsource: ~/knomi\nbuilder: platformio\nflashers: esptool\n"
            "helper: knomi_serial\n\n"
            "[type screen]\nfirmware: knomi\nplatformio_env: knomi\n"
        ),
    )
    monkeypatch.setattr(device_info, "reader_for", lambda family: _StubReader())

    assert pio.load(paths)["screen"].klipper_section == "stub_screen"


def test_flash_state_extracts_the_sha_with_the_reader_it_is_given(paths):
    api = Api(paths)
    info = {"A": {"version": OLD_VERSION, "mcu": "mcu"}}

    default = api.flash_state("A", info, HEAD, state="klipper")
    stubbed = api.flash_state("A", info, HEAD, state="klipper", reader=_StubReader())

    assert default["reason"] == "source_changed"
    assert stubbed["running_sha"] == HEAD[:8]
    assert stubbed["needs_flash"] is False


def test_type_status_reads_each_board_through_its_familys_reader(
    paths, live_registry_text, fake_root, monkeypatch
):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    make_device(fake_root / "bus", "Klipper", "stm32g0b1xx", "123456789012345678901")
    from mcu_updater import build as build_mod

    monkeypatch.setattr(build_mod, "git_head", lambda _d, **_kw: HEAD)
    monkeypatch.setattr(device_info, "reader_for", lambda family: _StubReader())
    api = Api(paths)
    versions = {"123456789012345678901": {"version": OLD_VERSION, "mcu": "mcu EBBT0"}}

    ebb = api.type_status(api.registry(), "bttebb36", versions)

    by_serial = {s["serial"]: s for s in ebb["serials"]}
    assert by_serial["123456789012345678901"]["needs_flash"] is False


def test_a_bulk_flash_selects_through_each_familys_reader(
    paths, live_registry_text, fake_root, monkeypatch
):
    from .test_agent_bulk import _moonraker, _stage_artifact, monkey_head

    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    _stage_artifact(paths, "bttebb36")
    make_device(fake_root / "bus", "Klipper", "stm32g0b1xx", "123456789012345678901")
    api = Api(paths, call=_moonraker({"123456789012345678901": OLD_VERSION}))
    monkey_head(api, paths)
    monkeypatch.setattr(device_info, "reader_for", lambda family: _StubReader())

    assert api._boards_to_flash(Registry.load(paths), "stale") == []
