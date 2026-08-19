"""Seeding answers from a firmware tree, and deriving the bootloader's from them.

Against real kconfiglib and two real (small) Kconfig trees, for the same reason
test_kconfig is: the behaviours that matter here - a value accepted while its
symbol is still invisible, a default that suppresses an answer from the minimal
config, a symbol defined in one tree and not the other - are kconfiglib's own
semantics. A stub would agree with whatever this module assumed.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil

import pytest

from mcu_updater import firmware, profiles
from mcu_updater.config import Registry
from mcu_updater.errors import (
    OffsetMismatchError,
    ProfileCustomisedError,
    ProfileError,
    ProfileNotFoundError,
)
from mcu_updater.paths import Paths

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
VENDORED = FIXTURES / "kconfiglib" / "kconfiglib.py"
PROFILE_TREE = FIXTURES / "profile_tree"

SEEDS = (
    "config.TestBoardUSB",
    "config.TestBoardCAN",
    "config.TestBoardBigLoader",
    "config.TestBoardSlowCAN",
)


def make_tree(root: pathlib.Path, name: str, kconfig_file: str, seeds: bool) -> pathlib.Path:
    """A firmware tree: src/Kconfig, its own kconfiglib, and maybe seed files."""
    tree = root / name
    (tree / "src").mkdir(parents=True, exist_ok=True)
    (tree / "lib" / "kconfiglib").mkdir(parents=True, exist_ok=True)
    shutil.copy(VENDORED, tree / "lib" / "kconfiglib" / "kconfiglib.py")
    shutil.copy(PROFILE_TREE / kconfig_file, tree / "src" / "Kconfig")
    if seeds:
        for seed in SEEDS:
            shutil.copy(PROFILE_TREE / seed, tree / seed)
    return tree


@pytest.fixture
def trees(tmp_path: pathlib.Path) -> pathlib.Path:
    """A pretend ~ holding an application tree and a bootloader tree.

    Named klipper/katapult so the built-in family conventions apply and no
    `[firmware ...]` section is needed - the cartographer case adds one, and
    that is tested separately rather than made the baseline.
    """
    make_tree(tmp_path, "klipper", "app.Kconfig", seeds=True)
    make_tree(tmp_path, "katapult", "boot.Kconfig", seeds=False)
    (tmp_path / "printer_data" / "config" / "mcu-updater").mkdir(parents=True)
    (tmp_path / "printer_data" / "mcu-updater").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def paths(trees: pathlib.Path) -> Paths:
    return Paths.from_env(env={"MCU_UPDATER_HOME": str(trees)})


@pytest.fixture
def registry(paths: Paths) -> Registry:
    with Registry.mutate(paths, "test setup") as reg:
        reg.add_type("carto_v4", "stm32g431xx")
    return Registry.load(paths)


def answers(result: profiles.SeedResult) -> dict[str, str]:
    return dict(profiles.parse_answer(line) for line in result.answers)  # type: ignore[arg-type]


def config_text(paths: Paths, mcu_type: str, fw: str) -> str:
    return pathlib.Path(paths.config_file(mcu_type, fw)).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# discovering what a tree offers
# --------------------------------------------------------------------------


def test_a_tree_offers_the_seed_files_it_ships(paths, registry):
    found = profiles.available(paths, "klipper")
    assert [s.name for s in found] == sorted(SEEDS)
    assert all(s.fw == "klipper" for s in found)


def test_a_tree_shipping_none_is_not_an_error(paths, registry):
    """Katapult ships no configs, and that is the correct answer for it - not a
    reason for a listing call to raise."""
    assert profiles.available(paths, "katapult") == []


def test_a_missing_tree_lists_nothing_rather_than_raising(paths, registry):
    shutil.rmtree(paths.fw_dir("klipper"))
    assert profiles.available(paths, "klipper") == []


def test_an_unknown_profile_names_the_real_ones(paths, registry):
    with pytest.raises(ProfileNotFoundError) as exc:
        profiles.find(paths, "klipper", "config.CartoV9")
    assert "config.TestBoardUSB" in str(exc.value)
    assert exc.value.data["available"] == sorted(SEEDS)


@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd",
        "config.x/../../../etc/passwd",
        "/etc/passwd",
        "",
        "   ",
        # Not a seed file at all: the prefix is the whitelist.
        "Makefile",
        ".ssh/id_rsa",
    ],
)
def test_a_profile_name_is_a_basename_in_the_tree_root(paths, registry, name):
    """The name arrives from a browser and is joined onto a source tree path.

    Refused *by shape*, and the distinction from "no such profile" is the point
    of the assertion rather than pedantry. Both refusals happen to stop a
    traversal today - a name with a separator in it can never equal the
    basename of a globbed seed - so a test that accepted either would pass with
    the shape check deleted, and would go on passing right up until something
    resolved a name without going through `find`.
    """
    with pytest.raises(ProfileError) as exc:
        profiles.find(paths, "klipper", name)
    assert not isinstance(exc.value, ProfileNotFoundError)
    assert exc.value.code == "profile"


# --------------------------------------------------------------------------
# seeding
# --------------------------------------------------------------------------


def test_seeding_writes_the_config_and_reports_only_the_answers(paths, registry):
    result = profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")

    # The seven-ish answers, and none of the symbols computed from them.
    assert answers(result) == {
        "LOW_LEVEL_OPTIONS": "y",
        "MACH_STM32": "y",
        "MACH_STM32G431": "y",
        "STM32_CLOCK_REF_24M": "y",
        "SCANNER": "y",
        "CARTOGRAPHER_G431_ENABLE": "y",
        "VERSION": '"TESTFW 6.2.0"',
    }
    written = config_text(paths, "carto_v4", "klipper")
    # ...while the file itself carries everything, because that is what make reads.
    assert "CONFIG_FLASH_APPLICATION_ADDRESS=0x8002000" in written
    assert "CONFIG_USBSERIAL=y" in written
    assert "CONFIG_CLOCK_FREQ=170000000" in written


def test_the_usb_and_can_variants_differ_by_one_answer(paths, registry):
    """The property that makes a 138-line file worth reducing: the entire
    difference between a USB build and a CAN build is which interface was
    picked. Everything else follows."""
    usb = profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    with Registry.mutate(paths, "second type") as reg:
        reg.add_type("carto_v4_can", "stm32g431xx")
    can = profiles.apply_seed(paths, "carto_v4_can", "klipper", "config.TestBoardCAN")

    assert set(can.answers) - set(usb.answers) == {"CONFIG_STM32_CANBUS_PA11_PA12=y"}
    assert set(usb.answers) - set(can.answers) == set()

    # And the derived consequences really are different, so the one answer is
    # doing all that work rather than the reduction hiding a difference.
    assert "CONFIG_CANSERIAL=y" in config_text(paths, "carto_v4_can", "klipper")
    assert "CONFIG_USBSERIAL=y" in config_text(paths, "carto_v4", "klipper")


def test_seeding_recomputes_rather_than_copying(paths, registry):
    """A seed is loaded and re-emitted, not copied.

    Proved by adding a symbol to the tree that the vendor's file predates: a
    copy would leave it absent, and `make` would then run olddefconfig over the
    saved answers on the next build. Loading picks it up now.
    """
    kconfig = pathlib.Path(paths.fw_dir("klipper")) / "src" / "Kconfig"
    kconfig.write_text(
        kconfig.read_text(encoding="utf-8")
        + '\nconfig ADDED_LATER\n    bool "Added after the vendor wrote their config"\n'
        "    default y\n",
        encoding="utf-8",
    )
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")

    assert "CONFIG_ADDED_LATER=y" in config_text(paths, "carto_v4", "klipper")
    assert "ADDED_LATER" not in (PROFILE_TREE / "config.TestBoardUSB").read_text(
        encoding="utf-8"
    )


def test_seeding_records_what_it_did(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    record = profiles.read_record(paths, "carto_v4", "klipper")

    assert record is not None
    assert record["profile"] == "config.TestBoardUSB"
    assert record["source_sha256"] and record["config_sha256"]
    # In the data tree, not next to the .config it vouches for.
    assert paths.profile_file("carto_v4", "klipper").startswith(paths.data_dir)


# --------------------------------------------------------------------------
# not overwriting answers we did not write
# --------------------------------------------------------------------------


def test_a_hand_built_config_is_not_overwritten(paths, registry):
    target = pathlib.Path(paths.config_file("carto_v4", "klipper"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("CONFIG_MACH_STM32=y\n# hand-written\n", encoding="utf-8")

    with pytest.raises(ProfileCustomisedError) as exc:
        profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    assert exc.value.data["reason"] == profiles.UNMANAGED
    assert "# hand-written" in target.read_text(encoding="utf-8")


def test_an_edited_profile_config_is_not_overwritten(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    target = pathlib.Path(paths.config_file("carto_v4", "klipper"))
    target.write_text(
        target.read_text(encoding="utf-8").replace("CONFIG_FOR_K1", "# CONFIG_FOR_K1"),
        encoding="utf-8",
    )

    with pytest.raises(ProfileCustomisedError) as exc:
        profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardCAN")
    assert exc.value.data["reason"] == profiles.CUSTOMISED
    assert exc.value.data["profile"] == "config.TestBoardUSB"


def test_force_replaces_it_but_keeps_a_backup(paths, registry):
    target = pathlib.Path(paths.config_file("carto_v4", "klipper"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("CONFIG_MACH_STM32=y\n# hand-written\n", encoding="utf-8")

    result = profiles.apply_seed(
        paths, "carto_v4", "klipper", "config.TestBoardUSB", force=True
    )
    assert result.backup is not None
    assert "# hand-written" in pathlib.Path(result.backup).read_text(encoding="utf-8")
    assert "CONFIG_CARTOGRAPHER_G431_ENABLE=y" in target.read_text(encoding="utf-8")


def test_reseeding_an_untouched_config_needs_no_force(paths, registry):
    """The repeatable case: picking up a vendor bump, or switching variant, on a
    config nobody has edited. Refusing here would make the safe operation the
    one that needs an override."""
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    result = profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardCAN")
    assert "CONFIG_STM32_CANBUS_PA11_PA12=y" in result.answers


# --------------------------------------------------------------------------
# your own profile
# --------------------------------------------------------------------------


def edit(paths: Paths, mcu_type: str, fw: str, old: str, new: str) -> None:
    """Change one answer in a saved config, as menuconfig or an editor would."""
    target = pathlib.Path(paths.config_file(mcu_type, fw))
    text = target.read_text(encoding="utf-8")
    assert old in text, f"{old} is not in the config to change"
    target.write_text(text.replace(old, new), encoding="utf-8")


def customise(paths: Paths, registry: Registry) -> None:
    """Seed from the vendor, then edit one answer - the whole of "it's mine now"."""
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    edit(paths, "carto_v4", "klipper", '"TESTFW 6.2.0"', '"MINE 1.0"')


def test_forcing_over_an_edit_keeps_the_answers_as_your_own_profile(paths, registry):
    """The guard that turns `force` from data loss into a switch.

    An edit made out of band - menuconfig over SSH, an editor in Mainsail - has
    never been through a capture, and this is the last moment anyone can save it.
    """
    customise(paths, registry)

    result = profiles.apply_seed(
        paths, "carto_v4", "klipper", "config.TestBoardCAN", force=True
    )

    assert result.kept == profiles.CUSTOM_PROFILE
    own = profiles.read_custom(paths, "carto_v4", "klipper")
    assert own is not None
    assert own.parent == "config.TestBoardUSB"
    assert 'CONFIG_VERSION="MINE 1.0"' in pathlib.Path(own.path).read_text(
        encoding="utf-8"
    )
    # And the vendor's answers really did land.
    assert "CONFIG_STM32_CANBUS_PA11_PA12=y" in config_text(paths, "carto_v4", "klipper")


def test_your_own_profile_is_offered_beside_the_vendors(paths, registry):
    customise(paths, registry)
    profiles.capture_custom(paths, "carto_v4", "klipper", parent="config.TestBoardUSB")

    offered = profiles.available(paths, "klipper", mcu_type="carto_v4")
    assert offered[0].name == profiles.CUSTOM_PROFILE
    assert offered[0].origin == profiles.ORIGIN_CUSTOM
    assert [s.name for s in offered[1:]] == sorted(SEEDS)

    # Without a type there is no custom slot to resolve - "what does this tree
    # ship" is still a question worth asking on its own.
    assert profiles.CUSTOM_PROFILE not in [
        s.name for s in profiles.available(paths, "klipper")
    ]


def test_switching_back_and_forth_is_lossless(paths, registry):
    """The lifecycle in one test: track, edit, go to the vendor's, come back."""
    customise(paths, registry)
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardCAN", force=True)
    assert 'CONFIG_VERSION="MINE 1.0"' not in config_text(paths, "carto_v4", "klipper")

    back = profiles.apply_seed(paths, "carto_v4", "klipper", profiles.CUSTOM_PROFILE)
    assert 'CONFIG_VERSION="MINE 1.0"' in config_text(paths, "carto_v4", "klipper")
    assert back.profile == profiles.CUSTOM_PROFILE

    state = profiles.status(paths, "carto_v4", "klipper")
    assert state.reason is None
    assert state.custom
    assert state.parent == "config.TestBoardUSB"
    assert state.label == "Your own profile"


