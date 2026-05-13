"""rsc.bundle -- bundle a flat RouterOS .rsc profile folder into one minimal file.

Walks a profile folder (``rsc/<profile>/NN-*.rsc``) plus an optional
**vars folder** of ``:global`` files (``rsc/`` by convention, holding
``secrets.rsc`` + ``vars.rsc``), resolves ``:global`` variable
assignments into property values, strips RouterOS scripting wrappers
(``:if`` / ``:foreach`` / ``$helper`` calls), and emits a minimal
``/export``-style ``.rsc`` with one operation per line.

A *profile* is a complete, named router configuration variant for the
same physical device (e.g. ``basic`` = single LAN; ``segmented`` = LAN
+ IoT VLAN). One profile folder = one apply-able config.

CLI
---
::

    rsc.bundle --profile rsc/segmented                   # vars dir auto-discovered
    rsc.bundle --profile rsc/segmented -o builds/        # auto-named under builds/
    rsc.bundle --profile rsc/segmented -o my-bundle.rsc  # explicit file path
    rsc.bundle --profile rsc/segmented --vars rsc/        # explicit vars dir
    rsc.bundle --profile rsc/segmented --no-flatten

Library
-------
::

    from rsc.bundle import bundle

    text = bundle("rsc/segmented", vars_dir="rsc/")
    text = bundle("rsc/segmented", flatten_output=False)  # raw concat

The legacy import-inlining API (:func:`bundle_file`, :func:`bundle_inline`,
:class:`BundleError`) is retained for tests and any caller still using
the ``/import file-name=...`` workflow. New code should use
:func:`bundle`.

Public API
----------
- :func:`bundle`         -- folder-in, text-out (default pipeline)
- :func:`flatten`        -- post-pass: vars + scripting strip + quoting
- :func:`load_profile`   -- enumerate profile files in load order
- :class:`LoaderError`   -- raised on malformed profile folder
- :func:`bundle_file`    -- legacy: bundle from disk via /import inlining
- :func:`bundle_inline`  -- legacy: bundle from in-memory ``{name: text}``
- :class:`BundleError`   -- legacy: raised on missing import / cycle
"""

from __future__ import annotations

from pathlib import Path

from rsc.parser import parse_text

from .bundler import BundleError, bundle_inline, bundle_file
from .compact import emit as _compact_emit
from .flatten import flatten
from .loader import (
    LoaderError,
    concat,
    concat_named,
    load_profile,
    load_yaml_profile,
)

__version__ = "0.5.0"


def bundle(
    profile_dir: str | Path,
    *,
    vars_dir: str | Path | None = None,
    yaml: bool = False,
    flatten_output: bool = True,
) -> str:
    """Bundle a flat profile folder into a deploy-ready ``.rsc`` string.

    Args:
        profile_dir: path to the profile folder (e.g. ``rsc/segmented``
            or, with ``yaml=True``, ``src/segmented``).
        vars_dir: optional folder of ``:global`` files to load before
            the profile modules. Every matching file at the top level
            (alphabetical) is included. Pass ``None`` (default) to skip.
            The convention is to point this at the shared
            ``<profile-parent>`` folder (e.g. ``rsc/`` or ``src/``).
        yaml: when True, treat *profile_dir* and *vars_dir* as YAML
            sources (``.yaml``). Each file is rendered to ``.rsc`` text
            via :mod:`rsc.yaml` before the normal pipeline runs.
        flatten_output: when True (default), substitute ``:global``
            vars, strip scripting wrappers, parse + re-emit
            one-line-per-op. When False, return the raw concatenated
            source.
    """
    if yaml:
        pairs = load_yaml_profile(profile_dir, vars_dir=vars_dir)
        text = concat_named(pairs)
    else:
        files = load_profile(profile_dir, vars_dir=vars_dir)
        text = concat(files)
    if not flatten_output:
        return text
    flat = flatten(text)
    cfg = parse_text(flat)
    return _compact_emit(cfg)


__all__ = [
    "BundleError",
    "LoaderError",
    "__version__",
    "bundle_inline",
    "bundle_file",
    "bundle",
    "concat_named",
    "flatten",
    "load_profile",
    "load_yaml_profile",
]
