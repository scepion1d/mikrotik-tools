"""Resolve import targets relative to the importing file's directory."""

from __future__ import annotations

from pathlib import Path


def resolve_relative(importer: Path, target: str) -> Path:
    """Resolve *target* relative to *importer*'s parent directory.

    Mirrors how RouterOS would address files on flash from a script's
    own location. Normalises path separators (``/`` and ``\\`` both work).

    Raises FileNotFoundError if the resolved path does not point to a
    regular file. Path traversal up out of the source tree is allowed --
    callers are expected to vet the source root.
    """
    base = importer.parent.resolve()
    candidate = (base / target).resolve()
    if not candidate.is_file():
        raise FileNotFoundError(
            f"import target not found: {target!r} (from {importer.name}, "
            f"resolved to {candidate})"
        )
    return candidate
