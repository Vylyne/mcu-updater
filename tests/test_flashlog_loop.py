"""One writer for the ledger: the batch loop.

Spec section 7 and Ruling 12. A flasher describes what it just wrote; it does
not decide whether to file it, where the file is, or whether this run was a
rehearsal. Those three are the same three decisions every copy of this code
used to make separately.
"""

from __future__ import annotations

import dataclasses
import json
import os

import pytest

from mcu_updater import flashers, helpers
from mcu_updater.build import FlashLog
from mcu_updater.errors import FlashError, UpdaterError
from mcu_updater.settings import Settings


class _Fake:
    """A flasher with nothing behind it but a record and a plan."""

    name = "fake"
    label = "Fake"
    chipsets: tuple[str, ...] = ("",)
    states: tuple[str, ...] = ()
    needs_services_stopped = False

    def __init__(
        self, record=None, *, fails=(), record_fails=(), settled_raises=False, extra=None
    ):
        self.record_value = record
        self.fails = set(fails)
        self.record_fails = set(record_fails)
        self.settled_raises = settled_raises
        self.extra = extra or {}
        self.written: list[str] = []

    def supports(self, device, helper) -> bool:
        return True

    def target(self, paths, device, helper, *, stop_services):
        return flashers.FlashTarget(
            flasher=self.name, type=device.type, id=device.id,
            stop_services=stop_services,
        )

    def prepared(self, bench, targets, ctx):
        import contextlib

        return contextlib.nullcontext(None)

    def write(self, bench, session, target, ctx):
        if target.id in self.fails:
            raise FlashError("the write failed", type=target.type, id=target.id)
        self.written.append(target.id)
        return dict(self.extra)

    def record(self, bench, target):
        if target.id in self.record_fails:
            raise UpdaterError("the ledger entry could not be built")
        if self.record_value is None:
            return None
        return dataclasses.replace(self.record_value, key=target.id)

    def settled(self, bench, target, ctx):
        if self.settled_raises:
            raise FlashError("it never came back", type=target.type, id=target.id)


RECORD = flashers.FlashRecord(
    key="",
    mcu_type="ebb36",
    fw="klipper",
    bin_sha256="ab" * 32,
    fw_sha="cafe1234",
    version="v0.12.0-1-gcafe123",
)


@pytest.fixture
def bench(paths, settings):
    return flashers.Bench(
        paths=paths, settings=settings, controller=lambda name=None: None
    )


def _target(flasher, id: str) -> flashers.FlashTarget:
    return flashers.FlashTarget(flasher=flasher.name, type="ebb36", id=id)


def _run(bench, flasher, targets, monkeypatch) -> dict:
    monkeypatch.setitem(flashers.registry._BY_NAME, flasher.name, flasher)
    return flashers.write_all(
        bench, targets, flashers.PlainContext(lambda *a: None)
    )


def test_the_loop_writes_the_record_a_flasher_describes(bench, monkeypatch):
    flasher = _Fake(RECORD)

    _run(bench, flasher, [_target(flasher, "S1")], monkeypatch)

    entry = FlashLog(bench.paths).all()["S1"]
    assert entry["type"] == "ebb36"
    assert entry["fw"] == "klipper"
    assert entry["bin_sha256"] == "ab" * 32
    assert entry["fw_sha"] == "cafe1234"
    assert entry["version"] == "v0.12.0-1-gcafe123"


def test_a_dry_run_records_nothing(paths, monkeypatch):
    """The guard that used to be copied into all four writers. Nothing was
    written, so nothing about the board is true afterwards."""
    rehearsal = flashers.Bench(
        paths=paths,
        settings=dataclasses.replace(Settings(), dry_run=True),
        controller=lambda name=None: None,
    )
    flasher = _Fake(RECORD)

    _run(rehearsal, flasher, [_target(flasher, "S1")], monkeypatch)

    assert FlashLog(paths).all() == {}


def test_a_flasher_with_nothing_to_file_records_nothing(bench, monkeypatch):
    flasher = _Fake(None)

    _run(bench, flasher, [_target(flasher, "S1")], monkeypatch)

    assert FlashLog(bench.paths).all() == {}


def test_confidence_rides_the_write_result_and_leaves_the_wire_alone(
    bench, monkeypatch
):
    """How the board was identified is known inside the write and nowhere else,
    so it comes back with the result - and comes straight back off it again.
    `flashed[]` is on the wire; the ledger is not."""
    flasher = _Fake(RECORD, extra={"serial": "S1", "confidence": "unique_bus_id"})

    result = _run(bench, flasher, [_target(flasher, "S1")], monkeypatch)

    assert result["flashed"] == [
        {"type": "ebb36", "id": "S1", "flasher": "fake", "serial": "S1"}
    ]
    assert FlashLog(bench.paths).all()["S1"]["confidence"] == "unique_bus_id"


