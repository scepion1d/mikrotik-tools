"""Diff two parsed Configs into a list of Ops.

Owns the **structural** algorithm:

- traversal order across menus,
- per-menu strategy (wipe-then-add for ordered menus, per-key matching
  otherwise),
- which ops are emitted (``add`` / ``set`` / ``remove`` / ``reset`` /
  ``wipe``),
- protected built-in rows that must never be removed.

The **per-property** concerns (normalisation, IP canonicalisation,
default + computed handling, value comparison) live in
:mod:`rsc.diff.props`.
"""

from __future__ import annotations

from .model import MENU_ORDER, MENUS_ORDERED, MENUS_SINGLETON, Config, Item, Op
from .props import (
    IDENTITY_PROPS,
    emit_props,
    normalise_props,
    normalise_value,
    prop_changes,
)


# Built-in rows that must never be removed: the row exists from boot
# and RouterOS would either reject the remove or break administration
# if it succeeded. The differ skips emitting ``remove [find ...]`` ops
# whose ``(menu, identity_key)`` matches an entry here.
#
# Entries use the same identity_key format the rest of the differ
# produces (e.g. ``"name=admin"``), so a candidate that simply omits
# the row from /export results in no destructive op.
_PROTECTED_ROWS: frozenset[tuple[str, str]] = frozenset({
    # /user admin -- removing the only admin would lock out the operator.
    ("/user", "name=admin"),
})


# Lookup table built from MENU_ORDER for O(1) sort-key resolution.
# Menus listed in MENU_ORDER get their canonical index; menus not
# listed fall into a final alphabetic bucket emitted afterwards.
_MENU_ORDER_INDEX: dict[str, int] = {m: i for i, m in enumerate(MENU_ORDER)}


# --- public entry point ----------------------------------------------------


def diff(
    old: Config, new: Config, *,
    strict: bool = False, lenient_defaults: bool = False,
) -> list[Op]:
    """Compute ops to transform *old* into *new*.

    Args:
        old, new: Configs produced by parse_file/parse_text.
        strict: When True, disable per-menu defaults + computed-property
            normalisation. Use this for the very first diff against an
            unfamiliar router so any silent default-table miscalibration
            surfaces as visible drift.
        lenient_defaults: When True, asymmetric drift where one side
            has an explicit "neutral" value (no/false/none/0/0s/empty)
            and the other side is silent gets suppressed. Useful when
            an authored config sets common defaults explicitly but the
            live router /export omits them. Risk: hides real drift if
            the actual default is non-neutral. Prefer adding an explicit
            MENU_DEFAULTS entry once verified. Ignored when strict=True.
    """
    ops: list[Op] = []
    all_menus = sorted(set(old.menus()) | set(new.menus()), key=_menu_sort_key)

    for menu in all_menus:
        ops.extend(
            _diff_menu(
                menu, old.index(menu), new.index(menu),
                strict=strict, lenient_defaults=lenient_defaults,
            )
        )

    return ops


# --- per-menu dispatch -----------------------------------------------------


def _menu_sort_key(menu: str) -> tuple[int, int, str]:
    """Sort menus by canonical apply-time order.

    Returns ``(bucket, index, menu)`` where:
      - bucket=0: menu is in :data:`MENU_ORDER` -> use its index there.
        These emit first, in the order that satisfies cross-menu
        references (e.g., ``/interface/wifi/datapath`` before
        ``/interface/wifi/configuration``).
      - bucket=1: menu is unknown -> alphabetic, emitted after every
        canonically-ordered menu. Safe default for menus that don't
        reference anything (or are only referenced themselves).
    """
    idx = _MENU_ORDER_INDEX.get(menu)
    if idx is None:
        return (1, 0, menu)
    return (0, idx, menu)


