"""The numbered menu, reading the one type list.

The menu's pickers read the kconfig-only `Registry`, so a Roadrunner, CMake or
PlatformIO type the CLI tracks did not exist in it. These drive the menus the
way a person does - queued answers to `input()` - against a fake install.
"""

from __future__ import annotations

import argparse

import pytest

from mcu_updater import cli, firmware, tui, typelist
from mcu_updater.config import Registry
from mcu_updater.errors import UpdaterError
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


def _roadrunner_on_bus(fake_root, serial: str) -> None:
    """A Roadrunner as it really enumerates (discovery/roadrunner.py's
    `usb-Vylyne_Roadrunner_<serial>-if00`): its chipset token is `Roadrunner`,
    never the type's `chipset: rp2040`."""
    (fake_root / "bus" / f"usb-Vylyne_Roadrunner_{serial}-if00").write_text("", encoding="utf-8")


def test_add_serial_on_a_non_kconfig_type_tracks_a_detected_board(
    c, fake_root, monkeypatch, capsys
):
    _roadrunner_on_bus(fake_root, "RR-NEW")
    # A board already tracked under a type is never offered.
    _roadrunner_on_bus(fake_root, RR_SERIAL)

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


def test_add_serial_on_a_kconfig_type_still_filters_by_chipset(
    c, fake_root, monkeypatch, capsys
):
    make_device(fake_root / "bus", "katapult", "stm32f072xb", "BBBB")
    make_device(fake_root / "bus", "katapult", "rp2040", "CCCC")
    _roadrunner_on_bus(fake_root, "RR-NEW")

    answers(monkeypatch, "1", "0")  # board, then cancel
    tui.menu_add_serial()

    options = _menu_lines(capsys.readouterr().out, "Select a serial to add to 'board'")
    assert options == ["1. BBBB (detected on bus)", "2. Enter serial manually", "0. Cancel"]


def test_remove_serial_on_a_non_kconfig_type_untracks_it(c, monkeypatch, capsys):
    answers(monkeypatch, "2", "1")  # roadrunner, then its one serial
    tui.menu_remove_serial()
    assert f"Removed serial {RR_SERIAL} from roadrunner" in capsys.readouterr().out
    assert Registry.load(c.paths).declared_serials("roadrunner") == []


