"""One physical device, one write.

Two entries can be the same device: a configured `/dev/ttyACM8` beside a board
whose by-id link points at it, two types whose helpers list the same section, a
hand-made link. A batch that wrote each in turn left the device with whichever
image came last and reported two successes.

The comparison is on the resolved node and happens in `write_all`, the one
place every caller's separately-selected lists meet. Everything here but the
last test is plain strings, so it runs on Windows too.
"""

from __future__ import annotations

import contextlib
import os
import sys

import pytest

from mcu_updater import flashers
from mcu_updater.artifacts import Artifact, Staged
from mcu_updater.firmware import FirmwareFamily
from mcu_updater.flashers.batch import refuse_shared
from mcu_updater.settings import Settings

NODE = "/dev/ttyACM8"
BOARD = "usb-Klipper_stm32g0b1xx_1-if00"


def _target(type: str, id: str, node: str | None, flasher: str = "fake") -> flashers.FlashTarget:
    return flashers.FlashTarget(flasher=flasher, type=type, id=id, resolved_path=node)


# --- the rule ---------------------------------------------------------------


def test_two_entries_for_one_node_are_both_refused():
    """Not all but one: nothing here knows which entry is right, and writing
    the first is the same bug with a different winner."""
    board = _target("ebb36", BOARD, NODE, "flashtool")
    screen = _target("knomi", NODE, NODE, "platformio")
    other = _target("octopus", "usb-Klipper_stm32h723xx_2-if00", "/dev/ttyACM1", "flashtool")

    kept, refused = refuse_shared([board, screen, other])

    assert kept == [other]
    assert [(r["type"], r["id"], r["flasher"]) for r in refused] == [
        ("ebb36", BOARD, "flashtool"),
        ("knomi", NODE, "platformio"),
    ]


def test_each_refusal_names_the_other_entry_and_the_node():
    kept, refused = refuse_shared([_target("ebb36", BOARD, NODE), _target("knomi", "/dev/knomi", NODE)])

    assert kept == []
    assert "/dev/knomi (knomi)" in refused[0]["error"]
    assert f"{BOARD} (ebb36)" in refused[1]["error"]
    # Itself is not one of the others.
    assert f"{BOARD} (ebb36)" not in refused[0]["error"]
    assert all(NODE in r["error"] for r in refused)


def test_three_entries_for_one_node_each_name_the_other_two():
    targets = [_target(name, f"/dev/{name}", NODE) for name in ("a", "b", "c")]

    kept, refused = refuse_shared(targets)

    assert kept == []
    assert "/dev/b (b), /dev/c (c)" in refused[0]["error"]
    assert "/dev/a (a), /dev/c (c)" in refused[1]["error"]


@pytest.mark.parametrize("nowhere", [None, ""])
def test_a_device_with_no_node_never_collides(nowhere):
    """A CAN board has no node. Two of them are two boards, not one."""
    first = _target("ebb36", "aabbccddeeff", nowhere)
    second = _target("sht36", "112233445566", nowhere)

    assert refuse_shared([first, second]) == ([first, second], [])


def test_different_nodes_are_left_alone():
    first = _target("ebb36", BOARD, "/dev/ttyACM0")
    second = _target("knomi", "/dev/knomi", "/dev/ttyACM1")

    assert refuse_shared([first, second]) == ([first, second], [])


def test_the_same_entry_listed_twice_is_a_repeat_not_a_conflict():
    """One `(type, id)` is one image either way. Whether a caller should have
    listed it twice is not this rule's question."""
    once = _target("knomi", NODE, NODE)
    again = _target("knomi", NODE, NODE)

    assert refuse_shared([once, again]) == ([once, again], [])


def test_one_id_under_two_types_is_a_conflict():
    """Two types whose helpers list the same section: same port, two images."""
    kept, refused = refuse_shared([_target("knomi_v1", NODE, NODE), _target("knomi_v2", NODE, NODE)])

    assert kept == []
    assert len(refused) == 2


# --- the batch --------------------------------------------------------------


class _Fake:
    name = "fake"
    label = "Fake"
    chipsets: tuple[str, ...] = ("",)
    states: tuple[str, ...] = ()
    needs_services_stopped = False
    accepts: tuple[str, ...] = ("bin",)

    def __init__(self) -> None:
        self.written: list[str] = []

    def supports(self, device, helper) -> bool:
        return True

    def target(self, paths, device, helper, artifact, *, stop_services):
        return flashers.FlashTarget(
            flasher=self.name, type=device.type, id=device.id, stop_services=stop_services, artifact=artifact
        )

    def prepared(self, bench, targets, ctx):
        return contextlib.nullcontext(None)

    def write(self, bench, session, target, ctx):
        self.written.append(target.id)
        return {}

    def record(self, bench, target):
        return None

    def settled(self, bench, target, ctx):
        return None


@pytest.fixture
def fake(monkeypatch):
    flasher = _Fake()
    monkeypatch.setitem(flashers.registry._BY_NAME, flasher.name, flasher)
    return flasher


