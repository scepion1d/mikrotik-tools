"""Post-bundle flattening: resolve ``:global`` vars and strip scripting wrappers.

After :func:`rsc.bundle.bundle_file` produces a self-contained bundle that
still contains RouterOS scripting glue (``:global``, ``:if``, ``:foreach``,
helper invocations like ``$iacLogInfo``), :func:`flatten` reduces it to the
subset that ``rsc.diff`` understands -- pure ``/path`` + ``add`` / ``set`` /
``remove`` statements with all variable references resolved to their literal
values.

Why
---
``rsc.diff`` parses a bundle as if it were a router export. Anything that
isn't ``/path`` or ``verb props=...`` becomes either noise (and is largely
ignored by the parser) or worse, a literal ``$adminCidrs`` token in the
``address=`` property -- which then mismatches a live router that has
``address=192.168.10.2,192.168.10.3``. Flattening produces a bundle that
diffs cleanly against ``/export`` output.

Strategy
--------
1. Collect every ``:global NAME "literal"`` (assignment form) into a map.
   Bodies of helper functions (``:global NAME do={...}``) and bare
   declarations (``:global NAME``) are ignored.
2. Substitute ``$NAME`` (word-bounded) globally with the bare value.
3. Drop scripting wrappers line-by-line, preserving block contents:
   - ``:KEYWORD ...`` lines (``:global``, ``:local``, ``:if``, ``:foreach``,
     ``:set``, ``:put``, ``:log``, ...) are removed. If the line opens a
     ``do={`` block, only the wrapper line and the matching closing ``}``
     are removed -- the *body* of the block is kept and processed at the
     outer level (so config statements nested inside ``:if/else`` survive).
   - ``$varName ...`` invocation lines (e.g. ``$iacLogInfo "x"``) are
     dropped the same way.
   - ``} else={`` continuations between branches of a stripped wrapper are
     dropped.
   - Pure-brace closing ``}`` lines that close a stripped wrapper are
     dropped.
4. Normalize property quoting to match RouterOS ``/export`` style:
   ``key="bareword"`` collapses to ``key=bareword`` when the value has no
   whitespace and no shell-special characters. Strings that need quoting
   (spaces, ``[]``, ``;``, etc.) are left as-is. Comment lines are skipped.

Limitations
-----------
- Only string-literal ``:global`` assignments are recognised. Computed
  expressions are left alone (and the ``$NAME`` ref will survive).
- String-aware brace counting handles ``"..."`` quoting but not arbitrary
  RouterOS escape sequences. Adequate for our config corpus.
- The pass is line-oriented after folding ``\\``-continuations, so the
  output loses the original line wrapping. ``rsc.diff`` doesn't care, and
  the result is still valid for ``/import``.
"""

from __future__ import annotations

import re


# `:global NAME "value"` -- single-line assignment to a string literal.
# Captures NAME and the unquoted value.
_GLOBAL_ASSIGN_RE = re.compile(
    r'^[ \t]*:global[ \t]+(?P<name>\w+)[ \t]+"(?P<value>[^"\\]*(?:\\.[^"\\]*)*)"[ \t]*$',
)

# A logical line that opens a script wrapper. We treat any leading-`:`
# directive as a wrapper candidate; the brace-counting decides whether it
# actually opens a body.
_COLON_LEAD_RE = re.compile(r"^[ \t]*:")

# A logical line that begins with a `$NAME` -- typically a helper invocation
# like `$iacLogInfo "x"`. Bare `$NAME` token followed by space, parenthesis,
# or end-of-line.
_DOLLAR_LEAD_RE = re.compile(r"^[ \t]*\$\w+(?:[ \t(]|$)")

# Pure-brace continuation/close lines that we drop while inside a stripped
# wrapper: standalone `}`, `} else={`, `} else= {`. Anything else with a
# brace stays put.
_PURE_CLOSE_RE = re.compile(r"^[ \t]*\}[ \t]*$")
_ELSE_CONT_RE = re.compile(r"^[ \t]*\}[ \t]*else[ \t]*=[ \t]*\{[ \t]*$")

