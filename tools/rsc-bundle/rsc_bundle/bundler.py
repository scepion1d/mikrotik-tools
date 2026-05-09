"""Bundle RouterOS .rsc files by inlining ``/import file-name=...`` directives.

Public entry points: :func:`bundle` (text-in) and :func:`bundle_file` (path-in).
"""

from __future__ import annotations

import re
from pathlib import Path

from .resolver import build_source_map
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
    """Bundle *entry* by walking *root* (defaults to entry's parent dir).

    Resolves imports by basename across the whole tree under *root*.
    Returns the bundled text.
    """
    entry_path = Path(entry).resolve()
    root_path = Path(root).resolve() if root is not None else entry_path.parent
    source_map = build_source_map(root_path)
    sources = {name: path.read_text(encoding="utf-8") for name, path in source_map.items()}
    # Make sure the entry file itself is in the map (it should be if it's
    # under root, but allow entries outside root by adding it explicitly).
    if entry_path.name not in sources:
        sources[entry_path.name] = entry_path.read_text(encoding="utf-8")
    return bundle(entry_path.name, sources)


def bundle(entry_basename: str, sources: dict[str, str]) -> str:
    """Bundle starting from *entry_basename*, resolving imports against *sources*.

    Each file's text is first passed through :func:`unfold` to expand
    ``:foreach`` loops over known array bindings into literal-import lines,
    so dynamic ``/import file-name=$f`` patterns become resolvable.

    *sources* maps basename -> file contents. Raises :class:`BundleError`
    on a missing import target or an import cycle.
    """
    if entry_basename not in sources:
        raise BundleError(f"entry not in sources: {entry_basename!r}")

    visited: set[str] = set()
    on_stack: list[str] = []
    out: list[str] = []

    def visit(basename: str) -> None:
        if basename in on_stack:
            cycle = " -> ".join(on_stack + [basename])
            raise BundleError(f"import cycle: {cycle}")
        if basename in visited:
            # Already inlined once; RouterOS would re-execute on duplicate
            # /import, but for IaC we treat sources as load-once.
            out.append(f"# rsc-bundle: skipped duplicate import of {basename}\n")
            return
        if basename not in sources:
            chain = " -> ".join(on_stack + [basename])
            raise BundleError(f"missing import target: {chain}")

        on_stack.append(basename)
        visited.add(basename)

        out.append(f"# >>> begin {basename}\n")
        # Unfold first so dynamic imports become literal.
        unfolded = unfold(sources[basename])
        for raw_line in unfolded.splitlines(keepends=True):
            match = IMPORT_RE.match(raw_line.rstrip("\r\n"))
            if match:
                target = match.group("qname") or match.group("bname")
                # Skip imports we still can't resolve statically (variable
                # reference). Leave the line verbatim so RouterOS handles it
                # at runtime if anyone actually uploads the bundled file
                # alongside the originals.
                if target.startswith("$"):
                    out.append(raw_line)
                    continue
                visit(target)
            else:
                out.append(raw_line)
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append(f"# <<< end {basename}\n")

        on_stack.pop()

    visit(entry_basename)
    return "".join(out)
