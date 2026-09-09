"""Firmware-specific helper capabilities selected by static configuration."""

from __future__ import annotations

from .registry import for_name
from .spec import BootselHandoff, BootselRequester

__all__ = ["BootselHandoff", "BootselRequester", "for_name"]