def test_a_written_board_is_recorded_before_a_later_device_fails(bench, monkeypatch):
    """Spec, Testing: `FlashLog` written before the failure is raised. An
    operator told "failed" with no record has every reason to write the same
    image to the same board again."""
    flasher = _Fake(RECORD, fails={"S2"})

    result = _run(
        bench, flasher, [_target(flasher, "S1"), _target(flasher, "S2")], monkeypatch
    )

    assert [f["id"] for f in result["failures"]] == ["S2"]
    assert sorted(FlashLog(bench.paths).all()) == ["S1"]


def test_a_board_that_never_came_back_is_still_recorded(bench, monkeypatch):
    """`settled` runs after the record, not before it. The image is on the
    board the moment the write returns; a slow return does not unwrite it."""
    flasher = _Fake(RECORD, settled_raises=True)

    _run(bench, flasher, [_target(flasher, "S1")], monkeypatch)

    assert "S1" in FlashLog(bench.paths).all()


def test_nothing_after_the_copy_can_lose_its_record(bench, monkeypatch):
    """Nothing which happens after the copy can lose the record, which pins the
    ordering promised by the agent API."""
    flasher = _Fake(RECORD)
    monkeypatch.setitem(flashers.registry._BY_NAME, flasher.name, flasher)

    with pytest.raises(UpdaterError, match="readiness failed"):
        flashers.write_all(
            bench,
            [_target(flasher, "S1")],
            flashers.PlainContext(lambda *a: None),
            on_ready=lambda reporter: (_ for _ in ()).throw(
                UpdaterError("readiness failed")
            ),
        )

    assert "S1" in FlashLog(bench.paths).all()


def test_a_record_failure_does_not_abort_the_batch(bench, monkeypatch):
    flasher = _Fake(RECORD, record_fails={"S1"})
    monkeypatch.setitem(flashers.registry._BY_NAME, flasher.name, flasher)
    reports: list[tuple[str, str]] = []

    result = flashers.write_all(
        bench,
        [_target(flasher, "S1"), _target(flasher, "S2")],
        flashers.PlainContext(lambda level, message: reports.append((level, message))),
    )

    assert [item["id"] for item in result["flashed"]] == ["S1", "S2"]
    assert result["failures"] == []
    assert reports == [
        (
            "warn",
            "S1: flashed, but its ledger record could not be filed: "
            "the ledger entry could not be built",
        )
    ]
    assert sorted(FlashLog(bench.paths).all()) == ["S2"]


# --- what each flasher describes ----------------------------------------------


def test_flashtool_describes_the_kconfig_sidecar(bench, paths):
    os.makedirs(paths.artifact_dir("ebb36"), exist_ok=True)
    with open(paths.sidecar_file("ebb36", "klipper"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "fw_sha": "built-klipper-sha",
                "bin_sha256": "built-bin-sha",
                "version": "CARTOGRAPHER 6.2.0",
            },
            fh,
        )
    target = flashers.flashtool.target_for(
        {"type": "ebb36", "serial": "S1", "chipset": "stm32g0b1xx", "fw": "klipper"}
    )

    record = flashers.Flashtool().record(bench, target)

    assert record == flashers.FlashRecord(
        key="S1",
        mcu_type="ebb36",
        fw="klipper",
        bin_sha256="built-bin-sha",
        fw_sha="built-klipper-sha",
        version="CARTOGRAPHER 6.2.0",
    )


def test_bootsel_describes_the_cmake_sidecar(bench, paths, cmake_type, tmp_path):
    """The two sidecar schemas name the tree commit differently - `sha` here,
    `fw_sha` for kconfig - so each flasher reads the one its own builder wrote."""
    uf2 = tmp_path / "roadrunner.uf2"
    uf2.write_bytes(b"image")
    target = flashers.bootsel.target_for(
        str(uf2),
        chipset="rp2040",
        type_name="roadrunner",
        serial="RR-1",
        helper=helpers.for_name("roadrunner", family="roadrunner"),
    )

    record = flashers.Bootsel().record(bench, target)

    assert record is not None
    assert record.key == "RR-1"
    assert record.fw == "roadrunner"
    assert record.fw_sha == "built-subtree-sha"
    assert record.bin_sha256 == "built-uf2-sha256"


def test_bootsel_has_nothing_to_file_for_a_bare_board(bench, cmake_type, tmp_path):
    """First install: the `type` is a chipset string and there may be no serial
    at all, so there is no tracked device to file this under."""
    uf2 = tmp_path / "katapult.uf2"
    uf2.write_bytes(b"image")
    target = flashers.bootsel.target_for(str(uf2), chipset="rp2040")

    assert flashers.Bootsel().record(bench, target) is None
