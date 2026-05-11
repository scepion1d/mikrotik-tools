"""Back-compat shim: re-exports the data model from :mod:`rsc.parser`.

Historically these dataclasses (``Item``, ``Config``, ``Op``) and the
menu-classification constants lived inside ``rsc.diff``. They moved to
the shared :mod:`rsc.parser` package so :mod:`rsc.bundle` can use them
too. This module keeps the old import paths working.

Prefer importing from :mod:`rsc.parser` directly in new code.
"""

from __future__ import annotations

from rsc.parser import (  # noqa: F401  (re-exports)
    IAC_PREFIX,
    MENU_ORDER,
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
    "MENU_ORDER",
    "MENUS_ORDERED",
    "MENUS_SINGLETON",
    "MENUS_WITH_NAME",
    "Op",
]
