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


def test_a_display_target_carries_no_family(paths, settings, tmp_path):
    """`None` is not "unknown" here - a PlatformIO env already names the board,
    the partitions and the flags, so there is no family axis at all. That is what
    makes a `fw` filter exclude screens rather than match them."""
    tree = tmp_path / "knomi_serial"
    (tree / ".pio" / "build" / "knomi_toolchanger").mkdir(parents=True)
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[display knomi_toolchanger]\nsource: {tree}\n")

    install = Install.load(paths, settings)
    targets = PlatformIO().targets(install)

    assert [(t.name, t.fw) for t in targets] == [("knomi_toolchanger", None)]
    assert PlatformIO().blocked(install, targets[0]) is None


def test_a_missing_source_and_a_wrong_source_read_differently(paths, settings):
    """Two different fixes: one is a missing `source:`/`pio_source`, the
    other is a path that is there and wrong. A single "not configured" would send
    somebody to edit a key that is already set."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write("\n[display no_source]\n\n[display bad_source]\nsource: /nope/not/here\n")

    install = Install.load(paths, settings)
    reasons = {
        t.name: PlatformIO().blocked(install, t) for t in PlatformIO().targets(install)
    }

    assert "no source tree configured" in reasons["no_source"]
    assert "not found" in reasons["bad_source"]


def test_the_shared_pio_source_is_applied_before_a_provider_sees_it(paths, settings, tmp_path):
    """`pio_source` is a fallback in the config layer, and `Install` resolves
    it. A provider re-implementing that would be a second place for it to drift -
    and it was already documented in the README before it existed."""
    tree = tmp_path / "shared"
    tree.mkdir()
    settings.pio_source = str(tree)
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write("\n[display knomi]\n")

    install = Install.load(paths, settings)
    assert install.displays["knomi"].source == str(tree)
    assert PlatformIO().blocked(install, BuildTarget("platformio", "knomi")) is None
