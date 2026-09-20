"""Which flasher writes a device is its family's answer.

The old global selector matched a chipset and state against every registered
flasher in registry order, so an RP2040 had one reachable flasher no matter
what it was running. Each `[firmware]` section now lists the flashers that may
write it, in order, and the first whose `supports()` accepts the device wins. A
family that lists nothing able to write a device refuses it by name instead of
guessing.
"""

from __future__ import annotations

import pytest

from mcu_updater import flashers
from mcu_updater.devices import (
    STATE_BOOTSEL,
    STATE_DFU,
    STATE_KATAPULT,
    STATE_KLIPPER,
    STATE_OFFLINE,
)
from mcu_updater.errors import ConfigCorruptError, NoFlasherError
from mcu_updater.firmware import FirmwareFamily
from mcu_updater.flashers import (
    KIND_BARE,
    KIND_CANBUS,
    KIND_SCREEN,
    KIND_SERIAL,
    Device,
)
from mcu_updater.helpers import BootselHandoff
from mcu_updater.settings import Settings

RR_SERIAL = "RR-0123456789ABCDEFGHJKMNPQRS"


class _Requester:
    """A helper that can put its board into BOOTSEL."""

    name = "requester"

    def request_bootsel(self, bench, *, serial, chipset, ctx):
        return BootselHandoff(topology="platform-x.usb-usb-0:1.3:1.0")

    def wait_ready(self, bench, *, serial, chipset, ctx):
        return None


class _Plain:
    """A helper with no BOOTSEL capability."""

    name = "plain"


def _family(*names: str, name: str = "fam") -> FirmwareFamily:
    return FirmwareFamily(name=name, flashers=tuple(names))


def _device(
    *,
    chipset: str = "stm32f446xx",
    state: str = STATE_KLIPPER,
    kind: str = KIND_SERIAL,
    type: str = "board",
    id: str = "usb-Klipper_stm32f446xx_29000-if00",
    detail: dict | None = None,
) -> Device:
    return Device(
        type=type,
        id=id,
        chipset=chipset,
        state=state,
        fw="fam",
        kind=kind,
        detail=detail or {},
    )


def _roadrunner(uf2: str = "/tmp/rr.uf2", *, chipset: str = "rp2040") -> Device:
    return _device(
        chipset=chipset,
        state="roadrunner",
        type="roadrunner",
        id=RR_SERIAL,
        detail={"uf2_file": uf2},
    )


def _bare_rp2040(uf2: str = "/tmp/katapult.uf2") -> Device:
    return _device(
        chipset="rp2040",
        state=STATE_BOOTSEL,
        kind=KIND_BARE,
        type="rp2040",
        id="",
        detail={"uf2_file": uf2},
    )


# --- supports ----------------------------------------------------------------


@pytest.mark.parametrize(
    "device, expected",
    [
        (_device(state=STATE_KLIPPER), True),
        (_device(state=STATE_KATAPULT), True),
        # An absent board is the write's device_not_found, not a selection
        # failure: that error names the fix.
        (_device(state=STATE_OFFLINE), True),
        (_device(chipset="rp2040"), True),
        (_device(chipset="lpc1769"), True),
        (_device(kind=KIND_CANBUS, state="unknown", id="bcb5346fc731"), True),
        (_device(chipset="esp32"), False),
        (_device(kind=KIND_SCREEN, chipset="esp32"), False),
        (_device(kind=KIND_BARE, state=STATE_DFU), False),
    ],
)
def test_flashtool_writes_serial_and_can_boards(device, expected):
    assert flashers.Flashtool().supports(device, None) is expected


@pytest.mark.parametrize(
    "device, expected",
    [
        (_device(kind=KIND_SCREEN, chipset="esp32", state="unknown"), True),
        (_device(kind=KIND_SCREEN, chipset="", state="unknown"), True),
        (_device(chipset="esp32"), False),
        (_device(), False),
    ],
)
def test_esptool_writes_screens(device, expected):
    assert flashers.Esptool().supports(device, None) is expected


@pytest.mark.parametrize(
    "device, expected",
    [
        (_device(kind=KIND_BARE, state=STATE_DFU, id=""), True),
        (_device(kind=KIND_BARE, state=STATE_BOOTSEL, id=""), False),
        (_device(kind=KIND_BARE, state=STATE_DFU, chipset="rp2040", id=""), False),
        (_device(state=STATE_DFU), False),
    ],
)
def test_dfu_util_writes_a_bare_stm32_in_dfu(device, expected):
    assert flashers.DfuUtil().supports(device, None) is expected


def test_bootsel_writes_a_board_already_in_bootsel():
    assert flashers.Bootsel().supports(_bare_rp2040(), None) is True


def test_bootsel_does_not_write_a_bare_board_that_is_not_in_bootsel():
    device = _device(chipset="rp2040", state=STATE_DFU, kind=KIND_BARE, id="")
    assert flashers.Bootsel().supports(device, _Requester()) is False