# `do={` somewhere on the line marks the open brace as a code-block body.
# Absence on a `:KEYWORD ... {` line means the brace opens a data literal
# (array / dictionary), whose contents are pure script data and must be
# discarded along with the wrapper.
_DO_BLOCK_RE = re.compile(r"do[ \t]*=[ \t]*\{")


def flatten(text: str, *, substitute_vars: bool = True) -> str:
    """Resolve ``:global`` vars and strip scripting noise from *text*.

    Returns a string that contains only RouterOS config statements,
    comments, and blank lines.

    Args:
        text: bundled .rsc text to clean up.
        substitute_vars: when True (default), every ``$NAME`` is replaced
            by the literal value of the corresponding ``:global NAME
            "value"`` assignment. When False, ``$NAME`` references survive
            into the output -- the bundle then expects the operator (or a
            companion secrets file) to define those globals before
            ``/import``-ing it. Scripting-strip and quote normalisation
            run regardless.
    """
    if substitute_vars:
        vars_map = _collect_globals(text)
        text = _substitute_globals(text, vars_map)
    text = _strip_scripting(text)
    text = _normalize_quoting(text)
    return text


# --------------------------------------------------------------------------
# variable collection / substitution
# --------------------------------------------------------------------------


def _collect_globals(text: str) -> dict[str, str]:
    """Scan *text* for ``:global NAME "value"`` and return ``{name: value}``.

    Bodies of helper functions (``:global NAME do={...}``), bare
    declarations (``:global NAME``), and any non-string-literal RHS are
    ignored.
    """
    out: dict[str, str] = {}
    for line in _fold_continuations(text.splitlines()):
        match = _GLOBAL_ASSIGN_RE.match(line)
        if match:
            out[match.group("name")] = match.group("value")
    return out


def _substitute_globals(text: str, vars_map: dict[str, str]) -> str:
    """Replace every ``$NAME`` (word-bounded) in *text* with its value from *vars_map*.

    Comment lines (``#``-prefixed) are skipped so secrets don't leak into
    doc-strings that reference them by var name. Names are matched
    longest-first so ``$adminPass`` is not partially matched by
    ``$adminP``.
    """
    if not vars_map:
        return text
    # Longest names first to avoid `$adminP` matching when `$adminPass`
    # exists. \b at the right edge ensures word boundaries.
    names_sorted = sorted(vars_map, key=len, reverse=True)
    pattern = re.compile(
        r"\$(" + "|".join(re.escape(n) for n in names_sorted) + r")\b"
    )

    def replace(m: re.Match[str]) -> str:
        return vars_map[m.group(1)]

    # Substitute per-line so we can skip comment lines -- secrets shouldn't
    # leak into doc-strings that reference them by var name (e.g.
    # `# Depends on: $adminPass (secrets.rsc)`).
    out_lines = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("#"):
            out_lines.append(line)
        else:
            out_lines.append(pattern.sub(replace, line))
    return "".join(out_lines)


# --------------------------------------------------------------------------
# scripting strip
# --------------------------------------------------------------------------


