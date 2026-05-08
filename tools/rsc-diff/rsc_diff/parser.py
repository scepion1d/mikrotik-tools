r"""Line-based parser for RouterOS .rsc files.

Handles:
  - Menu paths (lines starting with `/`)
  - `add ...` and `set ...` items, with optional leading indent
  - Line continuations via trailing `\`
  - Quoted "..." values (including embedded escapes)
  - Comments (lines starting with `#`) and blank lines
  - Bracket expressions like `[find default-name=ether1]` kept as one token

Does NOT interpret:
  - Variable expansions (`$adminPass` is kept as the literal string `$adminPass`)
  - Control flow (`:if`, `:foreach`, `:global`, `:log`, etc.) -- skipped
  - `:import` / `/import` -- skipped
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import Config, Item


# Lines we recognise as "not config items" and skip silently.
SCRIPT_DIRECTIVE_RE = re.compile(r"^\s*(:|/import\b|/file\b)")


def parse_file(path: str | Path) -> Config:
    return parse_text(Path(path).read_text(encoding="utf-8"))


def parse_text(text: str) -> Config:
    cfg = Config()
    current_menu: str | None = None

    for raw_line in _logical_lines(text):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if SCRIPT_DIRECTIVE_RE.match(line):
            continue

        if line.startswith("/"):
            # Menu path. RouterOS exports may put `set/add` on the same line as
            # the path (e.g. `/system/identity set name=foo`); split if so.
            head, _, rest = line.partition(" ")
            current_menu = _normalise_menu(head)
            rest = rest.strip()
            if rest:
                _consume_item(cfg, current_menu, rest)
            continue

        if current_menu is None:
            # Stray line outside any menu -- ignore.
            continue

        _consume_item(cfg, current_menu, line)

    return cfg


def _normalise_menu(menu: str) -> str:
    """Strip trailing slash, ensure leading slash, no whitespace."""
    menu = menu.strip()
    if menu.endswith("/") and len(menu) > 1:
        menu = menu[:-1]
    return menu


def _consume_item(cfg: Config, menu: str, line: str) -> None:
    """Parse a single `add ...` / `set ...` line and append to cfg."""
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
        rest = rest.lstrip()
    elif verb == "set" and rest and not _looks_like_kv(rest.split(" ", 1)[0]):
        # `set telnet disabled=yes` -- first token is a positional id.
        first, _, rest = rest.partition(" ")
        props["__selector__"] = first
        rest = rest.lstrip()

    for key, value in _tokenise_kv(rest):
        props[key] = value

    cfg.add(Item(menu=menu, verb=verb, props=props))


def _looks_like_kv(token: str) -> bool:
    """Crude check: does this token look like `key=value`?"""
    return "=" in token and not token.startswith("[")


# --- low-level lexing helpers ----------------------------------------------


def _logical_lines(text: str):
    r"""Yield lines with `\` continuations folded into one."""
    buf: list[str] = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buf.append(stripped[:-1].rstrip())
            continue
        if buf:
            buf.append(stripped.lstrip())
            yield " ".join(buf)
            buf = []
        else:
            yield stripped
    if buf:
        yield " ".join(buf)


def _take_bracket(s: str) -> tuple[str, str]:
    """Consume a `[ ... ]` (handles nested brackets and quotes).

    Returns (bracket_with_brackets, remainder).
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


def _tokenise_kv(s: str):
    """Yield (key, value) pairs from a string of `key=value key2="..." key3=[...]`."""
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
            bracket, rest_after = _take_bracket(s[i:])
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
    """Consume a "..." string starting at index `start`. Returns (raw, end_index).

    The returned raw value includes the surrounding quotes -- we keep them so
    the emitter can write the value back verbatim without guessing whether it
    needs quoting.
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