def test_going_back_to_your_own_answers_does_not_capture_over_them(paths, registry):
    """"Discard my edits" must not first save the edits it is discarding.

    Capturing here would overwrite the file about to be read, making the revert
    a no-op that silently keeps exactly what the user asked to throw away.
    """
    customise(paths, registry)
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardCAN", force=True)
    profiles.apply_seed(paths, "carto_v4", "klipper", profiles.CUSTOM_PROFILE)

    edit(paths, "carto_v4", "klipper", '"MINE 1.0"', '"SCRATCH 9.9"')
    result = profiles.apply_seed(
        paths, "carto_v4", "klipper", profiles.CUSTOM_PROFILE, force=True
    )

    assert result.kept is None
    own = pathlib.Path(profiles.read_custom(paths, "carto_v4", "klipper").path)
    assert 'CONFIG_VERSION="MINE 1.0"' in own.read_text(encoding="utf-8")
    assert "SCRATCH" not in own.read_text(encoding="utf-8")
    assert 'CONFIG_VERSION="MINE 1.0"' in config_text(paths, "carto_v4", "klipper")


def test_a_recapture_keeps_the_original_fork_point(paths, registry):
    """Editing your own profile *again* must not record "forked from myself".

    By then the record beside it names the custom profile, so nothing else on
    disk remembers the vendor entry this was forked from - and losing it loses
    both the "back to CartoV4USB" button and the baseline that says what
    changed, on the second edit, when nobody is watching.
    """
    customise(paths, registry)
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardCAN", force=True)
    profiles.apply_seed(paths, "carto_v4", "klipper", profiles.CUSTOM_PROFILE)
    edit(paths, "carto_v4", "klipper", '"MINE 1.0"', '"MINE 2.0"')

    again = profiles.capture_custom(
        paths, "carto_v4", "klipper", parent=profiles.CUSTOM_PROFILE
    )
    assert again.parent == "config.TestBoardUSB"
    assert profiles.answer_map(again.base)["VERSION"] == '"TESTFW 6.2.0"'
    assert profiles.answer_map(profiles.answer_lines(again.path))["VERSION"] == '"MINE 2.0"'


