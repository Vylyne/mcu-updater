"""Firmware-specific helper capabilities selected by static configuration."""

from __future__ import annotations

from .spec import BootselHandoff, BootselRequester


def for_name(name: str, *, family: str) -> BootselRequester | None:
    """Resolve a helper without importing implementations during package init."""
    from .registry import for_name as resolve

    return resolve(name, family=family)


__all__ = ["BootselHandoff", "BootselRequester", "for_name"]