def _diff_menu(
    menu: str,
    old_idx: dict[str, Item],
    new_idx: dict[str, Item],
    *,
    strict: bool,
    lenient_defaults: bool,
) -> list[Op]:
    """Dispatch a single menu's diff to the right strategy.

    Ordered menus (firewall chains) are notoriously fragile to diff in
    place: rules are positional, identity by comment is optional, and a
    single insert shifts every subsequent rule. We sidestep all of it
    by wipe-then-add: when ANY difference is detected, emit one
    ``remove [find]`` followed by an ``add`` per rule in declaration
    order. Everything else uses per-key matching.
    """
    if menu in MENUS_ORDERED:
        return _diff_menu_replace(menu, old_idx, new_idx, strict=strict)

    return _diff_menu_per_key(
        menu, old_idx, new_idx,
        strict=strict, lenient_defaults=lenient_defaults,
    )


# --- ordered-menu strategy: wipe-then-add ----------------------------------


def _diff_menu_replace(
    menu: str,
    old_idx: dict[str, Item],
    new_idx: dict[str, Item],
    *,
    strict: bool,
) -> list[Op]:
    """Wipe-then-add for ordered menus. No-op if old and new are identical.

    Equality is defined as same number of items in the same order with
    identical (identity-stripped, normalised) prop dicts. Comparing by
    position is the right choice for ordered menus -- two configs with
    the same rules in a different order are NOT equivalent on RouterOS.
    """
    old_items = list(old_idx.values())
    new_items = list(new_idx.values())
    if _ordered_equal(menu, old_items, new_items, strict=strict):
        return []

    ops: list[Op] = [Op(kind="wipe", menu=menu, identity_key="*")]
    for pos, item in enumerate(new_items):
        ops.append(
            Op(
                kind="add",
                menu=menu,
                identity_key=item.identity_key(pos),
                # Wipe-then-add always emits `add` ops -- the new rows
                # don't exist yet on the router, so identity props
                # (name, default-name, etc.) must stay in the prop list
                # to create them. Don't pass identity_key here.
                props=emit_props(menu, item.props, strict=strict),
            )
        )
    return ops


def _ordered_equal(
    menu: str, a: list[Item], b: list[Item], *, strict: bool,
) -> bool:
    """Two ordered-menu item lists semantically equal?

    Position-sensitive (firewall rules at index N must match), with the
    same prop normalisation applied as the per-key strategy.
    """
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if (
            normalise_props(menu, x.props, strict=strict)
            != normalise_props(menu, y.props, strict=strict)
        ):
            return False
    return True


# --- general-menu strategy: per-key matching -------------------------------


def _diff_menu_per_key(
    menu: str,
    old_idx: dict[str, Item],
    new_idx: dict[str, Item],
    *,
    strict: bool,
    lenient_defaults: bool = False,
) -> list[Op]:
    """Per-identity-key matching: emit remove/add/set/reset as needed."""
    old_keys = set(old_idx)
    new_keys = set(new_idx)

    ops: list[Op] = []
    ops.extend(_emit_removes(menu, old_keys - new_keys))
    ops.extend(_emit_creates(menu, new_idx, new_keys - old_keys, strict=strict))
    ops.extend(
        _emit_updates(
            menu, old_idx, new_idx, old_keys & new_keys,
            strict=strict, lenient_defaults=lenient_defaults,
        )
    )

    return ops


def _emit_removes(menu: str, removed_keys: set[str]) -> list[Op]:
    """Emit ``remove [find KEY=VAL]`` for keys in old but not new.

    Singletons can't be removed (the row is implicit). Positional
    selectors (@anon / @pos) are emitted in DESCENDING numeric order
    so removing higher-indexed items first leaves lower indices stable
    for any subsequent set/reset op. Named selectors keep their
    alphabetical order (stable under shifts).
    """
    if menu in MENUS_SINGLETON:
        return []

    ops: list[Op] = []
    for key in sorted(removed_keys, key=_remove_sort_key):
        if (menu, key) in _PROTECTED_ROWS:
            # Built-in row (e.g. /user admin). Removing it would lock
            # the operator out / break admin access. Skip.
            continue
        ops.append(Op(kind="remove", menu=menu, identity_key=key))
    return ops