def test_bootsel_writes_a_running_board_whose_helper_can_request_bootsel():
    assert flashers.Bootsel().supports(_roadrunner(), _Requester()) is True


@pytest.mark.parametrize("kind", [KIND_SCREEN, KIND_CANBUS])
def test_bootsel_does_not_claim_a_non_serial_handoff(kind):
    device = _device(
        chipset="rp2040",
        state="unknown",
        kind=kind,
        detail={},
    )

    assert flashers.Bootsel().supports(device, _Requester()) is False


def test_the_helper_vouches_for_its_board_without_a_chipset():
    """A CMake type may leave `chipset:` empty, and today's write path takes it."""
    assert flashers.Bootsel().supports(_roadrunner(chipset=""), _Requester()) is True


@pytest.mark.parametrize("helper", [None, _Plain()])
def test_bootsel_does_not_write_a_running_board_with_no_requester(helper):
    assert flashers.Bootsel().supports(_roadrunner(), helper) is False


def test_a_board_in_bootsel_still_needs_an_rp2040_chipset():
    device = _device(chipset="stm32f446xx", state=STATE_BOOTSEL, kind=KIND_BARE, id="")
    assert flashers.Bootsel().supports(device, None) is False


# --- resolve -----------------------------------------------------------------


def test_the_first_flasher_in_the_familys_order_wins():
    # A running RP2040 both flashers accept: flashtool by chipset, bootsel
    # through the helper.
    device = _device(chipset="rp2040", type="roadrunner", id=RR_SERIAL, detail={"uf2_file": "x"})
    helper = _Requester()

    assert flashers.resolve(_family("bootsel", "flashtool"), device, helper).name == "bootsel"
    assert flashers.resolve(_family("flashtool", "bootsel"), device, helper).name == "flashtool"


def test_first_install_goes_through_the_katapult_list():
    katapult = _family("dfu_util", "bootsel", name="katapult")

    assert flashers.resolve(katapult, _bare_rp2040(), None).name == "bootsel"
    dfu = _device(kind=KIND_BARE, state=STATE_DFU, id="")
    assert flashers.resolve(katapult, dfu, None).name == "dfu_util"


def test_a_flasher_the_family_does_not_list_is_never_chosen():
    """flashtool could write this board; the family did not list it."""
    assert flashers.resolve(_family("esptool"), _device(), None) is None


def test_a_family_with_no_list_resolves_nothing():
    assert flashers.resolve(_family(), _device(), None) is None


# --- select ------------------------------------------------------------------


def test_nothing_supporting_the_device_is_refused_by_name(paths):
    family = _family("esptool", name="klipper")
    device = _device(type="ebb36", id="usb-Klipper_stm32g0b1xx_1-if00")

    with pytest.raises(NoFlasherError) as exc:
        flashers.select(paths, family, device, None, stop_services=("klipper",))

    message = str(exc.value)
    assert "[firmware klipper]" in message
    assert "flashers: esptool" in message
    assert "ebb36" in message
    assert exc.value.code == "no_flasher"
    assert exc.value.data["family"] == "klipper"
    assert exc.value.data["flashers"] == ["esptool"]
    assert exc.value.data["state"] == STATE_KLIPPER


def test_a_family_with_no_flashers_says_so(paths):
    with pytest.raises(NoFlasherError) as exc:
        flashers.select(paths, _family(), _device(), None, stop_services=())
    assert "flashers: (none)" in str(exc.value)


def test_the_helper_path_target_stops_services(paths):
    helper = _Requester()

    target = flashers.select(
        paths,
        _family("bootsel", name="roadrunner"),
        _roadrunner("/tmp/rr.uf2"),
        helper,
        stop_services=("klipper", "moonraker"),
    )

    assert target.flasher == "bootsel"
    assert target.type == "roadrunner"
    assert target.id == RR_SERIAL
    assert target.needs_services_stopped is True
    assert flashers.needs_services_stopped(target) is True
    assert target.stop_services == ("klipper", "moonraker")
    assert target.detail["helper"] is helper
    assert target.detail["uf2_file"] == "/tmp/rr.uf2"
    assert target.detail["chipset"] == "rp2040"


def test_a_board_already_in_bootsel_stops_nothing(paths):
    target = flashers.select(
        paths,
        _family("dfu_util", "bootsel", name="katapult"),
        _bare_rp2040("/tmp/katapult.uf2"),
        None,
        stop_services=("klipper",),
    )

    assert target.flasher == "bootsel"
    assert flashers.needs_services_stopped(target) is False
    assert "helper" not in target.detail
    assert target.stop_services == ()


def test_a_board_already_in_bootsel_is_copied_not_asked(paths):
    """Even when the family's helper could ask: there is nothing to ask."""
    target = flashers.select(
        paths,
        _family("bootsel", name="roadrunner"),
        _bare_rp2040(),
        _Requester(),
        stop_services=("klipper",),
    )

    assert "helper" not in target.detail
    assert flashers.needs_services_stopped(target) is False


