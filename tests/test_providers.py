"""The Provider seam itself, as opposed to what a bulk operation does with it.

`test_agent_bulk.py` covers the selection a batch makes, which is where the
consequences live. This covers the contract underneath: that both build systems
answer the same questions, that the registry stays static, and that the places a
wrong answer would be *silent* raise instead.
"""

from __future__ import annotations

import os

import pytest

from mcu_updater import providers
from mcu_updater.providers import BuildTarget, Install, KconfigMake, PlatformIO
from mcu_updater.states import ARTIFACT_ABSENT, ARTIFACT_CURRENT

from .conftest import write_settings

EBB = "bttebb36"


@pytest.fixture
def install(paths, live_registry_text, settings):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, dry_run="true")
    return Install.load(paths, settings)


def _save_config(paths, mcu_type, fw="klipper") -> None:
    os.makedirs(paths.type_dir(mcu_type), exist_ok=True)
    with open(paths.config_file(mcu_type, fw), "w", encoding="utf-8") as fh:
        fh.write("CONFIG_MACH_STM32=y\n")


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------


def test_the_provider_set_is_static_and_ordered():
    """No `pkgutil` walk of the package directory.

    Discovery would mean this process imports whatever `.py` somebody dropped in
    - and this process holds the exclusive lock, flashes firmware, and has
    NOPASSWD `systemctl` for Klipper. The tuple is the seam; being able to add a
    build system without editing it was never part of the design.

    The order is asserted because a batch works through it: MCUs have always
    built first, and a refactor that reordered them would be a behaviour change
    hiding inside a rename.
    """
    assert [p.name for p in providers.PROVIDERS] == ["kconfig_make", "platformio"]


def test_an_unknown_provider_raises_rather_than_defaulting():
    """A target naming a provider that does not exist is a bug in whatever built
    it. Falling back to the first one would run a klipper build against a
    PlatformIO env."""
    with pytest.raises(KeyError):
        providers.by_name("prebuilt")


def test_every_provider_answers_the_whole_protocol(install):
    """Not a typing assertion - `Protocol` is structural and checked statically.
    This is the runtime half: a provider that forgot a method would only be
    found by the batch that called it, mid-fleet."""
    for provider in providers.PROVIDERS:
        assert provider.name and provider.label
        for target in provider.targets(install):
            assert target.provider == provider.name
            assert provider.describe(target)
            # Both are total functions over their own targets: neither may raise
            # for something the provider itself enumerated.
            provider.blocked(install, target)
            provider.artifact_status(install, target)


# --------------------------------------------------------------------------
# kconfig + make
# --------------------------------------------------------------------------


def test_a_kconfig_target_without_a_family_raises_rather_than_assuming_klipper(install):
    """The one place a quiet default would be dangerous.

    Every target this provider enumerates carries a family. One arriving without
    means it was hand-built for the wrong provider - and defaulting to klipper
    there is exactly the silent wrong-tree build that the (type, family) shape
    exists to prevent, only now with no config mismatch to fail on.
    """
    orphan = BuildTarget("kconfig_make", EBB)
    with pytest.raises(ValueError, match="no firmware family"):
        KconfigMake().blocked(install, orphan)


def test_an_unconfigured_type_is_blocked_with_the_menuconfig_reason(install):
    reason = KconfigMake().blocked(install, BuildTarget("kconfig_make", EBB, "klipper"))
    assert reason is not None and "menuconfig" in reason


def test_a_missing_source_tree_is_not_a_block(install, paths):
    """Deliberately *not* checked before building.

    A missing tree is a real failure worth reporting - the build raises naming
    the directory - while a block is skipped in silence. Treating "klipper isn't
    installed" as a setup step to wait for would drop every board from a fleet
    build and report success.
    """
    _save_config(paths, EBB)
    install = Install.load(paths, install.settings)
    assert KconfigMake().blocked(install, BuildTarget("kconfig_make", EBB, "klipper")) is None


def test_artifact_status_is_the_shared_vocabulary(install, paths, settings):
    from mcu_updater.build import build as do_build

    target = BuildTarget("kconfig_make", EBB, "klipper")
    assert KconfigMake().artifact_status(install, target).state == ARTIFACT_ABSENT

    settings.dry_run = True
    _save_config(paths, EBB)
    do_build(paths, install.registry, settings, EBB, "klipper")
    assert KconfigMake().artifact_status(install, target).state == ARTIFACT_CURRENT


# --------------------------------------------------------------------------
# platformio
# --------------------------------------------------------------------------


def test_a_display_declaring_firmware_carries_that_family(paths, settings, tmp_path):
    """The new shape: a type naming a platformio-built family reaches `fw` as
    that family, so "rebuild knomi_serial everywhere" can actually find it -
    the whole reason `firmware:` exists on a type at all."""
    tree = tmp_path / "knomi_serial"
    (tree / ".pio" / "build" / "knomi").mkdir(parents=True)
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            f"[firmware knomi_serial]\nsource: {tree}\nbuilder: platformio\n\n"
            "[type knomi]\nchipset: esp32\nfirmware: knomi_serial\nenv: knomi\n"
        )

    install = Install.load(paths, settings)
    targets = PlatformIO().targets(install)

    assert [(t.name, t.fw) for t in targets] == [("knomi", "knomi_serial")]


def test_a_source_less_family_still_falls_back_to_its_own_name(paths, settings):
    """`source:` lives on the `[firmware ...]` section now, not the type - so
    an unset key is no longer "nothing configured", it is the same `~/<family
    name>` convention klipper and katapult have always used. An explicit
    wrong path is a different, and differently worded, failure - both are
    "not found", but at the path each one actually names."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "[firmware no_source_fw]\nbuilder: platformio\n\n"
            "[type no_source]\nfirmware: no_source_fw\nenv: no_source\n\n"
            "[firmware bad_source_fw]\nsource: /nope/not/here\nbuilder: platformio\n\n"
            "[type bad_source]\nfirmware: bad_source_fw\nenv: bad_source\n"
        )

    install = Install.load(paths, settings)
    reasons = {
        t.name: PlatformIO().blocked(install, t) for t in PlatformIO().targets(install)
    }

    assert "not found" in reasons["no_source"]
    assert paths.fw_dir("no_source_fw") in reasons["no_source"]
    assert "not found" in reasons["bad_source"]
    assert "/nope/not/here" in reasons["bad_source"]


def test_pio_source_is_not_yet_applied_to_a_family_with_no_source(paths, settings, tmp_path):
    """`pio_source` used to be a fallback `Install.load()` applied before a
    provider ever saw a type. `source:` moved onto the `[firmware ...]`
    section this step, and nothing has reconnected `pio_source` to it yet -
    `providers/pio.py`'s `default_source` parameter is still threaded through
    for exactly that reason, but its body no longer reads it. A family with no
    `source:` of its own falls back to `~/<family name>`, the same as any
    other firmware family, not to `pio_source`.

    This is a deliberate, temporary gap: retiring the old `[display ...]`
    fallback now (rather than at Step 14, where the plan originally placed
    it) is what left `pio_source` disconnected early. Reconnecting it - or
    retiring the setting - belongs to Step 14 alongside the rest of
    `default_source`'s removal.
    """
    tree = tmp_path / "shared"
    tree.mkdir()
    settings.pio_source = str(tree)
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write("[firmware knomi_serial]\nbuilder: platformio\n\n[type knomi]\nfirmware: knomi_serial\nenv: knomi\n")

    install = Install.load(paths, settings)
    assert install.displays["knomi"].source != str(tree)
    assert install.displays["knomi"].source == str(paths.fw_dir("knomi_serial"))
