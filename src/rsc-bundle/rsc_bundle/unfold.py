"""Unfold ``:foreach`` loops over known ``:local`` / ``:global`` array bindings.

Recognises the common pattern::

    :local iacFiles { "a.rsc"; "b.rsc"; "c.rsc" }
    ...
    :foreach f in=$iacFiles do={
        /import file-name=$f
    }

and rewrites the loop body once per array item with ``$f`` substituted by a
quoted string literal. After unfolding, what was a dynamic ``/import
file-name=$f`` becomes a literal ``/import file-name="a.rsc"`` that the
bundler can statically resolve.

Limitations:
  - Only string-literal arrays are recognised. Mixed/computed arrays bail.
  - Only ``:foreach`` referencing a known array by ``$NAME`` is unfolded.
  - Nested foreaches over the same array don't currently re-introduce
    bindings; uncommon enough to defer.
"""

from __future__ import annotations

import re


# `:local NAME {` or `:global NAME {` starting an array literal. We capture
# NAME and the position just past the opening brace.
BINDING_START_RE = re.compile(
    r"^[ \t]*:(?:local|global)[ \t]+(?P<name>\w+)[ \t]*\{",
    re.MULTILINE,
)

# `:foreach VAR in=$ARR do={` opening a loop body. We capture the var, the
# array name, and the position just past the opening brace of `do={`.
FOREACH_START_RE = re.compile(
    r"^(?P<indent>[ \t]*):foreach[ \t]+(?P<var>\w+)[ \t]+in=\$(?P<arr>\w+)[ \t]+do=\{",
    re.MULTILINE,
)


def unfold(text: str) -> str:
    """Return *text* with ``:foreach`` loops over known arrays unrolled.

    Iterates to a fixed point so unfolding can chain (rare, but possible if
    one loop produces another).
    """
    bindings = _collect_bindings(text)
    if not bindings:
        return text

    while True:
        new_text = _unfold_once(text, bindings)
        if new_text == text:
            return text
        text = new_text


def _collect_bindings(text: str) -> dict[str, list[str]]:
    """Find every ``:local NAME { "a"; "b"; ... }`` and return ``{name: [items]}``.

    Skips bindings whose body isn't purely a list of quoted string literals.
    """
    out: dict[str, list[str]] = {}
    for match in BINDING_START_RE.finditer(text):
        brace_pos = match.end() - 1  # position of the opening `{`
        try:
            close = _find_matching_brace(text, brace_pos)
        except ValueError:
            continue
        body = text[brace_pos + 1 : close - 1]
        items = _parse_string_list(body)
        if items is not None:
            out[match.group("name")] = items
    return out


def _unfold_once(text: str, bindings: dict[str, list[str]]) -> str:
    """One pass: replace every ``:foreach`` over a known array with its expansion."""
    out: list[str] = []
    pos = 0
    n = len(text)
    while pos < n:
        match = FOREACH_START_RE.search(text, pos)
        if not match:
            out.append(text[pos:])
            break

        arr_name = match.group("arr")
        if arr_name not in bindings:
            # Unknown array -- copy through up to and including this foreach
            # opener, then continue scanning past it.
            out.append(text[pos : match.end()])
            pos = match.end()
            continue

        # Emit the text leading up to the foreach opener.
        out.append(text[pos : match.start()])

        brace_pos = match.end() - 1
        try:
            close = _find_matching_brace(text, brace_pos)
        except ValueError:
            # Malformed; emit verbatim and bail out of this loop.
            out.append(text[match.start() :])
            return "".join(out)

        body = text[brace_pos + 1 : close - 1]
        var_name = match.group("var")

        # Unroll: emit body once per item, with $VAR replaced by "literal".
        for item in bindings[arr_name]:
            substituted = _substitute_var(body, var_name, item)
            out.append(substituted)
            if not substituted.endswith("\n"):
                out.append("\n")

        pos = close
    return "".join(out)


def _substitute_var(text: str, var_name: str, value: str) -> str:
    """Replace ``$VAR`` (word-bounded) with ``"VALUE"``.

    Quoted form keeps every original use site valid -- both
    ``/import file-name=$f`` and ``("...".$f)`` accept a quoted string.
    """
    pattern = re.compile(rf"\${re.escape(var_name)}\b")
    return pattern.sub(f'"{value}"', text)


def _parse_string_list(body: str) -> list[str] | None:
    """Parse ``"a"; "b"; "c"`` into ``['a', 'b', 'c']``.

    Returns None if any token isn't a bare string literal -- we'd rather
    leave the foreach un-unfolded than emit a broken substitution.
    """
    items: list[str] = []
    for token in body.split(";"):
        token = token.strip()
        if not token:
            continue
        if len(token) < 2 or token[0] != '"' or token[-1] != '"':
            return None
        # Reject internal quoting/escapes for now (none of our arrays have them).
        if '"' in token[1:-1]:
            return None
        items.append(token[1:-1])
    return items


def _find_matching_brace(text: str, start: int) -> int:
    """Given ``text[start] == '{'``, return index just after matching ``}``.

    Tracks string literals so braces inside ``"..."`` don't count.
    """
    if text[start] != "{":
        raise ValueError(f"expected '{{' at {start}, got {text[start]!r}")
    depth = 0
    in_quote = False
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == '"':
            in_quote = not in_quote
        elif not in_quote:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    raise ValueError(f"unmatched '{{' at {start}")
