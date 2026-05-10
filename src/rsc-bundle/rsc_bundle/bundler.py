"""Bundle RouterOS .rsc files by inlining ``/import file-name=...`` directives.

Legacy entry point: rsc-bundle's modern path uses
:func:`rsc_bundle.bundle` (folder-in, text-out via the loader + compact
emitter pipeline). This module survives because some consumers and tests
still drive the old import-inlining workflow directly.

Each import target is resolved **relative to the importing file's
directory**, not by basename across a flat source tree. So
``/import file-name=helpers/log.rsc`` inside ``rsc/apply.rsc`` resolves to
``rsc/helpers/log.rsc``, and ``/import file-name=../shared/x.rsc`` would
walk up.

Public entry points
-------------------
- :func:`bundle_file` -- bundle from disk; imports walk relative to each file
- :func:`bundle_inline` -- bundle from an in-memory ``{name: text}`` map (tests)
- :class:`BundleError` -- raised on missing import target or import cycle
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .resolver import resolve_relative
from .unfold import unfold


# Type parameter for the import-walker -- a Path on disk, or a basename
# string for the in-memory variant. Each entry point keeps its own
# convention; the walker delegates fetching and resolution to the caller.
_K = TypeVar("_K")


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

    The *root* argument is accepted for backwards compatibility but
    ignored -- the entry's parent directory acts as the implicit base, and
    nested imports walk relative to their own file. New callers should
    omit it.

    Returns the bundled text.
    """
    del root  # kept for API compatibility; relative-path mode doesn't need it
    entry_path = Path(entry).resolve()
    if not entry_path.is_file():
        raise BundleError(f"entry not found: {entry_path}")

    def fetch_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BundleError(f"read failed: {path}: {exc}") from exc

    def resolve_import(path: Path, target: str) -> Path:
        try:
            return resolve_relative(path, target)
        except FileNotFoundError as exc:
            raise BundleError(str(exc)) from exc

    return _walk_imports(
        start=entry_path,
        label=lambda p: p.name,
        fetch=fetch_text,
        resolve=resolve_import,
    )


def bundle_inline(entry_basename: str, sources: dict[str, str]) -> str:
    """Bundle from an in-memory ``{basename: text}`` map.

    Used by tests. Imports are looked up by exact key match -- callers
    must supply pre-resolved keys (basename only, no relative paths).
    """
    if entry_basename not in sources:
        raise BundleError(f"entry not in sources: {entry_basename!r}")

    def fetch_text(name: str) -> str:
        # Already validated above for the entry; for transitive imports
        # this raises so the walker can build the cycle/missing chain.
        if name not in sources:
            raise BundleError(f"missing import target: {name}")
        return sources[name]

    def resolve_import(_owner: str, target: str) -> str:
        return target  # in-memory keys are flat; no path resolution needed

    return _walk_imports(
        start=entry_basename,
        label=lambda name: name,
        fetch=fetch_text,
        resolve=resolve_import,
    )


# Generic over `K`: the "key" used to identify a source -- a Path on disk,
# or a basename string for the in-memory variant. The walker delegates
# fetching and import-resolution to the caller, so each entry point can
# keep its own convention.
def _walk_imports(
    *,
    start: _K,
    label: Callable[[_K], str],
    fetch: Callable[[_K], str],
    resolve: Callable[[_K, str], _K],
) -> str:
    """Depth-first inline all ``/import`` directives starting from *start*.

    Cycles raise :class:`BundleError`; duplicate imports of an
    already-visited source emit a one-line skip note instead of recursing.
    Each emitted block is wrapped in ``# >>> begin <name>`` / ``# <<< end
    <name>`` banners so the bundled output is easy to navigate.

    Parameters
    ----------
    start    : the entry point (a :class:`Path` for on-disk bundling or a
               basename string for the in-memory variant).
    label    : ``key -> str`` for the banner / cycle / skip messages.
    fetch    : ``key -> text`` returning the source for a key (raises
               :class:`BundleError` for missing sources).
    resolve  : ``(owner_key, import_target) -> key`` for the next source
               (raises :class:`BundleError` for missing targets).
    """
    visited: set[_K] = set()
    on_stack: list[_K] = []
    out: list[str] = []

    def visit(key: _K) -> None:
        if key in on_stack:
            cycle = " -> ".join(label(k) for k in on_stack + [key])
            raise BundleError(f"import cycle: {cycle}")
        if key in visited:
            out.append(f"# rsc-bundle: skipped duplicate import of {label(key)}\n")
            return

        on_stack.append(key)
        visited.add(key)

        out.append(f"# >>> begin {label(key)}\n")
        unfolded = unfold(fetch(key))
        for raw_line in unfolded.splitlines(keepends=True):
            match = IMPORT_RE.match(raw_line.rstrip("\r\n"))
            if not match:
                out.append(raw_line)
                continue
            target = match.group("qname") or match.group("bname")
            # Skip imports we still can't resolve statically (variable
            # reference). Leave the line verbatim so RouterOS handles it
            # at runtime if anyone uploads the bundle alongside originals.
            if target.startswith("$"):
                out.append(raw_line)
                continue
            visit(resolve(key, target))

        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append(f"# <<< end {label(key)}\n")

        on_stack.pop()

    visit(start)
    return "".join(out)
