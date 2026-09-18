"""Static firmware-helper registry.

No package scanning or config-selected imports: helpers can stop services and
write firmware, so each implementation must be reviewed and named here.
"""

from __future__ import annotations

from ..errors import ConfigCorruptError
from .knomi_serial import KnomiSerialHelper
from .roadrunner import RoadrunnerHelper
from .spec import Helper

#: Every firmware-specific helper. Add an implementation, one explicit entry
#: here, and its name in `firmware.HELPERS`; configuration never controls which
#: Python module gets imported.
HELPERS: tuple[Helper, ...] = (KnomiSerialHelper(), RoadrunnerHelper())

_BY_NAME: dict[str, Helper] = {helper.name: helper for helper in HELPERS}


def for_name(name: str, *, family: str) -> Helper | None:
    """Resolve one configured helper, refusing misspellings before a write."""
    if not name:
        return None
    helper = _BY_NAME.get(name)
    if helper is None:
        raise ConfigCorruptError(
            f"firmware family '{family}' configures unknown helper '{name}' "
            f"(known: {', '.join(sorted(_BY_NAME))})",
            family=family,
            value=name,
        )
    return helper
