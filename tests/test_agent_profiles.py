"""`fw.profile.*` - seeding a type's answers from the browser.

The panel's side of :mod:`mcu_updater.profiles`. What is tested here is the
wiring and the defaults, not the seeding itself - test_profiles.py owns that.

Two defaults carry the weight, and both are choices about what happens when a
caller says nothing: the bootloader is derived unless asked otherwise, and a
config nobody seeded is not overwritten unless asked twice.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest

from mcu_updater import profiles
from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import RpcError
from mcu_updater.config import Registry
from mcu_updater.paths import Paths

from .test_profiles import PROFILE_TREE, SEEDS, make_tree


@pytest.fixture
def api(tmp_path) -> Api:
    make_tree(tmp_path, "klipper", "app.Kconfig", seeds=True)
    make_tree(tmp_path, "katapult", "boot.Kconfig", seeds=False)
    (tmp_path / "printer_data" / "config" / "mcu-updater").mkdir(parents=True)
    (tmp_path / "printer_data" / "mcu-updater").mkdir(parents=True)
    paths = Paths.from_env(env={"MCU_UPDATER_HOME": str(tmp_path)})
    with Registry.mutate(paths, "test setup") as reg:
        reg.add_type("carto_v4", "stm32g431xx")
    return Api(paths)


def config_text(api: Api, fw: str) -> str:
    return pathlib.Path(api.paths.config_file("carto_v4", fw)).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------


def test_listing_answers_for_a_type_not_for_a_tree(api):
    out = api.dispatch("fw.profile.list", {"name": "carto_v4"})

    assert out["firmware"] == "klipper"
    assert [s["name"] for s in out["available"]] == sorted(SEEDS)
    # Both families the type uses, each with its own verdict.
    assert set(out["state"]) == {"klipper", "katapult"}
    assert out["state"]["klipper"]["reason"] == profiles.UNMANAGED


def test_listing_an_unknown_type_is_refused(api):
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.profile.list", {"name": "nope"})
    assert exc.value.data["code"] == "unknown_type"


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------


def test_applying_seeds_the_application_and_derives_the_bootloader(api):
    """The default. Seeding only the application leaves a type whose two
    configs describe different boards, and they only have to disagree about one
    address for the pair to produce something that does not come back."""
    out = api.dispatch(
        "fw.profile.apply", {"name": "carto_v4", "profile": "config.TestBoardUSB"}
    )

    assert out["applied"]["fw"] == "klipper"
    assert out["derived"]["fw"] == "katapult"
    assert out["derived"]["profile"] == "derived:klipper"
    assert "CONFIG_MACH_STM32G431=y" in config_text(api, "klipper")
    assert "CONFIG_MACH_STM32G431=y" in config_text(api, "katapult")


def test_the_intent_is_recorded_in_the_hand_edited_config(api):
    api.dispatch("fw.profile.apply", {"name": "carto_v4", "profile": "config.TestBoardUSB"})

    assert Registry.load(api.paths).get("carto_v4").profile == "config.TestBoardUSB"
    # ...and only for the application. Katapult's is always derived, so a second
    # key would restate that rather than record anything.
    text = pathlib.Path(api.paths.main_config).read_text(encoding="utf-8")
    assert text.count("profile:") == 1


def test_deriving_can_be_declined(api):
    out = api.dispatch(
        "fw.profile.apply",
        {"name": "carto_v4", "profile": "config.TestBoardUSB", "derive": False},
    )
    assert out["derived"] is None
    assert not pathlib.Path(api.paths.config_file("carto_v4", "katapult")).exists()


def test_a_board_with_no_bootloader_derives_nothing(api):
    with Registry.mutate(api.paths, "no katapult") as reg:
        reg.add_type("bare", "stm32g431xx", katapult_installed=False)

    out = api.dispatch("fw.profile.apply", {"name": "bare", "profile": "config.TestBoardUSB"})
    assert out["derived"] is None


def test_a_failed_derivation_is_not_reported_as_success(api):
    """A bootloader that cannot be derived is a board that should not be
    flashed. Returning the application seeding with a warning attached is how
    that gets missed."""
    with pytest.raises(RpcError) as exc:
        api.dispatch(
            "fw.profile.apply", {"name": "carto_v4", "profile": "config.TestBoardBigLoader"}
        )

    assert exc.value.data["code"] == "offset_mismatch"
    assert exc.value.data["data"]["app_address"] == "0x8008000"
    # The application's config is valid on its own and stays; the bootloader's
    # was never written.
    assert pathlib.Path(api.paths.config_file("carto_v4", "klipper")).exists()
    assert not pathlib.Path(api.paths.config_file("carto_v4", "katapult")).exists()


def test_an_unknown_profile_is_refused_with_the_real_list(api):
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.profile.apply", {"name": "carto_v4", "profile": "config.Nope"})

    assert exc.value.data["code"] == "profile_not_found"
    assert exc.value.data["data"]["available"] == sorted(SEEDS)


def test_a_traversing_profile_name_is_refused(api):
    """The name arrives from a browser and is joined onto a source tree path."""
    secret = pathlib.Path(api.paths.home) / "secret.txt"
    secret.write_text("CONFIG_MACH_STM32=y\n", encoding="utf-8")

    with pytest.raises(RpcError) as exc:
        api.dispatch(
            "fw.profile.apply", {"name": "carto_v4", "profile": "../secret.txt"}
        )
    assert exc.value.data["code"] == "profile"


def test_existing_answers_are_not_overwritten_without_force(api):
    target = pathlib.Path(api.paths.config_file("carto_v4", "klipper"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("CONFIG_MACH_STM32=y\n# mine\n", encoding="utf-8")

    with pytest.raises(RpcError) as exc:
        api.dispatch(
            "fw.profile.apply", {"name": "carto_v4", "profile": "config.TestBoardUSB"}
        )
    assert exc.value.data["code"] == "profile_customised"
    assert "# mine" in target.read_text(encoding="utf-8")

    out = api.dispatch(
        "fw.profile.apply",
        {"name": "carto_v4", "profile": "config.TestBoardUSB", "force": True},
    )
    assert out["applied"]["backup"] is not None


# --------------------------------------------------------------------------
# drift, where the panel sees it
# --------------------------------------------------------------------------


def test_the_artifact_payload_carries_the_profile_verdict(api):
    api.dispatch("fw.profile.apply", {"name": "carto_v4", "profile": "config.TestBoardUSB"})
    assert api.artifact("carto_v4", "klipper")["profile"]["reason"] is None

    target = pathlib.Path(api.paths.config_file("carto_v4", "klipper"))
    target.write_text(target.read_text(encoding="utf-8") + "CONFIG_X=y\n", encoding="utf-8")

    verdict = api.artifact("carto_v4", "klipper")["profile"]
    assert verdict["reason"] == profiles.CUSTOMISED
    assert verdict["label"] == "Customised"
    # A customised config is not a stale artifact and does not want a rebuild -
    # which is why this is a third verdict rather than folded into `reason`.
    assert api.artifact("carto_v4", "klipper")["reason"] != profiles.CUSTOMISED


def test_an_unmanaged_type_is_not_painted_as_a_problem(api):
    """Every type predating profiles is in this state."""
    verdict = api.artifact("carto_v4", "klipper")["profile"]
    assert verdict["managed"] is False
    assert verdict["tone"] == "ok"


def test_forgetting_detaches_without_touching_the_answers(api):
    api.dispatch("fw.profile.apply", {"name": "carto_v4", "profile": "config.TestBoardUSB"})
    before = config_text(api, "klipper")

    out = api.dispatch("fw.profile.forget", {"name": "carto_v4"})

    assert set(out["forgotten"]) == {"klipper", "katapult"}
    assert config_text(api, "klipper") == before
    assert Registry.load(api.paths).get("carto_v4").profile == ""
    assert api.artifact("carto_v4", "klipper")["profile"]["managed"] is False


def test_a_vendor_bump_shows_up_as_something_to_do(api):
    api.dispatch("fw.profile.apply", {"name": "carto_v4", "profile": "config.TestBoardUSB"})
    seed = pathlib.Path(api.paths.fw_dir("klipper")) / "config.TestBoardUSB"
    seed.write_text(seed.read_text(encoding="utf-8").replace("6.2.0", "6.3.0"), encoding="utf-8")

    verdict = api.artifact("carto_v4", "klipper")["profile"]
    assert verdict["reason"] == profiles.SEED_MOVED
    assert verdict["tone"] == "attention"


def test_the_methods_are_advertised(api):
    """Not job methods and not flash methods: seeding writes a config file, in
    the same category as saving from the kconfig editor, and a read-only
    deployment still has answers worth looking at."""
    advertised = api.available_methods()
    assert {"fw.profile.list", "fw.profile.apply", "fw.profile.forget"} <= set(advertised)


def test_seeding_a_cartographer_fork_through_the_agent(tmp_path):
    """End to end on the shape this exists for: a declared family, a tree named
    after neither, and katapult derived across the two."""
    make_tree(tmp_path, "MCU-Firmware---Based-on-Klipper", "app.Kconfig", seeds=True)
    make_tree(tmp_path, "katapult", "boot.Kconfig", seeds=False)
    (tmp_path / "printer_data" / "config" / "mcu-updater").mkdir(parents=True)
    (tmp_path / "printer_data" / "mcu-updater").mkdir(parents=True)
    paths = Paths.from_env(env={"MCU_UPDATER_HOME": str(tmp_path)})
    pathlib.Path(paths.main_config).write_text(
        "[firmware cartographer]\n"
        "source: ~/MCU-Firmware---Based-on-Klipper\n"
        "artifact: klipper\n\n"
        "[mcu carto_v4]\n"
        "chipset: stm32g431xx\n"
        "firmware: cartographer\n",
        encoding="utf-8",
    )
    api = Api(paths)

    listing = api.dispatch("fw.profile.list", {"name": "carto_v4"})
    assert listing["firmware"] == "cartographer"
    assert [s["name"] for s in listing["available"]] == sorted(SEEDS)

    out = api.dispatch(
        "fw.profile.apply", {"name": "carto_v4", "profile": "config.TestBoardCAN"}
    )
    assert out["applied"]["fw"] == "cartographer"
    assert out["derived"]["app_address"] == 0x8002000

    # The probe-specific answers stay in the application; the board answers
    # reach the bootloader.
    app = pathlib.Path(paths.config_file("carto_v4", "cartographer")).read_text(
        encoding="utf-8"
    )
    boot = pathlib.Path(paths.config_file("carto_v4", "katapult")).read_text(
        encoding="utf-8"
    )
    assert "CONFIG_CARTOGRAPHER_G431_ENABLE=y" in app
    assert "CARTOGRAPHER" not in boot
    assert "CONFIG_STM32_CANBUS_PA11_PA12=y" in boot


def test_a_tree_that_ships_no_profiles_lists_none_rather_than_failing(api):
    for seed in SEEDS:
        (pathlib.Path(api.paths.fw_dir("klipper")) / seed).unlink()
    out = api.dispatch("fw.profile.list", {"name": "carto_v4"})
    assert out["available"] == []


def test_a_missing_tree_does_not_break_the_listing(api):
    shutil.rmtree(api.paths.fw_dir("klipper"))
    out = api.dispatch("fw.profile.list", {"name": "carto_v4"})
    assert out["available"] == []


def test_the_fixture_tree_is_the_one_the_tests_describe():
    """Guards the fixtures themselves: every seed named in SEEDS exists, and
    the two Kconfig trees are genuinely different files."""
    for seed in SEEDS:
        assert (PROFILE_TREE / seed).is_file()
    app = (PROFILE_TREE / "app.Kconfig").read_text(encoding="utf-8")
    boot = (PROFILE_TREE / "boot.Kconfig").read_text(encoding="utf-8")
    # Declarations, not mentions - both files talk about the other's symbols in
    # their comments, and only a `config` line defines one.
    assert "config SCANNER" in app and "config SCANNER" not in boot
    assert "config LAUNCH_APP_ADDRESS" in boot
    assert "config LAUNCH_APP_ADDRESS" not in app
    assert "config FLASH_APPLICATION_ADDRESS" in app
    assert "config FLASH_APPLICATION_ADDRESS" not in boot
