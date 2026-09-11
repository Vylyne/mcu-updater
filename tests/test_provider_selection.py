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
from mcu_updater.providers import selection


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


def test_cmake_is_asked_before_the_registry(paths, live_registry_text, monkeypatch):
    """Order, not membership - and it has to be forced to mean anything.

    `config.py`'s foreign-builder rule keeps the three name-sets disjoint, so a
    fixture built through real config can never make them collide, and a test
    written that way passes whatever order the resolver asks in. The collision
    is therefore forged here: `bttebb36` is a real kconfig type, and cmake is
    made to claim it too.

    The scenario is exactly the one the resolver's comment describes. If that
    rule ever slips and the registry is asked first, a Roadrunner resolves to
    `kconfig_make` and is sent down a build path with no `.config` to run,
    silently. This fails instead.
    """
    _seed_registry(paths, live_registry_text)
    monkeypatch.setattr(
        selection, "_declared_builders", lambda _p: {"bttebb36": "cmake"}
    )

    assert providers.provider_of(paths, "bttebb36") == providers.Cmake.name


def test_platformio_is_asked_before_both(paths, live_registry_text, monkeypatch):
    """The same forced collision, one rung higher."""
    _seed_registry(paths, live_registry_text)
    monkeypatch.setattr(
        selection, "_declared_builders", lambda _p: {"bttebb36": "platformio"}
    )

    assert providers.provider_of(paths, "bttebb36") == providers.PlatformIO.name


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
def test_a_malformed_foreign_section_does_not_break_resolution(
    paths, fake_root, live_registry_text
):
    """Selection answers "whose is this name", not "is this config good".

    `cmake.load()` raises for a cmake type that names no `cmake_target:`, and
    `pio.load()` raises for a PlatformIO type that names no `env:`. Resolving
    one name through those loads made every other section's validity
    load-bearing: a half-written screen section broke `flash -t <kconfig
    type>`, which has nothing to do with it. The build path still validates.
    """
    _seed_registry(paths, live_registry_text)
    tree = os.path.join(fake_root, "roadrunner", "rp2040")
    os.makedirs(tree, exist_ok=True)
    _append(
        paths,
        "\n[firmware roadrunner]\n"
        f"source: {tree}\n"
        "builder: cmake\n\n"
        "[type halfwritten]\nchipset: rp2040\nfirmware: roadrunner\n",
    )

    assert providers.provider_of(paths, "bttebb36") == providers.KconfigMake.name
    assert providers.provider_of(paths, "halfwritten") == providers.Cmake.name
    assert "halfwritten" in providers.known_type_names(paths)
