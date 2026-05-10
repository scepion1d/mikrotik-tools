"""Compact emitter: render a parsed :class:`~rsc_parser.Config` as one
line per operation, suitable for /import.

Output shape::

    /menu/path
    add prop=val prop=val ...
    add prop=val ...
    /next/menu
    set [find ...] prop=val
    ...

Design choices
--------------
- One physical line per operation (no ``\\`` continuations).
- Menu path emitted once per group; items follow with no indentation.
- No banner comments, no blank lines between groups (maximum density).
- ``comment=`` properties are preserved verbatim (with the standard
  /export-style requoting). The bundle stays a faithful representation
  of the authored source so ``rsc-diff`` against a router /export
  produces a clean delta.

Property quoting follows :func:`rsc_bundle.flatten._normalize_quoting`
style: bare values when possible, quoted only when the value contains
whitespace or shell-special characters.
"""

from __future__ import annotations

import re

from rsc_parser import Config, Item


# Characters that force RouterOS to keep a value quoted in /export output.
# Same regex as flatten._NEEDS_QUOTE_RE, repeated here so compact.py is
# self-contained.
_NEEDS_QUOTE_RE = re.compile(r'[\s\[\]{}();\\"`#$<>|&?*]')


def emit(cfg: Config) -> str:
    """Render *cfg* as compact one-line-per-op .rsc text.

    Preserves all properties verbatim. Quoting is normalised to /export
    style (bare when possible, quoted when the value needs it).
    """
    lines: list[str] = []
    for menu in cfg.menus():
        items = cfg.items_by_menu[menu]
        if not items:
            continue
        lines.append(menu)
        for item in items:
            lines.append(_render_item(item))
    # Trailing newline so concat with other files is well-behaved.
    return "\n".join(lines) + "\n"


def _render_item(item: Item) -> str:
    """Render one Item as ``add prop=val ...`` or ``set [...] prop=val ...``."""
    parts: list[str] = [item.verb]

    # `set` rows carry a __selector__ prop produced by the parser; emit
    # it next so the output matches authored RouterOS syntax.
    selector = item.props.get("__selector__")
    if selector:
        parts.append(selector)

    for key, raw_value in item.props.items():
        if key == "__selector__":
            continue
        parts.append(f"{key}={_requote(_strip_quotes(raw_value))}")

    return " ".join(parts)


def _strip_quotes(value: str) -> str:
    """``"foo"`` -> ``foo``; leave bare values untouched."""
    if len(value) >= 2 and value[0] == '"' == value[-1]:
        return value[1:-1]
    return value


def _requote(value: str) -> str:
    """Quote *value* iff RouterOS /export style requires it.

    Bracket expressions (``[ ... ]``) and empty strings are special:
    - ``[expr]`` is a script-resolved expression (e.g. ``admin-mac=[/interface
      get [find name=foo] mac-address]``); wrapping it in ``"..."`` would
      change its semantics from "evaluate this expression" to "a literal
      string starting with [". Pass through verbatim.
    - Empty string keeps explicit ``""`` (e.g. ``on-event=""`` clears the
      handler; dropping the quotes would parse as a missing value).
    """
    if value == "":
        return '""'
    # Bracket expression -- never quote.
    if len(value) >= 2 and value[0] == "[" and value[-1] == "]":
        return value
    if _NEEDS_QUOTE_RE.search(value):
        # Re-emit with quotes; assumes value has no embedded `"` (the
        # parser would have rejected unbalanced quotes earlier).
        return f'"{value}"'
    return value
