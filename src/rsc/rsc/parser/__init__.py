"""rsc.parser -- shared parsing + identity model for RouterOS .rsc tooling.

A dependency-free library that turns a RouterOS script into an indexed
:class:`Config` of :class:`Item` rows, classifies each item under its
menu, and resolves a stable ``iac.<type>.<subtype>.<tag>`` identifier
for it (including synthetic ids for built-in / id-less rows).

Consumers
---------
- ``rsc.diff`` -- uses the parser + identity to compare two configs.
- ``rsc.bundle`` -- uses the parser + identity to minimize a profile folder
  into a single deploy-ready ``.rsc``.

Quick start
-----------
::

    from rsc.parser import parse_file, entity_id

    cfg = parse_file("baseline.rsc")
    for menu, items in cfg.items_by_menu.items():
        for pos, item in enumerate(items):
            print(menu, entity_id(item, pos))

Public API
----------
- :func:`parse_file` -- read and parse a ``.rsc`` file from disk
- :func:`parse_text` -- parse a ``.rsc`` string in memory
- :class:`Config` -- parsed config (``{menu_path: [Item]}``)
- :class:`Item` -- one parsed config item with identity-key resolution
- :class:`Op` -- one diff operation (``add`` / ``set`` / ``remove`` / ...)
- :func:`entity_id` -- bare ``iac.x.y.z`` id (synthetic when needed)
- :func:`is_synthetic` -- True if entity_id derived the id vs read it
- :data:`MENUS_WITH_NAME`, :data:`MENUS_ORDERED`, :data:`MENUS_SINGLETON`
- :data:`IAC_PREFIX`
"""

from .identity import entity_id, is_synthetic
from .menus import IAC_PREFIX, MENUS_ORDERED, MENUS_SINGLETON, MENUS_WITH_NAME
from .model import Config, Item, Op
from .parser import parse_file, parse_text

__version__ = "0.2.0"

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
