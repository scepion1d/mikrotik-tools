"""rsc.yaml -- render YAML profile sources into RouterOS ``.rsc`` text.

A small, self-contained YAML-to-RSC renderer paired with the YAML schema
authored under ``src/`` (see ``src/README.md``). Two document shapes are
recognised:

- **Module YAML** -- a tree of RouterOS menus and their ops. Renders to
  one or more ``/menu/path`` blocks followed by ``add`` / ``set`` lines.
- **Globals YAML** -- a flat ``globals:`` list of ``name``/``value``
  entries. Renders to ``:global NAME "VALUE"`` declarations, one per
  entry.

The renderer is deliberately single-purpose: it produces ``.rsc`` text
that is valid input for the existing bundler pipeline
(``rsc.bundle.flatten`` -> ``rsc.parser`` -> ``rsc.bundle.compact``).
It does NOT try to preserve the source whitespace / line wrapping --
the bundler will reformat anyway.

Public API
----------
- :func:`to_rsc`        -- render a YAML string to ``.rsc`` text
- :func:`to_rsc_file`   -- read + render a ``.yaml`` file from disk
- :class:`YamlError`    -- raised on malformed YAML or schema violations
"""

from __future__ import annotations

from .converter import YamlError, to_rsc, to_rsc_file

__version__ = "0.1.0"

__all__ = [
    "YamlError",
    "__version__",
    "to_rsc",
    "to_rsc_file",
]
