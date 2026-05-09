"""Build a basename -> Path map by walking a source root."""

from __future__ import annotations

from pathlib import Path


def build_source_map(root: Path | str, *, suffix: str = ".rsc") -> dict[str, Path]:
    """Walk *root* recursively and return ``{basename: absolute_path}``.

    Mirrors the RouterOS flat-flash model: every ``.rsc`` anywhere under
    *root* is reachable by its basename alone. Raises ValueError on duplicate
    basenames -- the router can't disambiguate either, so neither do we.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise NotADirectoryError(f"source root not a directory: {root_path}")

    out: dict[str, Path] = {}
    for path in sorted(root_path.rglob(f"*{suffix}")):
        if not path.is_file():
            continue
        if path.name in out:
            raise ValueError(
                f"duplicate basename {path.name!r}: "
                f"{out[path.name]} and {path}"
            )
        out[path.name] = path.resolve()
    return out
