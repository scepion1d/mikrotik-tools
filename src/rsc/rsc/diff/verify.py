"""Apply an .rsc patch on top of a Config and check semantic equality.

Used by the ``rsc.diff`` roundtrip mode (see :mod:`rsc.diff.cli`) to
validate that the rollforward and rollback patches actually transform a
*live* router state into a *candidate* config and back again.

NOT a production interpreter -- the simulator only handles the ops that
``rsc.diff`` currently emits, plus a few selector forms used in router
exports. Anything more exotic (script blocks, file ops, etc.) is
ignored silently.

Public API
----------
- :func:`apply_patch` -- replay an .rsc patch on a base :class:`Config`.
- :func:`residual_ops` -- re-run the differ to score what's left.

The rest of the module exposes its lex helpers (``find_item``,
``parse_props``, ``deep_copy``) so tests can drive the simulator directly.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from rsc.diff import Config, Item, Op, diff, parse_file  # noqa: F401  (parse_file re-exported for convenience)
from rsc.diff.differ import _strip_identity
from rsc.parser.parser import _logical_lines, _take_bracket, _tokenise_kv


# Matches both equality (`[find name=foo]`) and contains (`[find comment~bar]`)
# selectors. The `key` group also captures positional `@anon` / `@pos`.
_FIND_RE = re.compile(r'\[find\s+(?P<key>[\w@-]+)(?P<op>[=~])(?P<val>.+)\]')

# Selectors that mean "every non-builtin row in this menu". The `[find]` form
# is the differ's wipe sentinel for ordered menus; the `!dynamic` /
# `dynamic=no` forms are the safer variants emitted for menus that may
# contain system-protected built-in rules (e.g. firewall chains).
_WIPE_SENTINELS: frozenset[str] = frozenset({
    "[find]", "[find !dynamic]", "[find dynamic=no]",
})

# Positional selector keys produced by :class:`Item.identity_key`. They
# refer to row indices, not properties, and are handled separately.
_POSITIONAL_KEYS: frozenset[str] = frozenset({"@anon", "@pos"})


# --- public lex helpers (also imported by tests) ----------------------------


def find_item(items, selector: str | None) -> Item | None:
    """Resolve a ``[find ...]`` selector to a single :class:`Item` in *items*.

    Returns ``None`` for the wipe sentinels (``[find]`` /
    ``[find !dynamic]`` / ``[find dynamic=no]``), malformed selectors,
    or no match. Positional selectors (``@anon=N`` / ``@pos=N``) are
    resolved by index into *items*; everything else is a linear scan
    comparing the property in *items* against the selector value (``=``
    for exact match, ``~`` for substring).
    """
    parsed = _parse_find_selector(selector)
    if parsed is None:
        return None
    key, op, val = parsed

    if key in _POSITIONAL_KEYS:
        # `[find @anon=N]` / `[find @pos=N]` -- the value is just N.
        idx = int(val.lstrip("="))
        return items[idx] if 0 <= idx < len(items) else None

    for it in items:
        prop_val = _unquote(it.props.get(key, ""))
        if op == "=" and prop_val == val:
            return it
        if op == "~" and val in prop_val:
            return it
    return None


def parse_props(rest: str) -> dict[str, str]:
    """Tokenise the right-hand side of a ``add``/``set`` line into ``{key: value}``."""
    return dict(_tokenise_kv(rest))


def deep_copy(cfg: Config) -> Config:
    """Return a deep copy of *cfg* with independently mutable item dicts."""
    out = Config()
    for items in cfg.items_by_menu.values():
        for it in items:
            out.add(Item(menu=it.menu, verb=it.verb, props=dict(it.props)))
    return out


# --- patch application -----------------------------------------------------


def apply_patch(base: Config, patch_path: Path) -> Config:
    """Replay the ops in *patch_path* on top of *base* and return the result.

    Reads each non-comment, non-blank line of the patch file and dispatches
    on the verb (``remove`` / ``add`` / ``set`` / ``reset``). The simulator
    walks each menu's item list and mutates the deep copy of *base* in
    place. Used by the roundtrip mode in :mod:`rsc.diff.cli` to score
    whether the patch transforms one config into another semantically.
    """
    cfg = deep_copy(base)
    cur_menu: str | None = None

    for raw in _logical_lines(patch_path.read_text(encoding="utf-8")):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("/"):
            cur_menu = line.split()[0]
            continue
        if cur_menu is None:
            continue

        verb, _, rest = line.partition(" ")
        rest = rest.strip()
        items = cfg.items_by_menu.setdefault(cur_menu, [])

        # Dispatch by verb. Each handler mutates *items* / *cfg* in place;
        # unknown verbs are silently dropped (we only model what the
        # differ emits).
        if verb == "remove":
            _apply_remove(items, rest)
        elif verb == "add":
            cfg.add(Item(menu=cur_menu, verb="add", props=parse_props(rest)))
        elif verb == "set":
            _apply_set(cfg, cur_menu, items, rest)
        elif verb == "reset":
            _apply_reset(items, rest)

    return cfg


# --- per-verb handlers -----------------------------------------------------


def _apply_remove(items: list[Item], rest: str) -> None:
    """``remove [find ...]`` -- drop matching item(s) from *items*.

    Wipe sentinels clear the whole list (used by the differ's
    wipe-then-add strategy on ordered menus). Otherwise we resolve the
    selector to a single row and remove it; missing rows are silently
    ignored.
    """
    if rest in _WIPE_SENTINELS:
        items.clear()
        return
    if not rest.startswith("["):
        return  # malformed -- nothing to do

    bracket, after = _take_bracket(rest)
    target, _ = _resolve_target_with_rest(items, bracket, after)
    if target is not None:
        items.remove(target)


def _apply_set(
    cfg: Config, menu: str, items: list[Item], rest: str
) -> None:
    """``set [find ...] prop=val ...`` or singleton ``set prop=val ...``.

    On a `[find KEY=VAL]` selector that matches no row, materialise a
    new row carrying ``KEY=VAL`` -- this models the boot-default rows
    that RouterOS /export omits (``/user admin``, default-named
    ``etherN``, ``/ip/service telnet``, ...). Without this, the
    rollforward set would silently drop and the verifier would report
    phantom drift on every deploy.
    """
    if rest.startswith("["):
        bracket, after = _take_bracket(rest)
        target, after = _resolve_target_with_rest(items, bracket, after)
        props = parse_props(after.strip())

        if target is None:
            # Synthesise the boot-default row when the selector is an
            # equality `[find KEY=VAL]` and no row exists yet.
            seed = _selector_kv(bracket)
            if seed is not None:
                target = Item(menu=menu, verb="set", props=dict(seed))
                cfg.add(target)
    else:
        # Singleton menu: bare `set prop=val ...` against the menu's
        # one implicit row. Create the row if the menu was empty.
        props = parse_props(rest)
        if items:
            target = items[0]
        else:
            target = Item(menu=menu, verb="set", props={})
            cfg.add(target)

    if target is not None:
        target.props.update(props)


def _apply_reset(items: list[Item], rest: str) -> None:
    """``reset [find ...] prop1 prop2 ...`` or singleton ``reset prop ...``.

    Removes the named props from the matched item; missing props are
    silently ignored (matches RouterOS behaviour).
    """
    if rest.startswith("["):
        bracket, after = _take_bracket(rest)
        target, after = _resolve_target_with_rest(items, bracket, after)
        names = after.strip().split()
    else:
        names = rest.strip().split()
        target = items[0] if items else None

    if target is None:
        return
    for name in names:
        target.props.pop(name, None)


# --- selector / numbers= resolution ----------------------------------------


def _resolve_target_with_rest(
    items: list[Item], bracket: str, after: str
) -> tuple[Item | None, str]:
    """Resolve a selector + remainder into ``(item_or_None, remainder)``.

    Two paths:
      - ``[find] numbers=N``  -- positional, N indexes into *items*.
                                 Strips the ``numbers=N`` token from
                                 the returned remainder.
      - ``[find KEY=VAL]``    -- linear scan via :func:`find_item`.
                                 Remainder is returned unchanged
                                 (already past the bracket).
    """
    after = after.strip()
    if bracket == "[find]" and after.startswith("numbers="):
        return _resolve_numbers_with_rest(items, after)
    return find_item(items, bracket), after


def _resolve_numbers(items: list[Item], rest: str) -> Item | None:
    """Resolve a leading ``numbers=N`` token in *rest* to an item, or None."""
    target, _ = _resolve_numbers_with_rest(items, rest)
    return target


def _resolve_numbers_with_rest(
    items: list[Item], rest: str
) -> tuple[Item | None, str]:
    """Pop a leading ``numbers=N`` token. Returns (item_or_None, remaining_rest)."""
    head, _, tail = rest.partition(" ")
    if not head.startswith("numbers="):
        return None, rest
    try:
        idx = int(head.split("=", 1)[1])
    except ValueError:
        return None, tail
    target = items[idx] if 0 <= idx < len(items) else None
    return target, tail


# --- selector parsing ------------------------------------------------------


def _parse_find_selector(
    selector: str | None,
) -> tuple[str, str, str] | None:
    """Parse ``[find KEY OP VAL]`` into ``(key, op, val)``.

    Returns ``None`` for ``None``, wipe sentinels, malformed input, or
    selectors without a key/op/value triple. *op* is ``=`` (equality)
    or ``~`` (contains). *val* is unquoted.
    """
    if selector is None:
        return None
    sel = selector.strip()
    if sel in _WIPE_SENTINELS:
        return None
    m = _FIND_RE.match(sel)
    if not m:
        return None
    return m.group("key"), m.group("op"), _unquote(m.group("val").strip())


def _selector_kv(selector: str) -> dict[str, str] | None:
    """Return ``{key: value}`` for a ``[find KEY=VAL]`` (equality) selector.

    Returns ``None`` for wipe sentinels, contains-form selectors
    (``[find comment~tag]``), positional forms (``@anon`` / ``@pos``),
    and anything malformed -- those don't identify a single boot-default
    row, so the verifier shouldn't synthesise one.

    Used by :func:`_apply_set` to materialise built-in rows that
    /export omitted from the parsed base (e.g. ``/user admin``).
    """
    parsed = _parse_find_selector(selector)
    if parsed is None:
        return None
    key, op, val = parsed
    if op != "=" or key in _POSITIONAL_KEYS:
        return None
    return {key: val}


def _unquote(value: str) -> str:
    """Strip surrounding ``"..."`` if present; leave bare values alone."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


