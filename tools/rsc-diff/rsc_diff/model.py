"""Data model for parsed RouterOS configs and diff operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# Menus where `name=` is the user-set identifier (not auto-generated).
# Items here use `name=` as their identity_key when present.
MENUS_WITH_NAME: frozenset[str] = frozenset(
    {
        "/interface/list",
        "/interface/bridge",
        "/interface/wifi/datapath",
        "/interface/wifi/security",
        "/interface/wifi/channel",
        "/interface/wifi/configuration",
        "/ip/pool",
        "/ip/dhcp-server",
        "/system/script",
    }
)

# Menus where items are positional (rules in chains). Identity falls back
# to the comment, then to position.
MENUS_ORDERED: frozenset[str] = frozenset(
    {
        "/ip/firewall/filter",
        "/ip/firewall/nat",
        "/ip/firewall/mangle",
        "/ip/firewall/raw",
        "/ipv6/firewall/filter",
        "/ipv6/firewall/nat",
        "/ipv6/firewall/mangle",
        "/ipv6/firewall/raw",
    }
)

# Menus that are settings-blocks (single implicit item, only `set` makes sense).
MENUS_SINGLETON: frozenset[str] = frozenset(
    {
        "/ip/dns",
        "/ip/neighbor/discovery-settings",
        "/system/clock",
        "/system/identity",
        "/disk/settings",
        "/tool/mac-server",
        "/tool/mac-server/mac-winbox",
        "/tool/mac-server/ping",
        "/system/routerboard/mode-button",
        "/system/routerboard/wps-button",
    }
)


# Tokens that hint at a stable identifier inside a comment.
IAC_PREFIX = "iac."


@dataclass(frozen=True)
class Item:
    """One configuration item under a menu path.

    `verb` records how the item appeared in source (add / set). It is
    informational only -- the differ recomputes the right verb for each op.
    """

    menu: str
    verb: Literal["add", "set"]
    props: dict[str, str] = field(default_factory=dict)

    def identity_key(self, position: int) -> str:
        """Compute the identity key under this item's menu.

        Resolution order:
          1. name=iac.x.y if menu accepts a `name`.
          2. iac.x.y token inside the comment.
          3. default-name=etherN for `set [find default-name=...]` items.
          4. Position fallback for ordered menus.
          5. menu path for singletons.
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

        # 5. Last resort: a hash-like key from sorted props
        # (good enough for items that should never collide)
        if name:
            return f"name={name}"
        return f"@anon={position}"


@dataclass
class Config:
    """Parsed configuration: menu_path -> [Item] in declaration order."""

    items_by_menu: dict[str, list[Item]] = field(default_factory=dict)

    def add(self, item: Item) -> None:
        self.items_by_menu.setdefault(item.menu, []).append(item)

    def menus(self) -> list[str]:
        return list(self.items_by_menu.keys())

    def index(self, menu: str) -> dict[str, Item]:
        """Return {identity_key: Item} for one menu."""
        return {
            item.identity_key(pos): item
            for pos, item in enumerate(self.items_by_menu.get(menu, []))
        }


@dataclass(frozen=True)
class Op:
    """One operation to be emitted into the patch script."""

    kind: Literal["add", "set", "remove"]
    menu: str
    identity_key: str
    props: dict[str, str] = field(default_factory=dict)
    """For add: full prop set. For set: only props that changed.
    For remove: empty (identity_key carries everything needed)."""
