"""Data model for parsed RouterOS configs and diff operations.

Pure dataclasses with no behaviour beyond identity-key resolution. Menu
classification lives in :mod:`rsc_parser.menus`; synthetic-id derivation
for built-in / id-less items lives in :mod:`rsc_parser.identity`.

The :meth:`Item.identity_key` method returns a *parser-level* key string
in the format ``key=value`` / ``comment~token`` / ``@pos=N`` / a menu
path -- this format is what the diff emitter consumes when building
``[find ...]`` selectors. For a *bare* iac-namespace identifier suitable
for diagnostics or cross-reference (e.g. ``place-before=`` pinning),
use :func:`rsc_parser.identity.entity_id`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .menus import IAC_PREFIX, MENUS_ORDERED, MENUS_SINGLETON, MENUS_WITH_NAME


@dataclass(frozen=True)
class Item:
    """One configuration item under a menu path.

    ``verb`` records how the item appeared in source (``add`` / ``set``).
    It is informational only -- the differ recomputes the right verb for
    each op.
    """

    menu: str
    verb: Literal["add", "set"]
    props: dict[str, str] = field(default_factory=dict)

    def identity_key(self, position: int) -> str:
        """Compute the identity key under this item's menu.

        Resolution order (returns the FIRST match):

        0. menu in :data:`MENUS_SINGLETON` -> the menu path itself
        1. ``name=iac.x.y`` if menu is in :data:`MENUS_WITH_NAME`
        2. iac-namespace token inside ``comment=`` -> ``comment~iac.x.y``
        3. ``default-name=etherN`` for ``set [find default-name=...]`` rows
        4. menu in :data:`MENUS_ORDERED` -> ``@pos=N``
        5. fallback: ``name=value`` if any ``name=`` is set, else ``@anon=N``

        The returned string is consumed by the diff emitter when building
        ``[find ...]`` selectors. It is NOT a bare iac id -- callers that
        need one should use :func:`rsc_parser.identity.entity_id` instead.
        """
        if self.menu in MENUS_SINGLETON:
            return self.menu  # one implicit item per singleton menu

        # 1. name= field
        name = self.props.get("name")
        if name and self.menu in MENUS_WITH_NAME:
            return f"name={name}"

        # 2. iac.* token in comment
        comment = self.props.get("comment", "")
        # Parser preserves surrounding quotes on "..." values; strip them
        # before tokenising so the iac.* prefix check actually matches.
        if comment.startswith('"') and comment.endswith('"') and len(comment) >= 2:
            comment = comment[1:-1]
        for token in comment.replace(",", " ").split():
            if token.startswith(IAC_PREFIX):
                return f"comment~{token.rstrip('.,;:')}"

        # 3. default-name= (built-ins addressed via [find default-name=...])
        default_name = self.props.get("default-name")
        if default_name:
            return f"default-name={default_name}"

        # 4. Ordered menus: positional fallback
        if self.menu in MENUS_ORDERED:
            return f"@pos={position}"

        # 5. Last resort: a key from `name=` if any, else anon position
        if name:
            return f"name={name}"
        return f"@anon={position}"


@dataclass
class Config:
    """Parsed configuration: ``menu_path -> [Item]`` in declaration order."""

    items_by_menu: dict[str, list[Item]] = field(default_factory=dict)

    def add(self, item: Item) -> None:
        """Append *item* to the list under its menu, creating the bucket if needed."""
        self.items_by_menu.setdefault(item.menu, []).append(item)

    def menus(self) -> list[str]:
        """Return menu paths in declaration order."""
        return list(self.items_by_menu.keys())

    def index(self, menu: str) -> dict[str, Item]:
        """Return ``{identity_key: Item}`` for one menu."""
        return {
            item.identity_key(pos): item
            for pos, item in enumerate(self.items_by_menu.get(menu, []))
        }


@dataclass(frozen=True)
class Op:
    """One operation to be emitted into the patch script.

    Kinds:
      ``add``    -- ``/menu add prop=val ...``
      ``set``    -- ``/menu set [find ...] prop=val ...`` (singletons: bare ``set``)
      ``reset``  -- ``/menu reset [find ...] prop1 prop2 ...``
                    Reverts named properties back to their RouterOS defaults.
                    Universal RouterOS console command for this purpose.
                    Used when a property exists in old but not in new and
                    isn't a boolean we can flip to ``no`` via ``set``.
      ``remove`` -- ``/menu remove [find ...]``
      ``wipe``   -- ``/menu remove [find]`` (clears entire menu;
                    used as the first op when an ordered menu changed and
                    the differ chose to replace it wholesale; identity_key="*")
    """

    kind: Literal["add", "set", "reset", "remove", "wipe"]
    menu: str
    identity_key: str
    props: dict[str, str] = field(default_factory=dict)
    """For ``add``: full prop set. For ``set``: only props that changed.

    For ``remove`` / ``wipe``: empty (identity_key carries everything needed).
    """
