"""The numbered menu, reading the one type list.

The menu's pickers read the kconfig-only `Registry`, so a Roadrunner, CMake or
PlatformIO type the CLI tracks did not exist in it. These drive the menus the
way a person does - queued answers to `input()` - against a fake install.
"""

from __future__ import annotations

import argparse
import dataclasses

import pytest

from mcu_updater import cli, tui, typelist
from mcu_updater.config import Registry
from mcu_updater.settings import Settings

from .conftest import make_device, save_registry, seed_base_firmwares

RR_SERIAL = "RR-ONE"


@pytest.fixture
def c(paths, monkeypatch):
    """A kconfig type `board` and a cmake type `roadrunner`, no real services."""
    seed_base_firmwares(paths)
    context = cli.Context(
        paths=paths, settings=Settings(service_backend="null", clean_before_build=False)
    )
    monkeypatch.setattr(cli, "_ctx", context)

    reg = Registry.load(paths)
    reg.add_type("board", "stm32f072xb")
    reg.add_serial("board", "AAAA-if00")
    save_registry(reg, paths)

    with open(paths.main_config, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "\n[firmware roadrunner]\nsource: ~/roadrunner/rp2040\nbuilder: cmake\n\n"
            "[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
            f"cmake_target: roadrunner_v1_usbserial\nserials:\n    {RR_SERIAL}\n"
        )
    return context


def answers(monkeypatch, *replies: str) -> list[str]:
    """Queue replies for `input()`; returns the prompts it was asked."""
    queue = list(replies)
    asked: list[str] = []

    def fake_input(prompt: str = "") -> str:
        asked.append(prompt)
        if not queue:
            pytest.fail(f"unexpected prompt: {prompt!r}")
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def _menu_lines(out: str, title: str) -> list[str]:
    """The numbered options printed under the last `title:` heading."""
    block = out.rsplit(f"\n{title}:\n", 1)[1]
    return [line.strip() for line in block.splitlines() if line.startswith("  ")]


def test_the_type_picker_lists_a_non_kconfig_type_alongside_a_kconfig_one(
    c, monkeypatch, capsys
):
    answers(monkeypatch, "0")
    assert tui.pick_mcu_type(allow_new=False) is None
    options = _menu_lines(capsys.readouterr().out, "Select MCU type")
    assert any(line.startswith("1. board  (chipset=stm32f072xb") for line in options)
    assert any(line.startswith("2. roadrunner  (chipset=rp2040, 1 serial(s))") for line in options)


def test_the_type_picker_returns_a_non_kconfig_type(c, monkeypatch):
    answers(monkeypatch, "2")
    assert tui.pick_mcu_type(allow_new=False) == "roadrunner"


def test_menuconfig_does_not_offer_a_non_kconfig_type(c, monkeypatch, capsys):
    called: list[argparse.Namespace] = []
    monkeypatch.setattr(cli, "make_menuconfig_cmd", called.append)
    answers(monkeypatch, "0")
    tui.menu_menuconfig()
    options = _menu_lines(capsys.readouterr().out, "Select MCU type")
    assert any("board" in line for line in options)
    assert not any("roadrunner" in line for line in options)
    assert called == []


def test_add_mcu_does_not_offer_a_non_kconfig_type(c, monkeypatch, capsys):
    answers(monkeypatch, "0")
    tui.menu_add_mcu()
    options = _menu_lines(capsys.readouterr().out, "Select MCU type")
    assert not any("roadrunner" in line for line in options)


def test_add_serial_on_a_non_kconfig_type_tracks_a_detected_board(
    c, fake_root, monkeypatch, capsys
):
    make_device(fake_root / "bus", "Vylyne", "rp2040", "RR-NEW")
    # A board already tracked under the other type is never offered.
    make_device(fake_root / "bus", "Klipper", "rp2040", RR_SERIAL)

    answers(monkeypatch, "2", "1")  # roadrunner, then the detected board
    tui.menu_add_serial()

    out = capsys.readouterr().out
    options = _menu_lines(out, "Select a serial to add to 'roadrunner'")
    assert options[0] == "1. RR-NEW (detected on bus)"
    assert not any(RR_SERIAL + " " in line for line in options)
    assert "Added serial RR-NEW to roadrunner" in out
    assert Registry.load(c.paths).declared_serials("roadrunner") == [RR_SERIAL, "RR-NEW"]
    with open(c.paths.main_config, encoding="utf-8") as fh:
        assert "RR-NEW" in fh.read()


def test_remove_serial_on_a_non_kconfig_type_untracks_it(c, monkeypatch, capsys):
    answers(monkeypatch, "2", "1")  # roadrunner, then its one serial
    tui.menu_remove_serial()
    assert f"Removed serial {RR_SERIAL} from roadrunner" in capsys.readouterr().out
    assert Registry.load(c.paths).declared_serials("roadrunner") == []


def test_flash_offers_exactly_the_types_the_flash_handler_accepts(c, monkeypatch, capsys):
    answers(monkeypatch, "0")
    tui.menu_flash()
    options = _menu_lines(capsys.readouterr().out, "Select MCU type")

    entries = typelist.load(c.paths)
    expected = [e.name for e in entries if tui.flashable_type(e)]
    offered = [line.split(". ", 1)[1].split("  (", 1)[0] for line in options[:-1]]
    assert offered == expected
    assert "roadrunner" in offered  # a cmake type: `flash -t roadrunner -s ...`

    # An entry no provider claims is out: `provider_of` refuses it.
    board = next(e for e in entries if e.name == "board")
    unclaimed = dataclasses.replace(board, builders=("",))
    assert not tui.flashable_type(unclaimed)
    assert tui.FLASH_BUILDERS == {"kconfig_make", "cmake", "platformio"}


def test_flash_offers_a_cmake_type_only_single_device_scope(c, monkeypatch, capsys):
    """`flash -t roadrunner` with no serial is refused type-level."""
    answers(monkeypatch, "2", "0")
    tui.menu_flash()
    scopes = _menu_lines(capsys.readouterr().out, "Flash scope for 'roadrunner'")
    assert scopes == ["1. Flash one specific device", "0. Cancel"]


def test_flash_single_device_passes_every_key_the_handler_reads(c, monkeypatch):
    called: list[argparse.Namespace] = []
    monkeypatch.setattr(cli, "flash_fw_cmd", called.append)
    answers(monkeypatch, "1", "2", "1")  # board, one device, its tracked serial
    tui.menu_flash()
    assert called == [
        argparse.Namespace(type="board", serial="AAAA-if00", yes=False, force=False)
    ]


def test_a_refused_add_prints_the_error_and_returns_to_the_menu(c, monkeypatch, capsys):
    # AAAA-if00 is tracked under `board`, so adding it to roadrunner is refused.
    answers(monkeypatch, "2", "1", "AAAA-if00")  # roadrunner, manual, the serial
    tui.menu_add_serial()
    out = capsys.readouterr().out
    assert "ERROR: serial 'AAAA-if00' is already tracked under 'board'" in out
    assert "(action did not complete successfully" in out
    assert Registry.load(c.paths).declared_serials("roadrunner") == [RR_SERIAL]
