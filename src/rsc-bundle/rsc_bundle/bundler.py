"""Bundle RouterOS .rsc files by inlining ``/import file-name=...`` directives.

Each import target is resolved **relative to the importing file's
directory**, not by basename across a flat source tree. So
``/import file-name=helpers/log.rsc`` inside ``rsc/apply.rsc`` resolves to
``rsc/helpers/log.rsc``, and ``/import file-name=../shared/x.rsc`` would
walk up.

Public entry points: :func:`bundle` (text-in) and :func:`bundle_file` (path-in).
"""

from __future__ import annotations

import re
from pathlib import Path

from .resolver import resolve_relative
from .unfold import unfold


# Matches a line that is exactly an `/import file-name=NAME` directive
# (optionally indented; trailing whitespace allowed). NAME may be bare or
# `"quoted"` -- both are valid RouterOS forms; the unfolder produces
# quoted literals after substitution.
IMPORT_RE = re.compile(
    r"""
    ^                       # start of line
    [ \t]*                  # optional leading indent
    /import [ \t]+          # the directive
    file-name=              # required parameter (exact spelling)
    (?:
        "(?P<qname>[^"\\]*(?:\\.[^"\\]*)*)"   # quoted form
        |
        (?P<bname>\S+)                         # bare form (no whitespace)
    )
    [ \t]*                  # trailing whitespace
    $                       # end of line
    """,
    re.VERBOSE,
)


class BundleError(Exception):
    """Raised on missing import target or import cycle."""


def bundle_file(entry: str | Path, root: str | Path | None = None) -> str:
    """Bundle *entry*. Imports are resolved relative to each file's location.

    The *root* argument is accepted for backwards compatibility but ignored;
    the entry's parent directory acts as the implicit base, and nested
    imports walk relative to their own file.

    Returns the bundled text.
    """
    del root  # kept for API compatibility; relative-path mode doesn't need it
    entry_path = Path(entry).resolve()
    if not entry_path.is_file():
        raise BundleError(f"entry not found: {entry_path}")

    visited: set[Path] = set()
    on_stack: list[Path] = []
    out: list[str] = []

    def visit(path: Path) -> None:
        path = path.resolve()
        if path in on_stack:
            cycle = " -> ".join(p.name for p in on_stack + [path])
            raise BundleError(f"import cycle: {cycle}")
        if path in visited:
            out.append(f"# rsc-bundle: skipped duplicate import of {path.name}\n")
            return

        on_stack.append(path)
        visited.add(path)

        out.append(f"# >>> begin {path.name}\n")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BundleError(f"read failed: {path}: {exc}") from exc

        unfolded = unfold(text)
        for raw_line in unfolded.splitlines(keepends=True):
            match = IMPORT_RE.match(raw_line.rstrip("\r\n"))
            if match:
                target = match.group("qname") or match.group("bname")
                # Skip imports we still can't resolve statically (variable
                # reference). Leave the line verbatim so RouterOS handles it
                # at runtime if anyone uploads the bundle alongside originals.
                if target.startswith("$"):
                    out.append(raw_line)
                    continue
                try:
                    resolved = resolve_relative(path, target)
                except FileNotFoundError as exc:
                    raise BundleError(str(exc)) from exc
                visit(resolved)
            else:
                out.append(raw_line)

        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append(f"# <<< end {path.name}\n")

        on_stack.pop()

    visit(entry_path)
    return "".join(out)


def bundle_inline(entry_basename: str, sources: dict[str, str]) -> str:
    """Bundle from an in-memory ``{basename: text}`` map.

    Used by tests. Imports are looked up by exact key match -- callers must
    supply pre-resolved keys (basename only).
    """
    if entry_basename not in sources:
        raise BundleError(f"entry not in sources: {entry_basename!r}")

    visited: set[str] = set()
    on_stack: list[str] = []
    out: list[str] = []

    def visit(name: str) -> None:
        if name in on_stack:
            cycle = " -> ".join(on_stack + [name])
            raise BundleError(f"import cycle: {cycle}")
        if name in visited:
            out.append(f"# rsc-bundle: skipped duplicate import of {name}\n")
            return
        if name not in sources:
            chain = " -> ".join(on_stack + [name])
            raise BundleError(f"missing import target: {chain}")

        on_stack.append(name)
        visited.add(name)

        out.append(f"# >>> begin {name}\n")
        unfolded = unfold(sources[name])
        for raw_line in unfolded.splitlines(keepends=True):
            match = IMPORT_RE.match(raw_line.rstrip("\r\n"))
            if match:
                target = match.group("qname") or match.group("bname")
                if target.startswith("$"):
                    out.append(raw_line)
                    continue
                visit(target)
            else:
                out.append(raw_line)
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append(f"# <<< end {name}\n")

        on_stack.pop()

    visit(entry_basename)
    return "".join(out)
