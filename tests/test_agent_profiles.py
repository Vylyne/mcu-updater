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
from mcu_updater.jobs import JobRunner
from mcu_updater.paths import Paths
from mcu_updater.settings import Settings

from .conftest import write_settings
from .test_profiles import PROFILE_TREE, SEEDS, make_tree


def _api_for(paths: Paths) -> Api:
    """An agent with a runner, because seeding is a job.

    Three Kconfig parses at a few hundred milliseconds each, against a rule that
    every method answers in well under a second - Moonraker awaits with no
    timeout, so a slow method holds a browser's request open.
    """
    return Api(paths, runner=JobRunner(paths, Settings))


@pytest.fixture
def api(tmp_path) -> Api:
    make_tree(tmp_path, "klipper", "app.Kconfig", seeds=True)
    make_tree(tmp_path, "katapult", "boot.Kconfig", seeds=False)
    (tmp_path / "printer_data" / "config" / "mcu-updater").mkdir(parents=True)
    (tmp_path / "printer_data" / "mcu-updater").mkdir(parents=True)
    paths = Paths.from_env(env={"MCU_UPDATER_HOME": str(tmp_path)})
    with Registry.mutate(paths, "test setup") as reg:
        reg.add_type("carto_v4", "stm32g431xx")
    return _api_for(paths)


def apply(api: Api, **params):
    """Seed, wait, and hand back the result the call used to return directly."""
    job = _run_apply(api, **params)
    assert job.state == "succeeded", job.error
    return job.result


def _run_apply(api: Api, **params):
    res = api.dispatch("fw.profile.apply", params)
    assert api.runner.wait(timeout=60)
    return api.runner.get(res["job_id"])


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


def test_each_profile_carries_what_tells_it_apart(api):
    """Eight entries whose seven answers are six the same is a picker that hides
    the line deciding anything. This is text over small files, so it is free and
    every listing gets it."""
    listed = api.dispatch("fw.profile.list", {"name": "carto_v4"})
    usb = next(s for s in listed["available"] if s["name"] == "config.TestBoardUSB")
    symbols = {row["symbol"]: row for row in usb["distinguishing"]}

    assert "MACH_STM32G431" not in symbols, "answered the same by all four"
    assert symbols["STM32_CANBUS_PA11_PA12"]["value"] == "n"
    # No labels without asking: naming them needs the tree parsed.
    assert symbols["STM32_CANBUS_PA11_PA12"]["label"] is None


def test_detail_labels_the_differences_in_the_trees_own_words(api):
    """`STM32_CANBUS_PA11_PA12` means nothing to anyone. Behind an opt-in
    because it costs a Kconfig parse - affordable on a click, never on the poll."""
    listed = api.dispatch("fw.profile.list", {"name": "carto_v4", "detail": True})
    usb = next(s for s in listed["available"] if s["name"] == "config.TestBoardUSB")
    labels = {row["symbol"]: row["label"] for row in usb["distinguishing"]}

    assert labels["STM32_CANBUS_PA11_PA12"] == "CAN bus (on PA11/PA12)"
    assert labels["STM32_USB_PA11_PA12"] == "USB (on PA11/PA12)"


def test_your_own_profile_is_listed_with_the_vendors(api):
    apply(api, name="carto_v4", profile="config.TestBoardUSB")
    profiles.capture_custom(
        api.paths, "carto_v4", "klipper", answers=['CONFIG_VERSION="MINE 1.0"']
    )

    listed = api.dispatch("fw.profile.list", {"name": "carto_v4"})
    first = listed["available"][0]
    assert first["name"] == profiles.CUSTOM_PROFILE
    assert first["origin"] == "custom"
    assert first["parent"] == "config.TestBoardUSB"


# --------------------------------------------------------------------------
# applying
# --------------------------------------------------------------------------


def test_applying_seeds_the_application_and_derives_the_bootloader(api):
    """The default. Seeding only the application leaves a type whose two
    configs describe different boards, and they only have to disagree about one
    address for the pair to produce something that does not come back."""
    out = apply(api, name="carto_v4", profile="config.TestBoardUSB")

    assert out["applied"]["fw"] == "klipper"
    assert out["derived"]["fw"] == "katapult"
    assert out["derived"]["profile"] == "derived:klipper"
    assert "CONFIG_MACH_STM32G431=y" in config_text(api, "klipper")
    assert "CONFIG_MACH_STM32G431=y" in config_text(api, "katapult")


def test_the_intent_is_recorded_in_the_hand_edited_config(api):
    apply(api, name="carto_v4", profile="config.TestBoardUSB")

    assert Registry.load(api.paths).get("carto_v4").profile == "config.TestBoardUSB"
    # ...and only for the application. Katapult's is always derived, so a second
    # key would restate that rather than record anything.
    text = pathlib.Path(api.paths.main_config).read_text(encoding="utf-8")
    assert text.count("profile:") == 1


