"""Diff two parsed Configs into a list of Ops."""

from __future__ import annotations

from .model import MENUS_SINGLETON, Config, Item, Op


# Props that should never appear in `set` operations because they identify
# the item (changing them = different item) or are read-only.
IDENTITY_PROPS: frozenset[str] = frozenset({"__selector__", "default-name"})


def diff(old: Config, new: Config) -> list[Op]:
    ops: list[Op] = []
    all_menus = sorted(set(old.menus()) | set(new.menus()))

    for menu in all_menus:
        ops.extend(_diff_menu(menu, old.index(menu), new.index(menu)))

    return ops


def _diff_menu(
    menu: str, old_idx: dict[str, Item], new_idx: dict[str, Item]
) -> list[Op]:
    ops: list[Op] = []

    old_keys = set(old_idx)
    new_keys = set(new_idx)

    # Removed: in old but not new. Singletons can't be removed.
    if menu not in MENUS_SINGLETON:
        for key in sorted(old_keys - new_keys):
            ops.append(Op(kind="remove", menu=menu, identity_key=key))

    # Added: in new but not old. Singletons emit `set` instead.
    for key in sorted(new_keys - old_keys):
        item = new_idx[key]
        if menu in MENUS_SINGLETON:
            ops.append(
                Op(
                    kind="set",
                    menu=menu,
                    identity_key=key,
                    props=_strip_identity(item.props),
                )
            )
        else:
            ops.append(
                Op(
                    kind="add",
                    menu=menu,
                    identity_key=key,
                    props=_strip_identity(item.props),
                )
            )

    # Common: same key, possibly different props.
    for key in sorted(old_keys & new_keys):
        old_item = old_idx[key]
        new_item = new_idx[key]
        changed = _changed_props(old_item.props, new_item.props)
        if changed:
            ops.append(
                Op(kind="set", menu=menu, identity_key=key, props=changed)
            )

    return ops


def _strip_identity(props: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in props.items() if k not in IDENTITY_PROPS}


def _changed_props(
    old_props: dict[str, str], new_props: dict[str, str]
) -> dict[str, str]:
    """Return only the props in `new_props` that differ from `old_props`.

    Identity props are excluded. Props removed from new are NOT emitted as
    `prop=` (RouterOS unset semantics differ per menu); flagged in roadmap.
    """
    changed: dict[str, str] = {}
    for key, new_value in new_props.items():
        if key in IDENTITY_PROPS:
            continue
        if old_props.get(key) != new_value:
            changed[key] = new_value
    return changed
