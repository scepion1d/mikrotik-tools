"""Back-compat shim: re-exports the data model from :mod:`rsc_parser`.

Historically these dataclasses (``Item``, ``Config``, ``Op``) and the
menu-classification constants lived inside ``rsc_diff``. They moved to
the shared :mod:`rsc_parser` package so :mod:`rsc_bundle` can use them
too. This module keeps the old import paths working.

Prefer importing from :mod:`rsc_parser` directly in new code.
"""

from __future__ import annotations

from rsc_parser import (  # noqa: F401  (re-exports)
    IAC_PREFIX,
    MENUS_ORDERED,
    MENUS_SINGLETON,
    MENUS_WITH_NAME,
    Config,
    Item,
    Op,
)

__all__ = [
    "Config",
    "IAC_PREFIX",
    "Item",
    "MENUS_ORDERED",
    "MENUS_SINGLETON",
    "MENUS_WITH_NAME",
    "Op",
]
