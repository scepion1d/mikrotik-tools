"""Diff two parsed Configs into a list of Ops."""

from __future__ import annotations

from .defaults import is_computed, is_default
from .model import MENUS_ORDERED, MENUS_SINGLETON, Config, Item, Op


# Props that should never appear in `set` operations because they identify
# the item (changing them = different item) or are read-only.
IDENTITY_PROPS: frozenset[str] = frozenset({"__selector__", "default-name"})


# Built-in rows that must never be removed: the row exists from boot and
# RouterOS would either reject the remove or break administration if it
# succeeded. The differ skips emitting `remove [find ...]` ops whose
# `(menu, identity_key)` matches an entry here.
#
# Entries use the same identity_key format the rest of the differ
# produces (e.g. "name=admin"), so a candidate that simply omits the row
# from /export results in no destructive op.
_PROTECTED_ROWS: frozenset[tuple[str, str]] = frozenset({
    # /user admin -- removing the only admin would lock out the operator.
    ("/user", "name=admin"),
})


def diff(old: Config, new: Config, *, strict: bool = False, lenient_defaults: bool = False) -> list[Op]:
    """Compute ops to transform *old* into *new*.

    Args:
        old, new: Configs produced by parse_file/parse_text.
        strict: When True, disable per-menu defaults + computed-property
            normalisation. Use this for the very first diff against an
            unfamiliar router so any silent default-table miscalibration
            surfaces as visible drift.
        lenient_defaults: When True, asymmetric drift where one side has
            an explicit "neutral" value (no/false/none/0/0s/empty) and
            the other side is silent gets suppressed. Useful when an
            authored config sets common defaults explicitly but the live
            router /export omits them. Risk: hides real drift if the
            actual default is non-neutral. Prefer adding an explicit
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


# Menus whose `set` ops reference NAMES of items created in OTHER menus.
# Emitted last (after the menus that create those items), so the router
# can validate the reference at apply time.
#
# Example: /disk/settings.auto-media-interface=iac.vlan.int requires that
# iac.vlan.int already exist on /interface/vlan. Default alphabetic order
# would emit /disk/settings before /interface/vlan -> "input does not match
# any value of auto-media-interface".
#
# /interface/wifi is here because its rows carry `configuration=` /
# `master-interface=` references to /interface/wifi/{configuration,...}
# items, and `/interface/wifi` sorts BEFORE its sub-paths alphabetically
# (shorter string wins) so without bumping it would activate before its
# configurations exist.
#
# Within this group, alphabetic order is preserved (relative ordering only
# matters when one late menu references another, which is rare).
_MENU_LATE: frozenset[str] = frozenset({
    "/disk/settings",
    "/interface/wifi",
    "/ip/neighbor/discovery-settings",
    "/tool/mac-server",
    "/tool/mac-server/mac-winbox",
    "/tool/mac-server/ping",
    "/system/routerboard/mode-button",
    "/system/routerboard/wps-button",
})


def _menu_sort_key(menu: str) -> tuple[int, str]:
    """Sort menus alphabetically, but push reference-heavy "settings" menus
    to the end so their ops run after the menus that create the referenced
    items.

    Returns (bucket, menu) where bucket=0 is normal and bucket=1 is late.
    """
    return (1 if menu in _MENU_LATE else 0, menu)


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
    single insert shifts every subsequent rule. We sidestep all of it by
    wipe-then-add: when ANY difference is detected, emit one
    ``remove [find]`` followed by an ``add`` per rule in declaration
    order. Everything else uses per-key matching.
    """
    if menu in MENUS_ORDERED:
        return _diff_menu_replace(menu, old_idx, new_idx, strict=strict)

    return _diff_menu_per_key(
        menu, old_idx, new_idx, strict=strict, lenient_defaults=lenient_defaults,
    )


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
    position is the right choice for ordered menus -- two configs with the
    same rules in a different order are NOT equivalent on RouterOS.
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
                # don't exist yet on the router, so identity props (name,
                # default-name, etc.) must stay in the prop list to
                # create them. Don't pass identity_key here.
                props=_emit_props(menu, item.props, strict=strict),
            )
        )
    return ops


def _ordered_equal(
    menu: str, a: list[Item], b: list[Item], *, strict: bool
) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if (
            _normalise_props(menu, x.props, strict=strict)
            != _normalise_props(menu, y.props, strict=strict)
        ):
            return False
    return True


