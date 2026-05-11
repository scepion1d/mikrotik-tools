"""rsc -- RouterOS ``.rsc`` script processing.

This package bundles three subpackages and a thin top-level CLI that
dispatches to each one:

- :mod:`rsc.parser` -- shared parser + identity model (library).
- :mod:`rsc.bundle` -- ``rsc bundle ...`` -- merge a profile folder into
  one minimal ``.rsc``.
- :mod:`rsc.diff`   -- ``rsc diff ...`` -- diff two ``.rsc`` configs into
  an apply-able patch (single-patch or roundtrip mode).

Library re-exports
------------------
The most-used parser surface is re-exported here so a bare
``from rsc import parse_file, Config, Item`` works.
"""

from .parser import (
    Config,
    IAC_PREFIX,
    Item,
    MENUS_ORDERED,
    MENUS_SINGLETON,
    MENUS_WITH_NAME,
    Op,
    entity_id,
    is_synthetic,
    parse_file,
    parse_text,
)

__version__ = "0.1.0"

__all__ = [
    "Config",
    "IAC_PREFIX",
    "Item",
    "MENUS_ORDERED",
    "MENUS_SINGLETON",
    "MENUS_WITH_NAME",
    "Op",
    "__version__",
    "entity_id",
    "is_synthetic",
    "parse_file",
    "parse_text",
]