def _strip_scripting(text: str) -> str:
    """Drop ``:KEYWORD`` / ``$helper`` wrapper lines, preserving block bodies.

    See the module docstring ("Strategy" section, step 3) for the rules.
    The pass is line-oriented after folding ``\\``-continuations -- output
    loses the original line wrapping but stays semantically equivalent
    for ``rsc.diff`` and ``/import``.
    """
    out: list[str] = []
    skip_close_depth = 0  # how many trailing `}` belong to stripped wrappers
    data_skip = 0  # > 0 while inside a `:local NAME { ... }` array literal
    for line in _fold_continuations(text.splitlines()):
        # Data-literal eat-everything mode (entered for `:local NAME {`-style
        # multi-line array bindings).
        if data_skip > 0:
            data_skip += _count_unquoted(line, "{") - _count_unquoted(line, "}")
            if data_skip < 0:
                data_skip = 0
            continue

        stripped = line.lstrip()

        # Inside a stripped code wrapper, drop `}` and `} else={` continuations
        # that close it. Real config never has lines that are JUST a `}`.
        if skip_close_depth > 0 and _PURE_CLOSE_RE.match(line):
            skip_close_depth -= 1
            continue
        if skip_close_depth > 0 and _ELSE_CONT_RE.match(line):
            # neutral depth: one wrapper close + one new wrapper open
            continue

        # Wrapper open / one-liner / bare declaration starting with `:`.
        if _COLON_LEAD_RE.match(line):
            opens = _count_unquoted(line, "{")
            closes = _count_unquoted(line, "}")
            net = opens - closes
            if net > 0:
                if _DO_BLOCK_RE.search(line):
                    # `do={` body: drop wrapper, keep contents (config may
                    # be nested inside :if/:foreach branches).
                    skip_close_depth += net
                else:
                    # data literal: drop wrapper AND body to matching `}`.
                    data_skip = net
            # one-liners (net == 0) drop with no depth change
            continue

        # Helper-style `$varName ...` invocation. Only treat as a wrapper if
        # the line genuinely starts with $NAME -- avoids stripping property
        # values that mention `$` further along.
        if _DOLLAR_LEAD_RE.match(stripped):
            opens = _count_unquoted(line, "{")
            closes = _count_unquoted(line, "}")
            if opens > closes:
                # Same do= heuristic in case a helper opens a code block.
                if _DO_BLOCK_RE.search(line):
                    skip_close_depth += opens - closes
                else:
                    data_skip = opens - closes
            continue

        out.append(line)

    # Re-join with newlines; trailing newline for parity with input style.
    text = "\n".join(out)
    if not text.endswith("\n"):
        text += "\n"
    return text


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


# `key="value"` where value has no embedded quote/backslash. We only need to
# spot the simple form; complex values (escapes, quoted-quotes) stay quoted.
_KV_QUOTED_RE = re.compile(r'(?P<key>[A-Za-z_][\w-]*)="(?P<val>[^"\\]*)"')

# Characters that force RouterOS to keep a value quoted in `/export` output.
# Whitespace + the punctuation that matters in script syntax.
_NEEDS_QUOTE_RE = re.compile(r'[\s\[\]{}();\\"`#$<>|&?*]')


def _normalize_quoting(text: str) -> str:
    """Match RouterOS ``/export`` quoting: drop quotes around bareword values.

    Live router exports emit ``comment=WAN`` (bare) for whitespace-free
    values and ``comment="LAN bridge"`` (quoted) only when needed. Source
    .rsc files tend to be uniformly quoted (``comment="WAN"``), which
    diffs as drift against an export. This pass normalizes the bundle so
    it matches export style.

    Skips comment lines (``# ...``) and section-header lines starting with
    ``/``. Both forms can contain literal ``"`` characters that aren't KV
    pairs.
    """
    out_lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("#") or stripped.startswith("/"):
            out_lines.append(line)
            continue

        def _maybe_unquote(m: re.Match[str]) -> str:
            val = m.group("val")
            # Empty -> keep "" (e.g. on-event="" needs the empty quotes).
            # Anything that would force quoting in /export -> keep quoted.
            if not val or _NEEDS_QUOTE_RE.search(val):
                return m.group(0)
            return f'{m.group("key")}={val}'

        out_lines.append(_KV_QUOTED_RE.sub(_maybe_unquote, line))
    return "".join(out_lines)


def _fold_continuations(lines: list[str]) -> list[str]:
    """Merge physical lines that end with ``\\`` into a single logical line.

    Trailing ``\\`` is consumed; the next physical line is appended after
    a single space (mirrors how RouterOS treats line continuations).
    """
    out: list[str] = []
    pending: str | None = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if pending is not None:
            line = pending + " " + line.lstrip()
            pending = None
        if line.rstrip().endswith("\\"):
            # strip the trailing backslash + any whitespace before it
            pending = re.sub(r"[ \t]*\\$", "", line.rstrip())
            continue
        out.append(line)
    if pending is not None:
        out.append(pending)
    return out


def _count_unquoted(text: str, char: str) -> int:
    """Count occurrences of *char* in *text* outside ``"..."`` strings."""
    assert len(char) == 1
    count = 0
    in_quote = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == '"':
            in_quote = not in_quote
        elif not in_quote and c == char:
            count += 1
        i += 1
    return count