def test_your_profile_is_never_written_into_the_vendors_tree(paths, registry):
    """A `git pull` in the vendor's fork must not be able to eat it, and this
    tool has no business writing into somebody else's working copy."""
    customise(paths, registry)
    own = profiles.capture_custom(paths, "carto_v4", "klipper")

    assert not own.path.startswith(paths.fw_dir("klipper"))
    assert list(pathlib.Path(paths.fw_dir("klipper")).glob("config.*")) != []
    assert not (pathlib.Path(paths.fw_dir("klipper")) / profiles.CUSTOM_PROFILE).exists()


def test_your_profile_is_kept_where_backups_look(paths, registry):
    """The one file here that cannot be regenerated.

    Once the `.config` it came from has been reseeded from a vendor profile,
    this is the only copy of the answers the user wrote - so it belongs with the
    hand-edited, backed-up things rather than beside the build artifacts, which
    exist to be thrown away and rebuilt.
    """
    customise(paths, registry)
    own = profiles.capture_custom(paths, "carto_v4", "klipper")

    assert own.path.startswith(paths.config_dir)
    assert not own.path.startswith(paths.data_dir)
    # Beside the file it was captured from, which is also what puts it in
    # Mainsail's editor next to the config it describes.
    assert os.path.dirname(own.path) == paths.type_dir("carto_v4")


