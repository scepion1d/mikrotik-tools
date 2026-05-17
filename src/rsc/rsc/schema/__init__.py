"""Bundled JSON Schema fragments for the rsc YAML profile shape.

The schema is split into fragments under this package so it can grow
without becoming unwieldy:

- ``_root.json``      -- top-level schema shape + ``globals`` / ``module``.
- ``_common.json``    -- shared primitives (``iacToken``, ``vlanId``,
  ``ipv4Cidr``, ``duration``, ...).
- ``menu_*.json``     -- one file per RouterOS top-level menu, holding both
  the ``menu_*`` shape definitions and the ``item_*`` row schemas that
  belong to it (``menu_interface.json``, ``menu_ip.json``, ...).

Public API:

- :func:`bundle`  -- merge all fragments into one dict (the runtime
  ``jsonschema``-ready schema).
- :func:`render`  -- :func:`bundle` serialised to a stable JSON string
  (2-space indent, trailing newline) for writing to disk.

The CLI wrapper ``rsc schema`` (see :mod:`rsc.schema.cli`) calls
:func:`render` to write ``src/schema.json`` for the VS Code YAML
extension; ``rsc.yaml.validate`` consumes the same bundle directly via
:func:`bundle` so the in-package schema and the on-disk file never
drift in normal use.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["bundle", "render"]


_HERE = Path(__file__).resolve().parent

_ROOT_FRAGMENT = "_root.json"

# Files merged first, in this exact order, before the rest is appended
# alphabetically. ``_root.json`` defines the top-level schema shape;
# ``_common.json`` defines shared primitives that menu fragments $ref.
_PREFERRED_ORDER = (_ROOT_FRAGMENT, "_common.json")


def _ordered_fragments() -> list[Path]:
    """Return fragment paths in the order they should be merged."""
    by_name = {p.name: p for p in _HERE.glob("*.json")}
    if _ROOT_FRAGMENT not in by_name:
        raise FileNotFoundError(f"missing root fragment: {_HERE / _ROOT_FRAGMENT}")
    ordered: list[Path] = []
    for name in _PREFERRED_ORDER:
        path = by_name.pop(name, None)
        if path is not None:
            ordered.append(path)
    ordered.extend(by_name[name] for name in sorted(by_name))
    return ordered


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load schema fragment {path}: {exc}") from exc


def bundle() -> dict:
    """Build the merged JSON Schema dict from packaged fragments.

    Raises :class:`ValueError` if two fragments define the same key under
    ``definitions``; that condition is always a bug (a typo silently
    shadowing an existing entry would cause hard-to-trace validation
    misses).
    """
    ordered = _ordered_fragments()
    root = _load(ordered[0])
    merged = root.setdefault("definitions", {})
    for path in ordered[1:]:
        frag = _load(path)
        for key, value in (frag.get("definitions") or {}).items():
            if key in merged:
                raise ValueError(f"duplicate definition `{key}` in {path.name}")
            merged[key] = value
    return root


def render() -> str:
    """Return :func:`bundle` as a stable JSON string.

    2-space indent, ``ensure_ascii=False`` (preserves any unicode in
    description text), and a single trailing newline so re-runs are
    diff-free.
    """
    return json.dumps(bundle(), indent=2, ensure_ascii=False) + "\n"