# --- summary / residual scoring --------------------------------------------


def menu_signature(items):
    """Multiset of identity-stripped prop dicts; used by :func:`cfg_diff_summary`."""
    return Counter(
        tuple(sorted(_strip_identity(it.props).items())) for it in items
    )


def cfg_diff_summary(a: Config, b: Config) -> list[str]:
    """Crude signature compare. Used as a fallback summary only -- the main
    verification path runs the differ and reports residual ops, which is the
    semantically authoritative answer."""
    diffs = []
    all_menus = sorted(set(a.menus()) | set(b.menus()))
    for menu in all_menus:
        ai = menu_signature(a.items_by_menu.get(menu, []))
        bi = menu_signature(b.items_by_menu.get(menu, []))
        if ai != bi:
            only_a = ai - bi
            only_b = bi - ai
            diffs.append(
                f"{menu}: in_left_only={sum(only_a.values())} "
                f"in_right_only={sum(only_b.values())}"
            )
    return diffs


def residual_ops(
    result: Config, target: Config, *,
    strict: bool = False, lenient_defaults: bool = False,
) -> list[Op]:
    """What additional ops would the differ emit to get from *result* to *target*?

    Empty list = patch round-tripped semantically. Re-using the differ here is
    the gold standard: it knows about per-menu defaults, computed properties,
    and identity matching, so we don't have to reimplement any of that just
    to verify a round-trip.
    """
    return diff(result, target, strict=strict, lenient_defaults=lenient_defaults)
