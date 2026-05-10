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
- ``comment=`` properties are minified to just the first ``iac.*`` token
  in their value when one is present; the human-readable suffix is
  dropped. Items without an iac-namespace token in their comment have
  the comment dropped entirely. Pass ``minify_comments=False`` to
  preserve the original comment text verbatim.

Why preserve iac.id tokens
--------------------------
``rsc-diff`` matches items between two configs by identity. For items
where identity comes from ``comment="iac.x.y -- ..."`` (e.g.
``/ip/dhcp-server/lease`` rows, ``/ipv6/firewall/address-list`` rows,
``/interface/list/member`` rows), losing the ``iac.x.y`` token would
collapse identity to position, making any insert in the middle of an
unordered menu look like every subsequent row changed. The minified
``comment=iac.x.y`` keeps the diff reliable while shedding the noise.

Property quoting follows :func:`rsc_bundle.flatten._normalize_quoting`
style: bare values when possible, quoted only when the value contains
whitespace or shell-special characters. The minified ``iac.id`` tokens
are bare by construction (no spaces).
"""

from __future__ import annotations

import re

from rsc_parser import IAC_PREFIX, Config, Item


# Characters that force RouterOS to keep a value quoted in /export output.
# Same regex as flatten._NEEDS_QUOTE_RE, repeated here so compact.py is
# self-contained.
_NEEDS_QUOTE_RE = re.compile(r'[\s\[\]{}();\\"`#$<>|&?*]')


def emit(cfg: Config, *, minify_comments: bool = True) -> str:
    """Render *cfg* as compact one-line-per-op .rsc text.

    Args:
        cfg: parsed config (from :func:`rsc_parser.parse_text`).
        minify_comments: when True (default), collapse any ``comment=``
            value containing an iac-namespace token to just that token
            and drop comments without one. When False, preserve the
            authored ``comment=`` text verbatim (still drops surrounding
            quotes when not needed by the bare-value rule).
    """
    lines: list[str] = []
    for menu in cfg.menus():
        items = cfg.items_by_menu[menu]
        if not items:
            continue
        lines.append(menu)
        for item in items:
            lines.append(_render_item(item, minify_comments=minify_comments))
    # Trailing newline so concat with other files is well-behaved.
    return "\n".join(lines) + "\n"


def _render_item(item: Item, *, minify_comments: bool) -> str:
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
        value = _process_value(key, raw_value, minify_comments=minify_comments)
        if value is None:
            # Comment with no iac token AND minification on -> drop.
            continue
        parts.append(f"{key}={value}")

    return " ".join(parts)


def _process_value(key: str, raw: str, *, minify_comments: bool) -> str | None:
    """Return the on-disk representation for one property value.

    Returns ``None`` to signal "drop this property entirely" (used for
    comments without an iac token when minification is on).
    """
    unquoted = _strip_quotes(raw)

    if key == "comment" and minify_comments:
        token = _first_iac_token(unquoted)
        if token is None:
            return None
        # Synthesised bare token: never needs quoting.
        return token

    return _requote(unquoted)


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


def _first_iac_token(text: str) -> str | None:
    """Return the first ``iac.*`` token in *text*, or None if none present.

    Strips trailing punctuation (``.``, ``,``, ``;``, ``:``) so a token
    found at the end of a sentence ("...iac.foo.bar.") still yields the
    bare identifier.
    """
    for raw_token in text.replace(",", " ").split():
        if raw_token.startswith(IAC_PREFIX):
            return raw_token.rstrip(".,;:")
    return None
