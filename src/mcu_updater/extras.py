"""What a seam contributes to a `targets[]` row beyond the shared keys.

A builder, flasher or helper that knows a fact worth showing on a row returns
an `Extra`, and `targets()` collects them into one list. The list is the point:
a new seam adds entries rather than a new wire type, and a reader renders
`label value` without branching on who said it. The per-builder `extra` object
this replaced grew one TypeScript type per builder, which is exactly the
branching a uniform row exists to remove.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any, Literal

#: Which kind of seam contributed an entry. A row orders its extras by this.
Seam = Literal["builder", "flasher", "helper"]
SEAMS: tuple[str, ...] = ("builder", "flasher", "helper")

#: A JSON scalar. Anything richer would be a new wire shape, which is what this
#: list exists to avoid.
Scalar = str | int | float | bool | None


@dataclasses.dataclass(frozen=True)
class Extra:
    """One fact for a row: who said it, a stable key, and words for a person."""

    seam: Seam
    #: The seam's registered name: `cmake`, `bootsel`, `knomi_serial`.
    name: str
    #: Stable and machine-readable. The UI never branches on it.
    key: str
    label: str
    value: Scalar

    def __post_init__(self) -> None:
        if self.seam not in SEAMS:
            raise ValueError(f"unknown seam {self.seam!r}; expected one of {SEAMS}")
        if self.value is not None and not isinstance(self.value, (str, int, float, bool)):
            raise TypeError(
                f"an extra's value must be a JSON scalar, not {type(self.value).__name__}"
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "seam": self.seam,
            "name": self.name,
            "key": self.key,
            "label": self.label,
            "value": self.value,
        }


def ordered(extras: Iterable[Extra]) -> list[dict[str, Any]]:
    """Builders, then flashers, then helpers, each as its seam returned them.

    `sorted` is stable, so sorting on the seam alone keeps each seam's order.
    """
    return [extra.to_json() for extra in sorted(extras, key=lambda e: SEAMS.index(e.seam))]


__all__ = ["SEAMS", "Extra", "Scalar", "Seam", "ordered"]
