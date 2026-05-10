r"""Line-based parser for RouterOS .rsc files.

Handles:
  - Menu paths (lines starting with ``/``)
  - ``add ...`` and ``set ...`` items, with optional leading indent
  - Line continuations via trailing ``\``
  - Quoted ``"..."`` values (including embedded escapes)
  - Comments (lines starting with ``#``) and blank lines
  - Bracket expressions like ``[find default-name=ether1]`` kept as one token

Does NOT interpret:
  - Variable expansions (``$adminPass`` is kept as the literal string ``$adminPass``)
  - Control flow (``:if``, ``:foreach``, ``:global``, ``:log``, etc.) -- skipped
  - ``:import`` / ``/import`` -- skipped
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from .model import Config, Item


# Lines we recognise as "not config items" and skip silently.
SCRIPT_DIRECTIVE_RE = re.compile(r"^\s*(:|/import\b|/file\b)")


# Matches `[find KEY=VALUE]` -- the simple equality form of a `set`
# selector. Used by _consume_item to surface KEY=VALUE into props so the
# identity-key resolution can find it.
_FIND_KV_RE = re.compile(r'^\[find\s+(?P<key>[\w-]+)=(?P<val>[^\]]+)\]$')


def parse_file(path: str | Path) -> Config:
    """Parse the file at *path* into a :class:`Config`."""
    return parse_text(Path(path).read_text(encoding="utf-8"))


def parse_text(text: str) -> Config:
    """Parse RouterOS script *text* into a :class:`Config`."""
    cfg = Config()
    current_menu: str | None = None

    for raw_line in _logical_lines(text):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if SCRIPT_DIRECTIVE_RE.match(line):
            continue

        if line.startswith("/"):
            # Menu path. Two formats are supported:
            #   authored:  /interface/bridge          (slash-separated)
            #   export:    /interface bridge          (space-separated submenus)
            # In both forms, the path may be followed on the same line by an
            # `add`/`set`/`remove` directive.
            current_menu, rest = _split_menu_and_rest(line)
            if rest:
                _consume_item(cfg, current_menu, rest)
            continue

        if current_menu is None:
            # Stray line outside any menu -- ignore.
            continue

        _consume_item(cfg, current_menu, line)

    return cfg


# Verbs that may appear on the same line as a menu path (RouterOS shorthand).
_INLINE_VERBS = {"add", "set", "remove", "reset", "find", "print", "edit"}


def _split_menu_and_rest(line: str) -> tuple[str, str]:
    """Split a ``/menu/path [rest]`` line into ``(normalised_menu, rest)``.

    Handles both formats:
      ``/interface/bridge``
      ``/interface bridge add name=foo``
    Returns the menu path with ``/`` separators and any trailing
    ``add``/``set``/``remove``/... directive (or empty string).
    """
    tokens = line.split()
    # Find where the menu path ends and a verb (or arg) begins.
    path_parts: list[str] = []
    rest_index = len(tokens)
    for i, tok in enumerate(tokens):
        if i == 0:
            # First token always part of the path; strip leading `/`.
            path_parts.append(tok.lstrip("/"))
            continue
        if tok in _INLINE_VERBS:
            rest_index = i
            break
        # Tokens containing `=` or `[` are command args, not menu segments.
        if "=" in tok or tok.startswith("[") or tok.startswith('"'):
            rest_index = i
            break
        path_parts.append(tok)

    menu = "/" + "/".join(p for p in path_parts if p)
    menu = _normalise_menu(menu)
    rest = " ".join(tokens[rest_index:])
    return menu, rest


def _normalise_menu(menu: str) -> str:
    """Strip trailing slash, ensure leading slash, normalise separators."""
    menu = menu.strip()
    # Authored form may legally have an inner `/`; nothing to do.
    if menu.endswith("/") and len(menu) > 1:
        menu = menu[:-1]
    return menu


def _consume_item(cfg: Config, menu: str, line: str) -> None:
    """Parse a single ``add ...`` / ``set ...`` line and append to *cfg*."""
    verb, _, rest = line.partition(" ")
    if verb not in ("add", "set"):
        # Unknown directive (could be `print`, `remove`, ...). Ignore for now.
        return

    rest = rest.strip()
    props: dict[str, str] = {}

    # `set` items often start with a [find ...] selector or a positional name.
    if verb == "set" and rest.startswith("["):
        selector, rest = _take_bracket(rest)
        props["__selector__"] = selector
        # If the selector is `[find KEY=VAL]`, surface KEY=VAL into props
        # so identity_key() resolves to a stable key (same trick as the
        # positional branch below). Without this, `set [find name=admin]`
        # against an /export that omits the admin row would fall through
        # to `@anon=N` -- meaningless on the live router.
        find_kv = _FIND_KV_RE.match(selector)
        if find_kv:
            key = find_kv.group("key")
            val = find_kv.group("val").strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            props.setdefault(key, val)
        rest = rest.lstrip()
    elif verb == "set" and rest and not _looks_like_kv(rest.split(" ", 1)[0]):
        # `set telnet disabled=yes` -- first token is a positional id.
        first, _, rest = rest.partition(" ")
        props["__selector__"] = first
        # The positional id is also the row identity for menus like
        # /ip/service (rows keyed by name=ssh, name=telnet, ...). Surface
        # it as `name=` so identity_key() can pick it up via
        # MENUS_WITH_NAME. `setdefault` so an explicit `name=` later in
        # the same line wins.
        props.setdefault("name", first)
        rest = rest.lstrip()

    for key, value in _tokenise_kv(rest):
        props[key] = value

    cfg.add(Item(menu=menu, verb=verb, props=props))


def _looks_like_kv(token: str) -> bool:
    """Crude check: does this token look like ``key=value``?"""
    return "=" in token and not token.startswith("[")


# --- low-level lexing helpers ----------------------------------------------


def _logical_lines(text: str) -> Iterator[str]:
    r"""Yield lines with ``\`` continuations folded into one.

    RouterOS treats ``\<newline><leading-whitespace>`` as a glue operator
    that joins without inserting any separator -- the export form often
    uses it to break inside a value, e.g. ``name=\\n    iac.bridge.lan``
    must rejoin to ``name=iac.bridge.lan``. We strip the trailing ``\``
    and the next line's leading whitespace, then concatenate with no
    separator at all.

    Authored configs put space + ``\`` at the end of a logical token, so
    losing the inter-token space here would be wrong. We preserve any
    whitespace that immediately preceded the ``\`` itself; that takes
    care of inter-token spacing.
    """
    buf: list[str] = []
    for line in text.splitlines():
        if line.endswith("\\"):
            # Drop the backslash; preserve trailing whitespace that came
            # before it (token separator in authored configs). Strip any
            # leading whitespace if this is a continuation line itself.
            content = line[:-1]
            if buf:
                content = content.lstrip()
            buf.append(content)
            continue
        if buf:
            buf.append(line.lstrip())
            yield "".join(buf)
            buf = []
        else:
            yield line.rstrip()
    if buf:
        yield "".join(buf)


def _take_bracket(s: str) -> tuple[str, str]:
    """Consume a ``[ ... ]`` (handles nested brackets and quotes).

    Returns ``(bracket_with_brackets, remainder)``.
    """
    assert s.startswith("[")
    depth = 0
    in_quote = False
    for i, ch in enumerate(s):
        if ch == '"' and (i == 0 or s[i - 1] != "\\"):
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return s[: i + 1], s[i + 1 :]
    raise ValueError(f"unterminated bracket in: {s!r}")


def _tokenise_kv(s: str) -> Iterator[tuple[str, str]]:
    """Yield ``(key, value)`` pairs from a string of ``key=value key2="..." key3=[...]``."""
    i = 0
    n = len(s)
    while i < n:
        # Skip whitespace
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break

        # Read key up to '='
        j = i
        while j < n and s[j] != "=" and not s[j].isspace():
            j += 1
        if j >= n or s[j] != "=":
            # Bare flag without value (rare in our scripts) -- skip
            i = j
            continue
        key = s[i:j]
        i = j + 1  # past '='

        # Read value: quoted string, bracket expr, or until next whitespace
        if i < n and s[i] == '"':
            value, i = _take_quoted(s, i)
        elif i < n and s[i] == "[":
            bracket, _ = _take_bracket(s[i:])
            value = bracket
            i += len(bracket)
        else:
            j = i
            while j < n and not s[j].isspace():
                j += 1
            value = s[i:j]
            i = j

        yield key, value


def _take_quoted(s: str, start: int) -> tuple[str, int]:
    """Consume a ``"..."`` string starting at index *start*.

    Returns ``(raw, end_index)``. The returned raw value INCLUDES the
    surrounding quotes -- we keep them so the emitter can write the value
    back verbatim without guessing whether it needs quoting.
    """
    assert s[start] == '"'
    i = start + 1
    n = len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if s[i] == '"':
            return s[start : i + 1], i + 1
        i += 1
    raise ValueError(f"unterminated string starting at {start}: {s[start:]!r}")