def _append_config(paths, text: str) -> None:
    with open(paths.main_config, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _declare_platformio(paths, fake_root, extra: str = "") -> None:
    tree = fake_root / "knomi"
    tree.mkdir()
    (tree / "platformio.ini").write_text("[env:knomi]\n", encoding="utf-8")
    _append_config(
        paths,
        f"\n[firmware knomi_serial]\nsource: {tree}\nbuilder: platformio\n\n"
        f"[type knomi]\nfirmware: knomi_serial\nplatformio_env: knomi\n{extra}",
    )


def test_flash_offers_exactly_the_types_the_flash_handler_accepts(c, monkeypatch, capsys):
    """A family whose `builder:` no provider knows passes config validation, and
    `flash_fw_cmd` refuses its type (`provider_of`: "no type ... is configured").
    The picker does not offer it."""
    _append_config(
        c.paths,
        "\n[firmware oddfw]\nsource: ~/odd\nbuilder: bogus\n\n"
        "[type oddtype]\nfirmware: oddfw\nserials: ODD-1\n",
    )
    assert "oddtype" in [e.name for e in typelist.load(c.paths)]
    with pytest.raises(UpdaterError):
        cli.flash_fw_cmd(argparse.Namespace(type="oddtype", serial=None, yes=True, force=False))

    answers(monkeypatch, "0")
    tui.menu_flash()
    options = _menu_lines(capsys.readouterr().out, "Select MCU type")
    assert options == [
        "1. board  (chipset=stm32f072xb, 1 serial(s))",
        "2. roadrunner  (chipset=rp2040, 1 serial(s))",
        "0. Cancel",
    ]


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


def test_build_never_asks_a_cmake_type_for_a_firmware_target(c, monkeypatch):
    """`build_fw_cmd`'s cmake and platformio branches ignore `-f`."""
    called: list[argparse.Namespace] = []
    monkeypatch.setattr(cli, "build_fw_cmd", called.append)
    asked = answers(monkeypatch, "2")  # roadrunner - and nothing else is asked
    tui.menu_build()
    assert asked == ["> "]
    assert called == [argparse.Namespace(type="roadrunner", fw=None, jobs=None)]


def test_build_never_asks_a_platformio_type_for_a_firmware_target(c, fake_root, monkeypatch):
    _declare_platformio(c.paths, fake_root)
    called: list[argparse.Namespace] = []
    monkeypatch.setattr(cli, "build_fw_cmd", called.append)
    answers(monkeypatch, "3")
    tui.menu_build()
    assert called == [argparse.Namespace(type="knomi", fw=None, jobs=None)]


def test_build_still_asks_a_kconfig_type_for_a_firmware_target(c, monkeypatch, capsys):
    called: list[argparse.Namespace] = []
    monkeypatch.setattr(cli, "build_fw_cmd", called.append)
    answers(monkeypatch, "1", "1")  # board, then the first firmware target
    tui.menu_build()
    assert "Select firmware target:" in capsys.readouterr().out
    first = firmware.names(c.paths)[0]
    assert called == [argparse.Namespace(type="board", fw=first, jobs=None)]


def test_flash_offers_a_platformio_type_whole_type_scope_only(
    c, fake_root, monkeypatch, capsys
):
    """`flash -t knomi -s X` matches X against a port or device_id, never a
    by-id serial - so a tracked serial is no single-device target for it."""
    _declare_platformio(c.paths, fake_root, "serials: KNOMI-1\n")
    answers(monkeypatch, "3", "0")
    tui.menu_flash()
    scopes = _menu_lines(capsys.readouterr().out, "Flash scope for 'knomi'")
    assert scopes == ["1. Flash every tracked serial under this type", "0. Cancel"]


def test_flash_does_not_offer_a_cmake_type_an_untracked_board(
    c, fake_root, monkeypatch, capsys
):
    _roadrunner_on_bus(fake_root, "RR-NEW")
    answers(monkeypatch, "2", "1", "0")  # roadrunner, one device, cancel
    tui.menu_flash()
    options = _menu_lines(capsys.readouterr().out, "Select a device under 'roadrunner'")
    assert options == [f"1. {RR_SERIAL} (tracked)", "0. Cancel"]


def test_flash_still_offers_a_kconfig_type_untracked_boards(
    c, fake_root, monkeypatch, capsys
):
    make_device(fake_root / "bus", "katapult", "stm32f072xb", "BBBB")
    answers(monkeypatch, "1", "2", "0")  # board, one device, cancel
    tui.menu_flash()
    options = _menu_lines(capsys.readouterr().out, "Select a device under 'board'")
    assert options == [
        "1. AAAA-if00 (tracked)",
        "2. BBBB (untracked, detected on bus)",
        "3. Enter serial manually",
        "0. Cancel",
    ]


def test_menuconfig_with_only_a_cmake_type_says_why_none_is_offered(
    paths, monkeypatch, capsys
):
    seed_base_firmwares(paths)
    monkeypatch.setattr(
        cli,
        "_ctx",
        cli.Context(paths=paths, settings=Settings(service_backend="null", clean_before_build=False)),
    )
    _append_config(
        paths,
        "\n[firmware roadrunner]\nsource: ~/roadrunner/rp2040\nbuilder: cmake\n\n"
        "[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
        "cmake_target: roadrunner_v1_usbserial\n",
    )
    answers(monkeypatch, "0")
    tui.menu_menuconfig()
    out = capsys.readouterr().out
    assert tui.KCONFIG_ONLY_MENUCONFIG in out
    assert (
        tui.KCONFIG_ONLY_MENUCONFIG
        == "No kconfig types are declared - menuconfig works on kconfig types only."
    )
    assert "No MCU types configured yet" not in out
    assert _menu_lines(out, "Select MCU type") == ["1. + Add a new MCU type", "0. Cancel"]


def test_a_config_with_no_types_keeps_the_no_types_message(paths, monkeypatch, capsys):
    seed_base_firmwares(paths)
    monkeypatch.setattr(
        cli,
        "_ctx",
        cli.Context(paths=paths, settings=Settings(service_backend="null", clean_before_build=False)),
    )
    assert tui.pick_mcu_type(allow_new=False, accepts=tui.kconfig_type) is None
    assert "No MCU types configured yet." in capsys.readouterr().out


def test_remove_type_offers_a_non_kconfig_type(c, monkeypatch, capsys):
    answers(monkeypatch, "2", "y")  # roadrunner, confirm
    tui.menu_remove_mcu_type()
    assert "Removed MCU Type: roadrunner" in capsys.readouterr().out
    assert [e.name for e in typelist.load(c.paths)] == ["board"]


def test_a_refused_add_prints_the_error_and_returns_to_the_menu(c, monkeypatch, capsys):
    # AAAA-if00 is tracked under `board`, so adding it to roadrunner is refused.
    answers(monkeypatch, "2", "1", "AAAA-if00")  # roadrunner, manual, the serial
    tui.menu_add_serial()
    out = capsys.readouterr().out
    assert "ERROR: serial 'AAAA-if00' is already tracked under 'board'" in out
    assert "(action did not complete successfully" in out
    assert Registry.load(c.paths).declared_serials("roadrunner") == [RR_SERIAL]