def test_deriving_can_be_declined(api):
    out = apply(api, name="carto_v4", profile="config.TestBoardUSB", derive=False)
    assert out["derived"] is None
    assert not pathlib.Path(api.paths.config_file("carto_v4", "katapult")).exists()


def test_a_board_with_no_bootloader_derives_nothing(api):
    with Registry.mutate(api.paths, "no katapult") as reg:
        reg.add_type("bare", "stm32g431xx", katapult_installed=False)

    out = apply(api, name="bare", profile="config.TestBoardUSB")
    assert out["derived"] is None


def test_a_failed_derivation_is_not_reported_as_success(api):
    """A bootloader that cannot be derived is a board that should not be
    flashed. Reporting the application seeding as a success with a warning
    attached is how that gets missed.

    The job *fails* - it does not succeed with a null `derived`. Only the two
    addresses can be compared after the parse, so this is the one refusal here
    that cannot be hoisted in front of the job; what matters is that it stays a
    refusal rather than becoming a footnote.
    """
    job = _run_apply(api, name="carto_v4", profile="config.TestBoardBigLoader")

    assert job.state == "failed"
    assert job.error["code"] == "offset_mismatch"
    assert job.error["data"]["app_address"] == "0x8008000"
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
    """And the refusal comes back *before* a job exists.

    "That config is yours, pass force" is something a caller acts on - it
    re-asks with force - so it has to arrive as a refusal it can catch, not as
    an error read out of a job that died three Kconfig parses later. The same
    check still runs inside the write, where it is the authority; this one is
    only allowed to be early.
    """
    target = pathlib.Path(api.paths.config_file("carto_v4", "klipper"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("CONFIG_MACH_STM32=y\n# mine\n", encoding="utf-8")

    with pytest.raises(RpcError) as exc:
        api.dispatch(
            "fw.profile.apply", {"name": "carto_v4", "profile": "config.TestBoardUSB"}
        )
    assert exc.value.data["code"] == "profile_customised"
    assert "# mine" in target.read_text(encoding="utf-8")
    assert api.runner.current() is None, "refused before a job was submitted"

    out = apply(api, name="carto_v4", profile="config.TestBoardUSB", force=True)
    assert out["applied"]["backup"] is not None


# --------------------------------------------------------------------------
# drift, where the panel sees it
# --------------------------------------------------------------------------


def test_the_artifact_payload_carries_the_profile_verdict(api):
    apply(api, name="carto_v4", profile="config.TestBoardUSB")
    assert api.artifact("carto_v4", "klipper")["profile"]["reason"] is None

    target = pathlib.Path(api.paths.config_file("carto_v4", "klipper"))
    target.write_text(target.read_text(encoding="utf-8") + "CONFIG_X=y\n", encoding="utf-8")

    verdict = api.artifact("carto_v4", "klipper")["profile"]
    assert verdict["reason"] == profiles.CUSTOMISED
    assert verdict["label"] == "Your own answers"
    # A customised config is not a stale artifact and does not want a rebuild -
    # which is why this is a third verdict rather than folded into `reason`.
    assert api.artifact("carto_v4", "klipper")["reason"] != profiles.CUSTOMISED


def test_one_artifact_call_hashes_a_config_once(api, monkeypatch):
    """Two questions of the same file in the same breath.

    "Is the binary current with its inputs" and "do the inputs still say what
    the profile said" each used to read the saved config for themselves, so one
    `fw.status` hashed every config on the printer twice.
    """
    from mcu_updater import build as build_mod

    seen: list[str] = []
    real = build_mod.sha256_file
    monkeypatch.setattr(
        build_mod, "sha256_file", lambda path: (seen.append(path), real(path))[1]
    )

    apply(api, name="carto_v4", profile="config.TestBoardUSB")
    seen.clear()
    api.artifact("carto_v4", "klipper")

    config = api.paths.config_file("carto_v4", "klipper")
    assert seen.count(config) == 1, seen


def test_an_unmanaged_type_is_not_painted_as_a_problem(api):
    """Every type predating profiles is in this state."""
    verdict = api.artifact("carto_v4", "klipper")["profile"]
    assert verdict["managed"] is False
    assert verdict["tone"] == "ok"


def test_forgetting_detaches_without_touching_the_answers(api):
    apply(api, name="carto_v4", profile="config.TestBoardUSB")
    before = config_text(api, "klipper")

    out = api.dispatch("fw.profile.forget", {"name": "carto_v4"})

    assert set(out["forgotten"]) == {"klipper", "katapult"}
    assert config_text(api, "klipper") == before
    assert Registry.load(api.paths).get("carto_v4").profile == ""
    assert api.artifact("carto_v4", "klipper")["profile"]["managed"] is False


def test_a_vendor_bump_shows_up_as_something_to_do(api):
    apply(api, name="carto_v4", profile="config.TestBoardUSB")
    seed = pathlib.Path(api.paths.fw_dir("klipper")) / "config.TestBoardUSB"
    seed.write_text(seed.read_text(encoding="utf-8").replace("6.2.0", "6.3.0"), encoding="utf-8")

    verdict = api.artifact("carto_v4", "klipper")["profile"]
    assert verdict["reason"] == profiles.SEED_MOVED
    assert verdict["tone"] == "attention"


# --------------------------------------------------------------------------
# taking the bump, on the button you were pressing anyway
# --------------------------------------------------------------------------


@pytest.fixture
def building(api) -> Api:
    """The same agent with builds faked out.

    The reseed lives inside `build.build()` rather than in `fw.status`, so
    reaching it means running a build.
    """
    write_settings(api.paths, dry_run="true", service_backend="null")
    built = Api(api.paths)
    built.runner = JobRunner(api.paths, built.settings)
    return built


def _build(api: Api, **params):
    res = api.dispatch("fw.build", {"name": "carto_v4", "fw": "klipper", **params})
    assert api.runner.wait(timeout=60)
    job = api.runner.get(res["job_id"])
    assert job.state == "succeeded", job.error
    return job


def _bump(api: Api, name: str = "config.TestBoardUSB") -> None:
    seed = pathlib.Path(api.paths.fw_dir("klipper")) / name
    seed.write_text(seed.read_text(encoding="utf-8").replace("6.2.0", "6.3.0"), encoding="utf-8")


def test_a_build_takes_the_bump_by_default(building):
    """Asking for nothing gets `reseed_on_build`, which is what makes this call,
    the CLI and a fleet build do the same thing."""
    apply(building, name="carto_v4", profile="config.TestBoardUSB")
    _bump(building)

    job = _build(building)

    assert job.result["reseeded"] == "config.TestBoardUSB"
    assert "6.3.0" in config_text(building, "klipper")
    assert any("reseeding" in line for line in _log(job))


def test_a_build_can_be_told_to_leave_it(building):
    """What "build as-is" on the confirm dialog sends. For this run only - the
    setting is untouched."""
    apply(building, name="carto_v4", profile="config.TestBoardUSB")
    _bump(building)

    job = _build(building, reseed=False)

    assert job.result["reseeded"] is None
    assert "6.2.0" in config_text(building, "klipper")


def test_the_setting_turns_it_off_for_every_path(building):
    write_settings(
        building.paths, dry_run="true", service_backend="null", reseed_on_build="false"
    )
    apply(building, name="carto_v4", profile="config.TestBoardUSB")
    _bump(building)

    job = _build(building)

    assert job.result["reseeded"] is None
    assert "6.2.0" in config_text(building, "klipper")


def test_the_setting_is_editable_from_the_panel(building):
    building.dispatch("fw.settings.set", {"settings": {"reseed_on_build": False}})
    assert building.settings().reseed_on_build is False
    # Coerced from the settings module's own list of booleans, so a switch never
    # comes back "must be a whole number".
    with pytest.raises(RpcError):
        building.dispatch("fw.settings.set", {"settings": {"reseed_on_build": 1}})


def test_a_build_never_reseeds_over_your_own_answers(building):
    """You are on your own profile; the vendor's bump is informational until you
    say otherwise. This is the one that would lose work."""
    apply(building, name="carto_v4", profile="config.TestBoardUSB")
    target = pathlib.Path(building.paths.config_file("carto_v4", "klipper"))
    target.write_text(
        target.read_text(encoding="utf-8").replace('"TESTFW 6.2.0"', '"MINE 1.0"'),
        encoding="utf-8",
    )
    _bump(building)

    job = _build(building)

    assert job.result["reseeded"] is None
    assert '"MINE 1.0"' in config_text(building, "klipper")


def test_a_fleet_build_takes_the_bump_too(building):
    """The claim that made `build.build()` the right home for this rule.

    A fleet build reaches the compiler through `providers.kconfig_make`, not
    through `fw.build` - so a reseed implemented in the agent's single-build
    method left every batch building the older answers, silently.
    """
    apply(building, name="carto_v4", profile="config.TestBoardUSB")
    _bump(building)

    res = building.dispatch("fw.build_all", {"scope": "all"})
    assert building.runner.wait(timeout=90)
    job = building.runner.get(res["job_id"])
    assert job.state == "succeeded", job.error

    assert "6.3.0" in config_text(building, "klipper")


def _log(job) -> list[str]:
    lines, _next, _dropped = job.log_since(0)
    return [line.text for line in lines]


# --------------------------------------------------------------------------
# a save is where you stop tracking and start owning
# --------------------------------------------------------------------------


def _edit_and_save(api: Api, value: str) -> dict:
    session = api.dispatch("fw.kconfig.open", {"name": "carto_v4", "fw": "klipper"})[
        "session"
    ]
    api.dispatch("fw.kconfig.set", {"session": session, "id": "VERSION", "value": value})
    return api.dispatch("fw.kconfig.save", {"session": session})


def test_saving_over_a_profile_keeps_the_answers_as_your_own(api):
    """Without this, editing a profile is the dead end it is today: the drift is
    reported and the answers that caused it have nowhere to live."""
    apply(api, name="carto_v4", profile="config.TestBoardUSB")

    saved = _edit_and_save(api, "MINE 1.0")

    assert saved["custom_profile"] == profiles.CUSTOM_PROFILE
    own = profiles.read_custom(api.paths, "carto_v4", "klipper")
    assert own is not None and own.parent == "config.TestBoardUSB"
    assert [row["symbol"] for row in profiles.overrides(api.paths, "carto_v4", "klipper")] == [
        "VERSION"
    ]


def test_your_profile_survives_removing_and_readding_the_type(api):
    """`fw.type.remove` already promises it keeps the config directory, and this
    is now the most valuable thing in there: the answers a user wrote, which
    nothing else on the machine holds a copy of."""
    apply(api, name="carto_v4", profile="config.TestBoardUSB")
    _edit_and_save(api, "MINE 1.0")

    out = api.dispatch("fw.type.remove", {"name": "carto_v4", "force": True})
    assert out["kept_config_dir"] == api.paths.type_dir("carto_v4")
    assert profiles.read_custom(api.paths, "carto_v4", "klipper") is not None

    api.dispatch("fw.type.add", {"name": "carto_v4", "chipset": "stm32g431xx"})
    offered = profiles.available(api.paths, "klipper", mcu_type="carto_v4")
    assert offered[0].name == profiles.CUSTOM_PROFILE
    assert offered[0].parent == "config.TestBoardUSB"


def test_a_save_that_changed_nothing_keeps_no_second_copy(api):
    """A capture identical to the vendor's entry is a duplicate in the picker."""
    apply(api, name="carto_v4", profile="config.TestBoardUSB")

    session = api.dispatch("fw.kconfig.open", {"name": "carto_v4", "fw": "klipper"})[
        "session"
    ]
    saved = api.dispatch("fw.kconfig.save", {"session": session})

    assert saved["custom_profile"] is None
    assert profiles.read_custom(api.paths, "carto_v4", "klipper") is None


def test_a_tree_that_ships_no_profiles_captures_nothing(api):
    """Katapult ships none, so there is no picker to offer this in and nothing
    to fork from - the .config is already the whole story."""
    session = api.dispatch("fw.kconfig.open", {"name": "carto_v4", "fw": "katapult"})[
        "session"
    ]
    api.dispatch(
        "fw.kconfig.set", {"session": session, "id": "LOW_LEVEL_OPTIONS", "value": "y"}
    )
    saved = api.dispatch("fw.kconfig.save", {"session": session})

    assert saved["custom_profile"] is None
    assert profiles.read_custom(api.paths, "carto_v4", "katapult") is None


def test_the_methods_are_advertised(api):
    """Not flash methods: seeding writes a config file and touches no board."""
    advertised = api.available_methods()
    assert {"fw.profile.list", "fw.profile.apply", "fw.profile.forget"} <= set(advertised)


def test_a_read_only_agent_still_lists_and_forgets(api):
    """Applying is a job for its *runtime*, not its danger - three Kconfig
    parses against a method budget of well under a second. So it needs a runner
    and disappears without one, while reading which profiles exist and
    detaching from one stay available: neither costs a parse, and an install
    with nothing to submit jobs to still has answers worth looking at."""
    read_only = Api(api.paths)
    advertised = set(read_only.available_methods())

    assert "fw.profile.apply" not in advertised
    assert {"fw.profile.list", "fw.profile.forget"} <= advertised


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
    api = _api_for(paths)

    listing = api.dispatch("fw.profile.list", {"name": "carto_v4"})
    assert listing["firmware"] == "cartographer"
    assert [s["name"] for s in listing["available"]] == sorted(SEEDS)

    out = apply(api, name="carto_v4", profile="config.TestBoardCAN")
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