def test_a_vendor_shipping_the_reserved_name_is_shadowed(paths, registry):
    """Two entries with one name is a picker where a click is ambiguous."""
    (pathlib.Path(paths.fw_dir("klipper")) / profiles.CUSTOM_PROFILE).write_text(
        "CONFIG_MACH_STM32=y\n", encoding="utf-8"
    )
    customise(paths, registry)
    profiles.capture_custom(paths, "carto_v4", "klipper")

    offered = profiles.available(paths, "klipper", mcu_type="carto_v4")
    named = [s for s in offered if s.name == profiles.CUSTOM_PROFILE]
    assert len(named) == 1
    assert named[0].origin == profiles.ORIGIN_CUSTOM


# --------------------------------------------------------------------------
# telling profiles apart, and saying what you changed
# --------------------------------------------------------------------------


def test_only_the_answers_that_differ_distinguish_a_profile(paths, registry):
    rows = profiles.distinguishing(profiles.available(paths, "klipper"))
    usb = {row["symbol"]: row["value"] for row in rows["config.TestBoardUSB"]}

    # Every one of these is answered identically by all four, so listing it under
    # each entry would bury the ones that decide anything.
    for shared in ("LOW_LEVEL_OPTIONS", "MACH_STM32", "MACH_STM32G431"):
        assert shared not in usb
    assert usb["STM32_CANBUS_PA11_PA12"] == "n"
    assert usb["STM32_USB_PA11_PA12"] == "y"

    can = {row["symbol"]: row["value"] for row in rows["config.TestBoardCAN"]}
    slow = {row["symbol"]: row["value"] for row in rows["config.TestBoardSlowCAN"]}
    assert (can["CANBUS_FREQUENCY"], slow["CANBUS_FREQUENCY"]) == ("1000000", "250000")


