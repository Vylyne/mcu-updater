"""install.sh is not run by the suite, so pin the lines that wire it to seed.py."""

from __future__ import annotations

from .conftest import REPO_ROOT


def _script() -> str:
    return (REPO_ROOT / "install.sh").read_text(encoding="utf-8")


def test_install_runs_the_seeder_before_validating_the_config():
    text = _script()
    assert "-m mcu_updater.seed" in text
    assert text.index("    seed_firmware_sections\n") < text.index("reg = Registry.load(paths)")


def test_install_offers_a_single_branch_katapult_clone():
    assert "git clone --single-branch https://github.com/Arksine/katapult" in _script()