def _no_controller(name=None):
    raise AssertionError(f"nothing was written, so nothing should stop {name!r}")


def test_a_batch_writes_neither_entry_and_reports_both(paths, fake):
    lines: list[tuple[str, str]] = []
    ready: list = []
    bench = flashers.Bench(paths=paths, settings=Settings(), controller=_no_controller)
    board = _target("ebb36", BOARD, NODE)
    screen = _target("knomi", "/dev/knomi", NODE)

    result = flashers.write_all(
        bench,
        [board, screen],
        flashers.PlainContext(lambda stream, line: lines.append((stream, line))),
        on_ready=ready.append,
    )

    assert fake.written == []
    assert result["flashed"] == []
    assert [f["id"] for f in result["failures"]] == [BOARD, "/dev/knomi"]
    assert any(stream == "warn" and line.startswith(f"{BOARD}: ") for stream, line in lines)
    # Nothing was attempted, so nothing stopped and there is no restart to wait on.
    assert ready == []


def test_the_rest_of_the_batch_is_still_written(paths, fake):
    """A failure, not an abort - the same rule a device nothing can write follows."""
    bench = flashers.Bench(paths=paths, settings=Settings(), controller=_no_controller)
    targets = [
        _target("ebb36", BOARD, NODE),
        _target("octopus", "usb-Klipper_stm32h723xx_2-if00", "/dev/ttyACM1"),
        _target("knomi", "/dev/knomi", NODE),
    ]

    result = flashers.write_all(bench, targets, flashers.PlainContext(lambda *a: None))

    assert fake.written == ["usb-Klipper_stm32h723xx_2-if00"]
    assert [f["id"] for f in result["flashed"]] == ["usb-Klipper_stm32h723xx_2-if00"]
    assert sorted(f["id"] for f in result["failures"]) == ["/dev/knomi", BOARD]


def test_a_refusal_from_selection_and_one_from_here_are_both_reported(paths, fake):
    bench = flashers.Bench(paths=paths, settings=Settings(), controller=_no_controller)
    unwritable = {"type": "sht36", "id": "usb-x", "flasher": None, "error": "nothing can write it"}

    result = flashers.write_all(
        bench,
        [_target("ebb36", BOARD, NODE), _target("knomi", "/dev/knomi", NODE)],
        flashers.PlainContext(lambda *a: None),
        refused=[unwritable],
    )

    assert [f["id"] for f in result["failures"]] == ["usb-x", BOARD, "/dev/knomi"]


# --- selection resolves the path --------------------------------------------


def _select(paths, fake, device: flashers.Device) -> flashers.FlashTarget:
    return flashers.select(
        paths,
        FirmwareFamily(name="fam", flashers=("fake",)),
        device,
        None,
        stop_services=(),
        staged=Staged(fw="fam", artifacts=(Artifact("bin", "/x.bin"),)),
    )


def _device(id: str, path: str | None, type: str = "board") -> flashers.Device:
    return flashers.Device(type=type, id=id, chipset="", state="klipper", fw="fam", path=path)


def test_selection_resolves_where_the_device_was_listed(paths, fake, tmp_path):
    """Whatever spelling the lister had: the target carries the node itself,
    so no flasher's `target` has to know the field exists."""
    node = tmp_path / "ttyACM8"
    node.write_text("", encoding="utf-8")
    (tmp_path / "by-path").mkdir()
    roundabout = os.path.join(str(tmp_path), "by-path", "..", "ttyACM8")

    direct = _select(paths, fake, _device("a", str(node)))
    indirect = _select(paths, fake, _device("b", roundabout))

    assert direct.resolved_path == os.path.realpath(str(node))
    assert indirect.resolved_path == direct.resolved_path
    assert roundabout != str(node)


def test_a_device_listed_nowhere_resolves_to_nothing(paths, fake):
    assert _select(paths, fake, _device("a", None)).resolved_path is None
    assert _select(paths, fake, _device("a", "")).resolved_path is None


def test_the_resolved_path_stays_off_the_wire(paths, fake):
    """Good for one batch. A node's name is whatever the kernel handed out this
    time, so nothing downstream should be given the chance to remember it."""
    target = _select(paths, fake, _device("a", "/dev/ttyACM8"))

    assert target.to_json() == {"type": "board", "id": "a", "flasher": "fake"}


@pytest.mark.skipif(sys.platform == "win32", reason="needs real symlinks, as udev makes them")
def test_a_by_id_link_and_the_node_it_points_at_are_one_device(paths, fake, tmp_path):
    """The case from the printer: `[type board]` tracks the by-id link while a
    PlatformIO section names the tty outright."""
    node = tmp_path / "ttyACM8"
    node.write_text("", encoding="utf-8")
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    link = by_id / BOARD
    os.symlink(node, link)

    board = _select(paths, fake, _device(BOARD, str(link), type="ebb36"))
    screen = _select(paths, fake, _device(str(node), str(node), type="knomi"))

    kept, shared = refuse_shared([board, screen])

    assert kept == []
    assert [r["id"] for r in shared] == [BOARD, str(node)]
