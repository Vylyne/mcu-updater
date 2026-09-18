"""Firmware-specific helper capabilities selected by static configuration."""

from __future__ import annotations

from .spec import BootselHandoff, BootselRequester, Helper


def for_name(name: str, *, family: str) -> Helper | None:
    """Resolve a helper without importing implementations during package init."""
    from .registry import for_name as resolve

    return resolve(name, family=family)


def bootsel_requester(helper: Helper | None) -> BootselRequester | None:
    """`helper`'s BOOTSEL request capability, or None when it has none."""
    return helper if isinstance(helper, BootselRequester) else None


__all__ = ["BootselHandoff", "BootselRequester", "Helper", "bootsel_requester", "for_name"]
