"""Static firmware-helper registry.

No package scanning or config-selected imports: helpers can stop services and
write firmware, so each implementation must be reviewed and named here.
"""

from __future__ import annotations

from ..errors import ConfigCorruptError
from .spec import BootselRequester

#: Every firmware-specific helper. Add an implementation and one explicit entry
#: here; configuration never controls which Python module gets imported.
HELPERS: tuple[BootselRequester, ...] = ()

_BY_NAME: dict[str, BootselRequester] = {helper.name: helper for helper in HELPERS}


def for_name(name: str, *, family: str) -> BootselRequester | None:
    """Resolve one configured helper, refusing misspellings before a write."""
    if not name:
        return None
    helper = _BY_NAME.get(name)
    if helper is None:
        raise ConfigCorruptError(
            f"firmware family '{family}' configures unknown helper '{name}'"
        )
    return helper