def _emit_creates(
    menu: str, new_idx: dict[str, Item], created_keys: set[str], *,
    strict: bool,
) -> list[Op]:
    """Emit ``add`` or ``set [find ...]`` for keys in new but not old.

    Three sub-cases:

    - **singleton menu** -> always ``set`` (one implicit row).
    - **item.verb == "set"** -> still ``set [find ...]`` even though the
      menu has no matching row in old. This handles built-in rows
      (e.g. ``/user admin``, ``/interface/ethernet etherN``,
      ``/ip/service telnet``) that exist on the live router but are
      omitted from /export when no property differs from default. The
      authored side's ``set [find ...]`` is the right command regardless.
    - **otherwise** -> ``add prop=val ...`` (a brand-new row).
    """
    ops: list[Op] = []
    for key in sorted(created_keys):
        item = new_idx[key]
        if menu in MENUS_SINGLETON or item.verb == "set":
            # `set [find KEY=VAL] ...` -- pass identity_key so the
            # synthetic KEY=VAL prop the parser injected gets stripped
            # (it's already conveyed by the [find ...] selector).
            ops.append(
                Op(
                    kind="set",
                    menu=menu,
                    identity_key=key,
                    props=emit_props(
                        menu, item.props,
                        strict=strict, identity_key=key,
                    ),
                )
            )
        else:
            # `add ...` -- this row doesn't exist on the router yet,
            # so ALL props (including identity ones like name=) must
            # be present to create it. Don't pass identity_key.
            ops.append(
                Op(
                    kind="add",
                    menu=menu,
                    identity_key=key,
                    props=emit_props(menu, item.props, strict=strict),
                )
            )
    return ops


def _emit_updates(
    menu: str,
    old_idx: dict[str, Item],
    new_idx: dict[str, Item],
    common_keys: set[str],
    *,
    strict: bool,
    lenient_defaults: bool,
) -> list[Op]:
    """Emit ``set`` and/or ``reset`` for rows present on both sides
    whose props differ.

    A single row can produce up to two ops: a ``set`` for changed/added
    props and a ``reset`` for non-boolean removed props. Boolean
    removals are folded into the ``set`` as ``prop=no`` by
    :func:`prop_changes`.
    """
    ops: list[Op] = []
    for key in sorted(common_keys):
        old_item = old_idx[key]
        new_item = new_idx[key]
        changed, removed = prop_changes(
            menu, old_item.props, new_item.props,
            strict=strict, lenient_defaults=lenient_defaults,
        )
        if changed:
            ops.append(
                Op(kind="set", menu=menu, identity_key=key, props=changed)
            )
        if removed:
            ops.append(
                Op(
                    kind="reset",
                    menu=menu,
                    identity_key=key,
                    props={p: "" for p in removed},
                )
            )
    return ops


# --- structural helpers ----------------------------------------------------


def _strip_identity(props: dict[str, str]) -> dict[str, str]:
    """Drop identity-only keys (``__selector__``, ``default-name``).

    Used by :mod:`rsc.diff.verify`'s :func:`menu_signature` to compute
    a structural fingerprint that's stable under identity-only changes.
    """
    return {k: v for k, v in props.items() if k not in IDENTITY_PROPS}


def _remove_sort_key(key: str) -> tuple[int, int | str]:
    """Sort key for ``remove`` ops within a menu.

    Positional selectors (@anon / @pos) sort first, in DESCENDING
    numeric order, so removing higher-indexed items first leaves lower
    indices stable for any subsequent set/reset op that targets them.
    Named selectors fall back to alphabetical (those are stable under
    shifts).
    """
    if key.startswith("@anon=") or key.startswith("@pos="):
        n = int(key.split("=", 1)[1])
        return (0, -n)
    return (1, key)


# --- back-compat aliases ---------------------------------------------------
# Underscore-prefixed names imported by tests and :mod:`rsc.diff.verify`.

_normalise_value = normalise_value
_normalise_props = normalise_props
_emit_props = emit_props
_prop_changes = prop_changes