def test_a_profile_merely_not_mentioning_a_symbol_distinguishes_nothing(paths, registry):
    """Vendor seeds are hand-maintained and carry computed lines inconsistently
    - `SCANNER` is in two of these four and `USBSERIAL` in two others. Treating
    "not mentioned" as a value makes every entry differ from every other in a
    dozen places, which is the noise this exists to remove."""
    rows = profiles.distinguishing(profiles.available(paths, "klipper"))
    usb = {row["symbol"]: row["value"] for row in rows["config.TestBoardUSB"]}
    big = {row["symbol"]: row["value"] for row in rows["config.TestBoardBigLoader"]}

    # Answered by USB and CAN, identically, and simply absent from the other two.
    assert "SCANNER" not in usb and "SCANNER" not in big
    # And an entry never carries a row for an answer it does not give.
    assert "CANBUS_FREQUENCY" not in usb
    assert all(row["value"] is not None for rows_ in rows.values() for row in rows_)


def test_a_lone_profile_is_distinguished_by_nothing(paths, registry):
    only = [profiles.find(paths, "klipper", "config.TestBoardUSB")]
    assert profiles.distinguishing(only) == {"config.TestBoardUSB": []}


def test_what_you_changed_from_the_vendors_answers(paths, registry):
    assert profiles.overrides(paths, "carto_v4", "klipper") == []

    customise(paths, registry)
    profiles.capture_custom(paths, "carto_v4", "klipper", parent="config.TestBoardUSB")

    changed = profiles.overrides(paths, "carto_v4", "klipper")
    assert [row["symbol"] for row in changed] == ["VERSION"]
    assert changed[0]["was"] == '"TESTFW 6.2.0"'
    assert changed[0]["now"] == '"MINE 1.0"'


def test_the_baseline_is_the_parents_own_answers_not_its_file(paths, registry):
    """A vendor seed is hand-maintained and carries computed lines a minimal
    capture omits - `USBSERIAL`, `CLOCK_FREQ`, every architecture not chosen.
    Diffing against the file itself reports a dozen changes nobody made."""
    customise(paths, registry)
    own = profiles.capture_custom(paths, "carto_v4", "klipper")

    vendor = profiles.answer_map(
        profiles.answer_lines(profiles.find(paths, "klipper", "config.TestBoardUSB").path)
    )
    assert "USBSERIAL" in vendor
    assert "USBSERIAL" not in profiles.answer_map(own.base)


def test_what_you_changed_survives_switching_away_and_back(paths, registry):
    """The state the baseline has to be kept in the file for: once you are on
    your own profile, the record beside it describes *that*, so the parent's
    answers are no longer anywhere else to be found."""
    customise(paths, registry)
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardCAN", force=True)
    profiles.apply_seed(paths, "carto_v4", "klipper", profiles.CUSTOM_PROFILE)

    changed = profiles.overrides(paths, "carto_v4", "klipper")
    assert [row["symbol"] for row in changed] == ["VERSION"]
    assert changed[0]["was"] == '"TESTFW 6.2.0"'


# --------------------------------------------------------------------------
# taking a vendor bump, at build time
# --------------------------------------------------------------------------


def bump(paths: Paths, name: str = "config.TestBoardUSB") -> None:
    seed = pathlib.Path(paths.fw_dir("klipper")) / name
    seed.write_text(
        seed.read_text(encoding="utf-8").replace("6.2.0", "6.3.0"), encoding="utf-8"
    )