def test_the_flashtool_target_carries_the_board_dict(paths):
    board = {"type": "ebb36", "serial": "usb-x", "chipset": "stm32g0b1xx"}
    device = _device(type="ebb36", id="usb-x", chipset="stm32g0b1xx", detail=board)

    target = flashers.select(
        paths, _family("flashtool"), device, None, stop_services=("klipper",)
    )

    assert target.flasher == "flashtool"
    assert dict(target.detail) == board
    assert target.stop_services == ("klipper",)
    assert flashers.needs_services_stopped(target) is True


def test_the_dfu_target_carries_the_image_and_serial(paths):
    device = _device(
        kind=KIND_BARE, state=STATE_DFU, id="DFU123", detail={"fw_bin": "/tmp/k.bin"}
    )

    target = flashers.select(
        paths, _family("dfu_util"), device, None, stop_services=("klipper",)
    )

    assert target.flasher == "dfu_util"
    assert target.detail["fw_bin"] == "/tmp/k.bin"
    assert target.detail["dfu_serial"] == "DFU123"
    assert flashers.needs_services_stopped(target) is False


# --- a batch ------------------------------------------------------------------


def _board_device(fw: str = "klipper") -> Device:
    return Device(
        type="ebb36",
        id="usb-Klipper_stm32g0b1xx_1-if00",
        chipset="stm32g0b1xx",
        state=STATE_KLIPPER,
        fw=fw,
        detail={
            "type": "ebb36",
            "serial": "usb-Klipper_stm32g0b1xx_1-if00",
            "chipset": "stm32g0b1xx",
            "fw": fw,
        },
    )


def _screen_device(fw: str = "knomi") -> Device:
    return Device(
        type="knomi",
        id="/dev/ttyKNOMI",
        chipset="",
        state="unknown",
        fw=fw,
        kind=KIND_SCREEN,
        detail={},
    )


def test_a_batch_refuses_one_device_without_dropping_the_rest(paths):
    """Spec §8 step 1. The refused screen comes first, so a refusal that raised
    would lose the board after it."""
    families = {
        "klipper": _family("flashtool", name="klipper"),
        "knomi": _family("dfu_util", name="knomi"),
    }

    targets, refused = flashers.select_each(
        paths,
        families,
        [(_screen_device(), ("klipper",)), (_board_device(), ("klipper", "moonraker"))],
    )

    assert [t.id for t in targets] == ["usb-Klipper_stm32g0b1xx_1-if00"]
    assert targets[0].flasher == "flashtool"
    assert targets[0].stop_services == ("klipper", "moonraker")
    [entry] = refused
    assert entry["type"] == "knomi"
    assert entry["id"] == "/dev/ttyKNOMI"
    assert entry["flasher"] is None
    # Its own family, not whichever the batch happened to resolve first.
    assert "[firmware knomi] (flashers: dfu_util)" in entry["error"]


def test_a_device_whose_family_is_undeclared_is_a_config_error(paths):
    with pytest.raises(ConfigCorruptError):
        flashers.select_device(
            paths, {}, _board_device(fw="nowhere"), stop_services=()
        )


def _no_controller(name=None):
    raise AssertionError(f"nothing was written, so nothing should stop {name!r}")


def test_a_refusal_is_reported_with_the_failures_and_nothing_waits(paths):
    """A refusal is warned and listed in `failures[]` like a failed write. A batch of
    nothing but refusals stopped nothing, so it has no restart to wait on."""
    lines: list[tuple[str, str]] = []
    ready: list = []
    entry = {
        "type": "ebb36",
        "id": "usb-x",
        "flasher": None,
        "error": "nothing in [firmware klipper] (flashers: esptool) can write it",
    }
    bench = flashers.Bench(paths=paths, settings=Settings(), controller=_no_controller)

    result = flashers.write_all(
        bench,
        [],
        flashers.PlainContext(lambda stream, line: lines.append((stream, line))),
        on_ready=ready.append,
        refused=[entry],
    )

    assert result == {"flashed": [], "failures": [entry]}
    assert ("warn", f"usb-x: {entry['error']}") in lines
    assert ready == []


# --- group_by_stop -----------------------------------------------------------


def test_the_target_flag_overrides_the_flasher_when_grouping():
    stays_up = flashers.FlashTarget(flasher="flashtool", type="a", id="1", needs_services_stopped=False)
    goes_down = flashers.FlashTarget(flasher="bootsel", type="b", id="2", needs_services_stopped=True)
    default_free = flashers.FlashTarget(flasher="bootsel", type="c", id="3")
    default_stopped = flashers.FlashTarget(flasher="flashtool", type="d", id="4")

    stopped, free = flashers.group_by_stop([stays_up, goes_down, default_free, default_stopped])

    assert stopped == [goes_down, default_stopped]
    assert free == [stays_up, default_free]
