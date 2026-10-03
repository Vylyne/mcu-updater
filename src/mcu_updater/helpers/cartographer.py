"""Cartographer's Klipper fork.

Its one oddity: `CONFIG_VERSION` is a hand-maintained literal such as
"CARTOGRAPHER 6.2.0", with no commit in it. docs/decisions.md ("Do not
synthesize a sha into Cartographer's `CONFIG_VERSION`") records why this reader
reports no sha rather than inventing one, and why the verdict for these boards
is the built-stamp comparison instead.
"""

from __future__ import annotations


class CartographerHelper:
    name: str = "cartographer"
    klipper_prefix: str = "mcu"

    def running_sha(self, version: str | None) -> str | None:
        return None

    def is_dirty(self, version: str | None) -> bool:
        return False


__all__ = ["CartographerHelper"]
