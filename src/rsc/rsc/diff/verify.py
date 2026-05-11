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


def find_item(items, selector: str | None) -> Item | None:
    """Resolve a ``[find ...]`` selector to a single :class:`Item` in *items*.

    Returns ``None`` for the wipe sentinels (``[find]`` /
    ``[find !dynamic]`` / ``[find dynamic=no]``), malformed selectors,
    or no match. Positional selectors (``@anon=N`` / ``@pos=N``) are
    resolved by index into *items*; everything else is a linear scan
    comparing the property in *items* against the selector value (``=``
    for exact match, ``~`` for substring).
    """
    if selector is None:
        return None
    sel = selector.strip()
    if sel in ("[find]", "[find !dynamic]", "[find dynamic=no]"):
        return None  # wipe sentinel
    m = _FIND_RE.match(sel)
    if not m:
        return None
    key, op, val = m.group("key"), m.group("op"), m.group("val").strip()
    if val.startswith('"') and val.endswith('"'):
        val = val[1:-1]
    if key in ("@anon", "@pos"):
        idx = int(val.lstrip("="))
        return items[idx] if 0 <= idx < len(items) else None
    for it in items:
        v = it.props.get(key, "")
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        if (op == "=" and v == val) or (op == "~" and val in v):
            return it
    return None


def parse_props(rest: str) -> dict[str, str]:
    """Tokenise the right-hand side of a ``add``/``set`` line into ``{key: value}``."""
    return {k: v for k, v in _tokenise_kv(rest)}


def _selector_kv(selector: str) -> dict[str, str] | None:
    """Return ``{key: value}`` for a ``[find KEY=VAL]`` (equality) selector.

    Returns ``None`` for wipe sentinels, contains-form selectors
    (``[find comment~tag]``), positional forms (``@anon`` / ``@pos``),
    and anything malformed -- those don't identify a single boot-default
    row, so the verifier shouldn't synthesise one.

    Used by :func:`apply_patch` to materialise built-in rows that
    /export omitted from the parsed base (e.g. ``/user admin``).
    """
    sel = selector.strip()
    if sel in ("[find]", "[find !dynamic]", "[find dynamic=no]"):
        return None
    m = _FIND_RE.match(sel)
    if not m:
        return None
    key, op, val = m.group("key"), m.group("op"), m.group("val").strip()
    if op != "=" or key in ("@anon", "@pos"):
        return None
    if val.startswith('"') and val.endswith('"'):
        val = val[1:-1]
    return {key: val}


def deep_copy(cfg: Config) -> Config:
    """Return a deep copy of *cfg* with independently mutable item dicts."""
    out = Config()
    for items in cfg.items_by_menu.values():
        for it in items:
            out.add(Item(menu=it.menu, verb=it.verb, props=dict(it.props)))
    return out


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
    text = patch_path.read_text(encoding="utf-8")
    for raw in _logical_lines(text):
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

        if verb == "remove":
            # Wipe sentinels: bare `[find]` and the safer `[find !dynamic]` /
            # `[find dynamic=no]` forms emitted for menus that may contain
            # system-protected built-in rules (e.g. firewall chains).
            if rest in ("[find]", "[find !dynamic]", "[find dynamic=no]"):
                items.clear()
            elif rest.startswith("["):
                bracket, after = _take_bracket(rest)
                # `remove [find] numbers=N` -- positional remove, used when
                # the emitter falls back to numbered addressing for menus
                # without a stable identity property.
                if bracket == "[find]" and after.strip().startswith("numbers="):
                    target = _resolve_numbers(items, after.strip())
                else:
                    target = find_item(items, bracket)
                if target is not None:
                    items.remove(target)

        elif verb == "add":
            cfg.add(Item(menu=cur_menu, verb="add", props=parse_props(rest)))

        elif verb == "set":
            if rest.startswith("["):
                bracket, after = _take_bracket(rest)
                # `set [find] numbers=N prop=val ...` -- positional set.
                if bracket == "[find]" and after.strip().startswith("numbers="):
                    target, after = _resolve_numbers_with_rest(items, after.strip())
                else:
                    target = find_item(items, bracket)
                props = parse_props(after.strip())
                # Built-in row that /export omits (e.g. /user admin,
                # /interface/ethernet etherN, /ip/service telnet): the
                # row exists on the live router but isn't in the parsed
                # base Config because /export skipped it. RouterOS's
                # `set [find KEY=VAL]` finds and updates the boot-default
                # row; mirror that here by synthesising the row with the
                # selector's KEY=VAL surfaced as an identity prop. Without
                # this, the rollforward set silently drops, residual_ops
                # re-emits the same set, and the verifier reports phantom
                # drift on every deploy.
                if target is None:
                    selector_props = _selector_kv(bracket)
                    if selector_props is not None:
                        target = Item(
                            menu=cur_menu, verb="set",
                            props=dict(selector_props),
                        )
                        cfg.add(target)
            else:
                props = parse_props(rest)
                if items:
                    target = items[0]
                else:
                    target = Item(menu=cur_menu, verb="set", props={})
                    cfg.add(target)
            if target is not None:
                target.props.update(props)

        elif verb == "reset":
            if rest.startswith("["):
                bracket, after = _take_bracket(rest)
                if bracket == "[find]" and after.strip().startswith("numbers="):
                    target, after = _resolve_numbers_with_rest(items, after.strip())
                else:
                    target = find_item(items, bracket)
                names = after.strip().split()
            else:
                names = rest.strip().split()
                target = items[0] if items else None
            if target is not None:
                for n in names:
                    target.props.pop(n, None)

    return cfg


def _resolve_numbers(items, rest: str) -> Item | None:
    """Resolve a leading ``numbers=N`` token in *rest* to an item, or None."""
    target, _ = _resolve_numbers_with_rest(items, rest)
    return target


def _resolve_numbers_with_rest(items, rest: str) -> tuple[Item | None, str]:
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


def residual_ops(result: Config, target: Config, *, strict: bool = False, lenient_defaults: bool = False) -> list[Op]:
    """What additional ops would the differ emit to get from *result* to *target*?

    Empty list = patch round-tripped semantically. Re-using the differ here is
    the gold standard: it knows about per-menu defaults, computed properties,
    and identity matching, so we don't have to reimplement any of that just
    to verify a round-trip.
    """
    return diff(result, target, strict=strict, lenient_defaults=lenient_defaults)