def test_a_build_takes_the_bump_when_the_config_is_still_ours(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    bump(paths)

    said: list[str] = []
    assert (
        profiles.reseed_if_moved(paths, "carto_v4", "klipper", log=said.append)
        == "config.TestBoardUSB"
    )
    assert "6.3.0" in config_text(paths, "carto_v4", "klipper")
    assert said and "config.TestBoardUSB" in said[0]


def test_a_build_leaves_your_own_answers_alone(paths, registry):
    """Both can be true at once. The one that decides what may happen is the
    local edit, and reseeding over it is what would lose work."""
    customise(paths, registry)
    bump(paths)

    assert profiles.reseed_if_moved(paths, "carto_v4", "klipper") is None
    assert 'CONFIG_VERSION="MINE 1.0"' in config_text(paths, "carto_v4", "klipper")


def test_a_build_with_nothing_to_take_changes_nothing(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    before = config_text(paths, "carto_v4", "klipper")

    assert profiles.reseed_if_moved(paths, "carto_v4", "klipper") is None
    assert config_text(paths, "carto_v4", "klipper") == before


def test_the_cli_build_agrees_with_the_panel(paths, registry, trees, monkeypatch, capsys):
    """The other end of "one rule": `updatefw build` takes the bump by default
    and `--no-reseed` declines it, without either being implemented here."""
    from mcu_updater import cli

    monkeypatch.setenv("MCU_UPDATER_HOME", str(trees))
    monkeypatch.delenv("MCU_UPDATER_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MCU_UPDATER_DATA_DIR", raising=False)
    pathlib.Path(paths.main_config).write_text(
        pathlib.Path(paths.main_config).read_text(encoding="utf-8")
        + "\n[updater]\ndry_run: true\nservice_backend: null\n",
        encoding="utf-8",
    )
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    bump(paths)

    cli.main(["build", "-t", "carto_v4", "-f", "klipper", "--no-reseed"])
    assert "6.2.0" in config_text(paths, "carto_v4", "klipper")

    cli.main(["build", "-t", "carto_v4", "-f", "klipper"])
    assert "6.3.0" in config_text(paths, "carto_v4", "klipper")
    assert "config.TestBoardUSB" in capsys.readouterr().out


def test_a_bumped_application_re_derives_the_bootloader_rather_than_seeding_it(
    paths, registry
):
    """Katapult has no seed file of its own. Its "seed" is the application's
    config, so taking the bump means deriving again - and deriving is what runs
    the offset check that keeps the pair bootable."""
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    profiles.derive_bootloader(paths, "carto_v4", "klipper")
    bump(paths)
    profiles.reseed_if_moved(paths, "carto_v4", "klipper")

    assert (
        profiles.reseed_if_moved(paths, "carto_v4", "katapult") == "derived:klipper"
    )
    assert profiles.status(paths, "carto_v4", "katapult").reason is None


# --------------------------------------------------------------------------
# drift
# --------------------------------------------------------------------------


def test_an_unseeded_type_reads_as_unmanaged_and_not_as_a_problem(paths, registry):
    state = profiles.status(paths, "carto_v4", "klipper")
    assert state.reason == profiles.UNMANAGED
    assert state.managed is False
    # Every type predating profiles is in this state; painting them amber would
    # be noise about a thing that is not wrong.
    assert state.tone == "ok"


def test_a_seeded_config_reads_as_clean(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    state = profiles.status(paths, "carto_v4", "klipper")
    assert state.reason is None
    assert state.profile == "config.TestBoardUSB"
    assert state.tone == "ok"


def test_editing_the_config_becomes_visible(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    target = pathlib.Path(paths.config_file("carto_v4", "klipper"))
    target.write_text(
        target.read_text(encoding="utf-8") + "CONFIG_ADDED_BY_HAND=y\n", encoding="utf-8"
    )

    state = profiles.status(paths, "carto_v4", "klipper")
    assert state.reason == profiles.CUSTOMISED
    # An OK tone, not an amber one: your own answers are a destination, and the
    # capture that keeps them is what makes that true.
    assert state.tone == "ok"


def test_a_vendor_bump_reads_as_a_moved_seed(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    seed = pathlib.Path(paths.fw_dir("klipper")) / "config.TestBoardUSB"
    seed.write_text(
        seed.read_text(encoding="utf-8").replace("6.2.0", "6.3.0"), encoding="utf-8"
    )

    state = profiles.status(paths, "carto_v4", "klipper")
    assert state.reason == profiles.SEED_MOVED
    assert state.tone == "attention"


def test_a_local_edit_outranks_a_vendor_bump(paths, registry):
    """Both can be true at once. The one that changes what a caller may safely
    do is the local edit, because reseeding over it is what loses work."""
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    seed = pathlib.Path(paths.fw_dir("klipper")) / "config.TestBoardUSB"
    seed.write_text(
        seed.read_text(encoding="utf-8").replace("6.2.0", "6.3.0"), encoding="utf-8"
    )
    target = pathlib.Path(paths.config_file("carto_v4", "klipper"))
    target.write_text(
        target.read_text(encoding="utf-8") + "CONFIG_ADDED_BY_HAND=y\n", encoding="utf-8"
    )

    assert profiles.status(paths, "carto_v4", "klipper").reason == profiles.CUSTOMISED


def test_a_lost_record_degrades_to_unmanaged(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    pathlib.Path(paths.profile_file("carto_v4", "klipper")).write_text(
        "not json", encoding="utf-8"
    )
    assert profiles.status(paths, "carto_v4", "klipper").reason == profiles.UNMANAGED


def test_forget_detaches_without_touching_the_config(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    before = config_text(paths, "carto_v4", "klipper")

    assert profiles.forget(paths, "carto_v4", "klipper") is True
    assert profiles.status(paths, "carto_v4", "klipper").reason == profiles.UNMANAGED
    assert config_text(paths, "carto_v4", "klipper") == before


# --------------------------------------------------------------------------
# deriving the bootloader
# --------------------------------------------------------------------------


def test_deriving_carries_the_board_answers_and_drops_the_application_ones(
    paths, registry
):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    result = profiles.derive_bootloader(paths, "carto_v4", "klipper")

    carried = {profiles.parse_answer(line)[0] for line in result.carried}  # type: ignore[index]
    dropped = {profiles.parse_answer(line)[0] for line in result.dropped}  # type: ignore[index]

    # The board: architecture, model, crystal.
    assert {"MACH_STM32", "MACH_STM32G431", "STM32_CLOCK_REF_24M"} <= carried
    # The application: what it scans with, and what it calls itself.
    assert {"SCANNER", "CARTOGRAPHER_G431_ENABLE", "VERSION"} <= dropped
    assert carried & dropped == set()


def test_the_bootloader_config_is_written_and_recorded_as_derived(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    result = profiles.derive_bootloader(paths, "carto_v4", "klipper")

    assert result.profile == "derived:klipper"
    assert "CONFIG_MACH_STM32G431=y" in config_text(paths, "carto_v4", "katapult")
    assert profiles.status(paths, "carto_v4", "katapult").reason is None


def test_the_can_interface_is_carried_too(paths, registry):
    """Not just the chip. A bootloader built for USB on a CAN-wired board is
    simply absent from the bus, and nothing else in the pair would say so."""
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardCAN")
    profiles.derive_bootloader(paths, "carto_v4", "klipper")
    assert "CONFIG_STM32_CANBUS_PA11_PA12=y" in config_text(paths, "carto_v4", "katapult")


def test_an_answer_the_target_tree_will_not_take_is_reported_as_dropped(paths, registry):
    """"We wrote it into the file" is not "it took".

    kconfiglib remembers a user value it cannot apply - here a CAN speed
    carried without the CAN interface it depends on - and goes on reporting the
    default. Reading the value back is the only thing that can tell the
    difference, and an answer silently landing in `carried` when it did not
    apply is a config that claims to describe a board it does not.

    Exercised directly because the two filters overlap: `_partition` already
    drops everything the tree does not define, so through `derive_bootloader`
    each guard hides the other's absence.
    """
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardSlowCAN")
    result = profiles.derive_bootloader(paths, "carto_v4", "klipper")

    assert "CONFIG_CANBUS_FREQUENCY=250000" in result.dropped
    assert "CONFIG_CANBUS_FREQUENCY=250000" not in result.carried
    # The interface itself carried fine, so this is about the value and not
    # about the symbol being unknown here.
    assert "CONFIG_STM32_CANBUS_PA11_PA12=y" in result.carried
    assert "CONFIG_CANBUS_FREQUENCY=1000000" in config_text(paths, "carto_v4", "katapult")


def test_a_disagreeing_offset_is_refused(paths, registry):
    """The one invariant. Both configs are individually valid and both would
    build; together they produce a board that does not come back."""
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardBigLoader")

    with pytest.raises(OffsetMismatchError) as exc:
        profiles.derive_bootloader(paths, "carto_v4", "klipper")

    assert exc.value.data["app_address"] == "0x8008000"
    assert exc.value.data["launch_address"] == "0x8002000"
    # And nothing was written, so there is no half-derived config to flash.
    assert not pathlib.Path(paths.config_file("carto_v4", "katapult")).exists()


def test_a_check_that_cannot_run_is_refused_rather_than_skipped(paths, registry):
    """A missing symbol on one side turns the offset check into a no-op that
    still reads as verified. That is worse than having no check."""
    kconfig = pathlib.Path(paths.fw_dir("katapult")) / "src" / "Kconfig"
    kconfig.write_text(
        kconfig.read_text(encoding="utf-8").replace("config LAUNCH_APP_ADDRESS", "config UNUSED_ADDR"),
        encoding="utf-8",
    )
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")

    with pytest.raises(OffsetMismatchError) as exc:
        profiles.derive_bootloader(paths, "carto_v4", "klipper")
    assert "LAUNCH_APP_ADDRESS" in str(exc.value)


def test_deriving_needs_the_application_config_first(paths, registry):
    with pytest.raises(ProfileError) as exc:
        profiles.derive_bootloader(paths, "carto_v4", "klipper")
    assert "nothing to derive" in str(exc.value)


def test_deriving_will_not_overwrite_a_hand_built_bootloader_config(paths, registry):
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    target = pathlib.Path(paths.config_file("carto_v4", "katapult"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("CONFIG_MACH_STM32=y\n# mine\n", encoding="utf-8")

    with pytest.raises(ProfileCustomisedError):
        profiles.derive_bootloader(paths, "carto_v4", "klipper")
    assert "# mine" in target.read_text(encoding="utf-8")


def test_reseeding_the_application_makes_the_derived_config_stale(paths, registry):
    """A derivation is a function of the application's config, so that config
    changing is the bootloader's equivalent of a vendor bump."""
    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    profiles.derive_bootloader(paths, "carto_v4", "klipper")
    assert profiles.status(paths, "carto_v4", "katapult").reason is None

    profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardCAN")
    assert profiles.status(paths, "carto_v4", "katapult").reason == profiles.SEED_MOVED


# --------------------------------------------------------------------------
# answer parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("CONFIG_MACH_STM32=y", ("MACH_STM32", "y")),
        ("CONFIG_VERSION=\"CARTOGRAPHER 6.2.0\"", ("VERSION", '"CARTOGRAPHER 6.2.0"')),
        ("CONFIG_CANBUS_FREQUENCY=1000000", ("CANBUS_FREQUENCY", "1000000")),
        # The form a minimal config uses for a bool whose default is y. Treating
        # it as a comment would carry the symbol across at the wrong value.
        ("# CONFIG_FOR_K1 is not set", ("FOR_K1", "n")),
        ("  # CONFIG_FOR_K1 is not set  ", ("FOR_K1", "n")),
        ("# Automatically generated file; DO NOT EDIT.", None),
        ("", None),
        ("MACH_STM32=y", None),
    ],
)
def test_answer_lines_are_read_both_ways_round(line, expected):
    assert profiles.parse_answer(line) == expected


# --------------------------------------------------------------------------
# a vendor fork, which is the case this exists for
# --------------------------------------------------------------------------


def test_a_declared_family_seeds_from_its_own_tree(tmp_path, trees):
    """Cartographer's shape: an application family whose tree is neither named
    after it nor a sibling of klipper's."""
    make_tree(trees, "MCU-Firmware---Based-on-Klipper", "app.Kconfig", seeds=True)
    paths = Paths.from_env(env={"MCU_UPDATER_HOME": str(trees)})
    config = pathlib.Path(paths.main_config)
    config.write_text(
        "[firmware cartographer]\n"
        "source: ~/MCU-Firmware---Based-on-Klipper\n"
        "artifact: klipper\n\n"
        "[type carto_v4]\n"
        "chipset: stm32g431xx\n"
        "firmware: cartographer\n"
        "profile: config.TestBoardUSB\n",
        encoding="utf-8",
    )

    reg = Registry.load(paths)
    mcu = reg.get("carto_v4")
    assert mcu.application() == "cartographer"
    assert mcu.profile == "config.TestBoardUSB"

    families = firmware.load(paths)
    result = profiles.apply_seed(
        paths, "carto_v4", "cartographer", mcu.profile, families=families
    )
    assert "CONFIG_CARTOGRAPHER_G431_ENABLE=y" in result.answers
    assert result.config_path == paths.config_file("carto_v4", "cartographer")

    # And katapult is derived from it, across two differently-named trees.
    derived = profiles.derive_bootloader(
        paths, "carto_v4", "cartographer", families=families
    )
    assert derived.app_address == 0x8002000
    assert "CONFIG_MACH_STM32G431=y" in config_text(paths, "carto_v4", "katapult")


def test_the_profile_key_round_trips_through_the_registry(paths, registry):
    with Registry.mutate(paths, "set profile") as reg:
        reg.get("carto_v4").profile = "config.TestBoardUSB"
    assert Registry.load(paths).get("carto_v4").profile == "config.TestBoardUSB"

    with Registry.mutate(paths, "clear profile") as reg:
        reg.get("carto_v4").profile = ""
    assert Registry.load(paths).get("carto_v4").profile == ""
    assert "profile" not in pathlib.Path(paths.main_config).read_text(encoding="utf-8")


def test_a_record_survives_a_round_trip_as_json(paths, registry):
    result = profiles.apply_seed(paths, "carto_v4", "klipper", "config.TestBoardUSB")
    on_disk = json.loads(
        pathlib.Path(paths.profile_file("carto_v4", "klipper")).read_text(encoding="utf-8")
    )
    assert on_disk["answers"] == result.answers
    assert on_disk["profile"] == result.profile
