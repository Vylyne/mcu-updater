"""Which provider owns a type name.

The question itself is one line; the reason it has its own module is that two
call sites used to answer it by guessing, and both guesses were the same guess:
"not in the kconfig registry, therefore PlatformIO". That was true for exactly
as long as there were two providers.

So these tests are mostly about the *order* the resolver asks in and about the
names it refuses, not about the happy path.
"""

from __future__ import annotations

import os

import pytest

from mcu_updater import providers
from mcu_updater.errors import UnknownTypeError
from mcu_updater.paths import Paths


def _seed_registry(paths: Paths, text: str) -> None:
    os.makedirs(paths.config_dir, exist_ok=True)
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _append(paths: Paths, text: str) -> None:
    with open(paths.main_config, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _declare_cmake(paths: Paths, fake_root, name: str = "roadrunner") -> None:
    """A cmake type with a source tree and no build directory.

    Mirrors `test_agent_bulk._declare_cmake`: CMakeLists.txt so the tree reads
    as real, and deliberately no `build/`, which would make `blocked()` shell
    out to a real cmake.
    """
    tree = os.path.join(fake_root, "roadrunner", "rp2040")
    os.makedirs(tree, exist_ok=True)
    with open(os.path.join(tree, "CMakeLists.txt"), "w", encoding="utf-8") as fh:
        fh.write("project(roadrunner)\n")
    _append(
        paths,
        f"\n[firmware roadrunner]\nsource: {tree}\nbuilder: cmake\n\n"
        f"[type {name}]\nchipset: rp2040\nfirmware: roadrunner\n"
        f"cmake_target: roadrunner_v1_i2c_rgb\n",
    )


def _declare_display(paths: Paths, name: str = "knomi_toolchanger") -> None:
    """A PlatformIO display, declared the way `test_agent_bulk` declares one.

    A display is a `[type ...]` carrying an `env:`, not a section of its own -
    the provider is derived from the family's `builder:`, not from the section
    name. `live_registry_text` already declares the `knomi_serial` family
    pointed at `~/knomi_serial`, so the tree goes there rather than an
    arbitrary temp dir, or the family and the fixture disagree about the source.
    """
    tree = os.path.join(paths.home, "knomi_serial")
    os.makedirs(os.path.join(tree, ".pio", "build", name), exist_ok=True)
    _append(
        paths,
        f"\n[type {name}]\nchipset: esp32\nfirmware: knomi_serial\nenv: {name}\n",
    )


def test_resolves_a_kconfig_type(paths, live_registry_text):
    _seed_registry(paths, live_registry_text)

    assert providers.provider_of(paths, "bttebb36") == providers.KconfigMake.name


def test_resolves_a_cmake_type(paths, fake_root, live_registry_text):
    _seed_registry(paths, live_registry_text)
    _declare_cmake(paths, fake_root)

    assert providers.provider_of(paths, "roadrunner") == providers.Cmake.name


def test_resolves_a_platformio_display(paths, live_registry_text):
    _seed_registry(paths, live_registry_text)
    _declare_display(paths)

    assert (
        providers.provider_of(paths, "knomi_toolchanger") == providers.PlatformIO.name
    )


def test_unknown_name_raises_rather_than_defaulting(paths, live_registry_text):
    """The bug this resolver exists to prevent, in its original form.

    Defaulting an unrecognised name to kconfig is what produced "no saved
    klipper config" for a screen - an error about the wrong thing entirely.
    """
    _seed_registry(paths, live_registry_text)

    with pytest.raises(UnknownTypeError) as excinfo:
        providers.provider_of(paths, "nosuchtype")

    assert excinfo.value.data["type"] == "nosuchtype"
    assert "bttebb36" in excinfo.value.data["known"]


def test_cmake_is_asked_before_the_registry(paths, fake_root, live_registry_text):
    """Order, not membership.

    `config.py`'s foreign-builder rule keeps a cmake type out of the kconfig
    registry, so today this cannot collide. If that rule ever slips, asking the
    registry first answers `kconfig_make` for a Roadrunner and sends it down a
    build path with no `.config` to run - silently. This pins the order so the
    slip is a test failure rather than a wrong build.
    """
    _seed_registry(paths, live_registry_text)
    _declare_cmake(paths, fake_root)

    assert providers.provider_of(paths, "roadrunner") != providers.KconfigMake.name


def test_platformio_is_asked_first(paths, fake_root, live_registry_text):
    _seed_registry(paths, live_registry_text)
    _declare_display(paths)
    _declare_cmake(paths, fake_root)

    assert (
        providers.provider_of(paths, "knomi_toolchanger") == providers.PlatformIO.name
    )


def test_known_names_span_every_provider(paths, fake_root, live_registry_text):
    _seed_registry(paths, live_registry_text)
    _declare_display(paths)
    _declare_cmake(paths, fake_root)

    known = providers.known_type_names(paths)

    assert "bttebb36" in known
    assert "roadrunner" in known
    assert "knomi_toolchanger" in known
    assert known == sorted(set(known))


def test_resolution_reflects_a_type_added_after_first_call(
    paths, fake_root, live_registry_text
):
    """No caching: `fw.type.add` has to work without restarting the agent."""
    _seed_registry(paths, live_registry_text)

    with pytest.raises(UnknownTypeError):
        providers.provider_of(paths, "roadrunner")

    _declare_cmake(paths, fake_root)

    assert providers.provider_of(paths, "roadrunner") == providers.Cmake.name