def _normalise_props(
    menu: str, props: dict[str, str], *, strict: bool
) -> dict[str, str | None]:
    """Strip identity, computed (always), and defaults (unless strict).

    Returns the value-normalised dict suitable for equality comparison.
    Properties whose value matches the documented default for this menu
    are mapped to ``None`` (treated as "absent"), so source's explicit
    `protocol-mode=rstp` matches export's omission.
    """
    out: dict[str, str | None] = {}
    for k, v in props.items():
        if k in IDENTITY_PROPS:
            continue
        if is_computed(menu, k):
            continue
        normalised = _normalise_value(v)
        if not strict and normalised is not None and is_default(menu, k, normalised):
            continue
        out[k] = normalised
    return out


def _emit_props(
    menu: str, props: dict[str, str], *, strict: bool,
    identity_key: str | None = None,
) -> dict[str, str]:
    """Strip identity + computed + (unless strict) default-valued props.

    Used when emitting an `add` op so the patch doesn't restate values
    that the router would interpret as no-ops anyway. Returns the raw
    string values (no quote-stripping) since these are written back out.

    *identity_key* (when given) is the diff-op's selector key string
    (e.g. ``"name=admin"``). The matching prop is dropped from the output
    -- it would be redundant with the ``[find ...]`` selector the emitter
    will render, and worse, on built-in rows like ``/user admin`` it
    would render as ``set [find name=admin] name=admin password=...``
    which RouterOS rejects (can't ``set`` an identity field).
    """
    # Map "name=admin" -> ("name", "admin") for the redundancy check below.
    selector_key = selector_val = None
    if identity_key and "=" in identity_key and not identity_key.startswith("@"):
        selector_key, _, selector_val = identity_key.partition("=")

    out: dict[str, str] = {}
    for k, v in props.items():
        if k in IDENTITY_PROPS:
            continue
        if is_computed(menu, k):
            continue
        if k == selector_key and _normalise_value(v) == selector_val:
            # Already conveyed by [find KEY=VAL]; emitting it would be
            # a no-op or worse, an attempt to re-set an identity field.
            continue
        if not strict:
            normalised = _normalise_value(v)
            if normalised is not None and is_default(menu, k, normalised):
                continue
        out[k] = v
    return out


def _diff_menu_per_key(
    menu: str,
    old_idx: dict[str, Item],
    new_idx: dict[str, Item],
    *,
    strict: bool,
    lenient_defaults: bool = False,
) -> list[Op]:
    ops: list[Op] = []

    old_keys = set(old_idx)
    new_keys = set(new_idx)

    # Removed: in old but not new. Singletons can't be removed.
    if menu not in MENUS_SINGLETON:
        # IMPORTANT: positional selectors (@anon / @pos) refer to live row
        # numbers at apply time. Removing @anon=0 shifts every subsequent
        # row down by one, breaking any later @anon=N selector. So emit
        # positional removes in DESCENDING order; named removes keep their
        # alphabetical order (those are stable under shifts).
        for key in sorted(old_keys - new_keys, key=_remove_sort_key):
            if (menu, key) in _PROTECTED_ROWS:
                # Built-in row (e.g. /user admin). Removing it would
                # lock the operator out / break admin access. Skip.
                continue
            ops.append(Op(kind="remove", menu=menu, identity_key=key))

    # Added: in new but not old. Three sub-cases:
    #   - singleton menu        -> always `set` (one implicit row)
    #   - item.verb == "set"    -> still `set [find ...]` even though the
    #                              menu has no matching row in old. This
    #                              handles built-in rows (e.g. /user admin,
    #                              /interface/ethernet etherN, /ip/service
    #                              telnet) that exist on the live router
    #                              but are omitted from /export when no
    #                              property differs from default. The
    #                              authored side's `set [find ...]` is
    #                              the right command regardless.
    #   - otherwise             -> `add prop=val ...`
    for key in sorted(new_keys - old_keys):
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
                    props=_emit_props(menu, item.props, strict=strict,
                                      identity_key=key),
                )
            )
        else:
            # `add ...` -- this row doesn't exist on the router yet, so
            # ALL props (including identity ones like name=) must be
            # present to create it. Don't pass identity_key.
            ops.append(
                Op(
                    kind="add",
                    menu=menu,
                    identity_key=key,
                    props=_emit_props(menu, item.props, strict=strict),
                )
            )

    # Common: same key, possibly changed props (added / changed / removed).
    for key in sorted(old_keys & new_keys):
        old_item = old_idx[key]
        new_item = new_idx[key]
        changed, removed = _prop_changes(
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


def _strip_identity(props: dict[str, str]) -> dict[str, str]:
    """Drop identity-only keys (``__selector__``, ``default-name``).

    Used by :mod:`rsc_diff.verify`'s :func:`menu_signature` to compute a
    structural fingerprint that's stable under identity-only changes.
    """
    return {k: v for k, v in props.items() if k not in IDENTITY_PROPS}


def _remove_sort_key(key: str) -> tuple[int, int | str]:
    """Sort key for ``remove`` ops within a menu.

    Positional selectors (@anon / @pos) sort first, in DESCENDING numeric
    order, so removing higher-indexed items first leaves lower indices
    stable for any subsequent set/reset op that targets them. Named
    selectors fall back to alphabetical (stable under shifts).
    """
    if key.startswith("@anon=") or key.startswith("@pos="):
        n = int(key.split("=", 1)[1])
        return (0, -n)
    return (1, key)


# Boolean values RouterOS recognises. When a property is removed AND the old
# value was one of these, we can express the removal as `prop=no` (the default
# for these flags) rather than `reset prop`. Reads more idiomatic in patches
# and avoids any per-menu quirks around what `reset` accepts.
_BOOLEAN_VALUES: frozenset[str] = frozenset({"yes", "no", "true", "false"})


# "Neutral" property values: when we see an *explicit* prop with one of these
# values on one side and the *same prop missing* on the other side, the
# `lenient_defaults` heuristic treats them as equal. The assumption is that
# RouterOS omits a prop from /export iff it equals its default, and these
# tokens are the most common defaults across the menu universe.
#
# RISK: if the actual default differs (e.g. some flag defaults to `yes`),
# this hides real drift. Off by default; opt in via diff(lenient_defaults=True)
# or `rsc-diff --lenient`. Prefer adding an explicit MENU_DEFAULTS entry once
# the true default is verified.
_NEUTRAL_VALUES: frozenset[str] = frozenset({
    "no", "false", "none", "0", "0s", "",
})


def _prop_changes(
    menu: str,
    old_props: dict[str, str],
    new_props: dict[str, str],
    *,
    strict: bool,
    lenient_defaults: bool = False,
) -> tuple[dict[str, str], list[str]]:
    """Compare prop dicts. Returns (changed_or_added_props, removed_prop_names).

    Identity, computed, and (unless strict) default-valued properties are
    treated as absent on BOTH sides. Removed boolean-typed props (where
    the old value was yes/no/true/false) are folded into the changed-set
    as `prop=no` rather than reported as removed -- a `set` with the
    default value is more idiomatic and avoids `reset` edge cases.

    Property values are compared after _normalise_value() so authored
    `comment="LAN"` matches export-emitted `comment=LAN` (RouterOS strips
    quotes when not needed).

    When *lenient_defaults* is True, asymmetric drift where one side has
    `prop=NEUTRAL` and the other side is missing `prop` is suppressed.
    See _NEUTRAL_VALUES for the value set.
    """
    old_norm = _normalise_props(menu, old_props, strict=strict)
    new_norm = _normalise_props(menu, new_props, strict=strict)

    changed: dict[str, str] = {}
    for key, new_value in new_norm.items():
        old_value = old_norm.get(key)
        if old_value == new_value:
            continue
        if (
            lenient_defaults
            and old_value is None
            and new_value in _NEUTRAL_VALUES
        ):
            # candidate has explicit neutral, live is silent -> assume default
            continue
        # Emit the RAW value from new_props (preserves quoting/format).
        changed[key] = new_props[key]

    removed: list[str] = []
    for key in sorted(set(old_norm) - set(new_norm)):
        old_value = old_norm[key]
        if lenient_defaults and old_value in _NEUTRAL_VALUES:
            # live has explicit neutral, candidate is silent -> assume default
            continue
        if old_value in _BOOLEAN_VALUES:
            # Fold boolean removal into the `set` op as the default value.
            changed[key] = "no" if old_value in ("yes", "true") else "yes"
        else:
            removed.append(key)
    return changed, removed


def _normalise_value(value: str | None) -> str | None:
    """Strip surrounding quotes for value comparison.

    RouterOS quotes strings only when needed (whitespace, special chars).
    `comment="LAN"` and `comment=LAN` are equivalent on the router; this
    normalisation makes them equal during diff.
    """
    if value is None:
        return None
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in ('"', "'")
    ):
        return value[1:-1]
    return value
