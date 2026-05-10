"""rsc-bundle -- bundle a flat RouterOS .rsc profile folder into one minimal file.

Walks a profile folder (``rsc/<profile>/{secrets.rsc, vars.rsc, NN-*.rsc}``),
resolves ``:global`` variable assignments into property values, strips
RouterOS scripting wrappers (``:if`` / ``:foreach`` / ``$helper`` calls),
and emits a minimal ``/export``-style ``.rsc`` with one operation per line.

A *profile* is a complete, named router configuration variant for the
same physical device (e.g. ``basic`` = single LAN; ``segmented`` = LAN
+ IoT VLAN). One profile folder = one apply-able config.

CLI
---
::

    rsc-bundle rsc/segmented                   # -> ./out/segmented-YYMMDD-XXXXX.rsc
    rsc-bundle rsc/segmented -o builds/        # -> builds/segmented-YYMMDD-XXXXX.rsc
    rsc-bundle rsc/segmented -o my-bundle.rsc  # -> my-bundle.rsc
    rsc-bundle rsc/segmented --keep-comments
    rsc-bundle rsc/segmented --no-flatten

Library
-------
::

    from rsc_bundle import bundle

    text = bundle("rsc/segmented")  # default pipeline
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

from rsc_parser import parse_text

from .bundler import BundleError, bundle_inline, bundle_file
from .compact import emit as _compact_emit
from .flatten import flatten
from .loader import LoaderError, concat, load_profile

__version__ = "0.2.0"


def bundle(
    profile_dir: str,
    *,
    flatten_output: bool = True,
) -> str:
    """Bundle a flat profile folder into a deploy-ready ``.rsc`` string.

    Args:
        profile_dir: path to the profile folder (e.g. ``rsc/segmented``).
        flatten_output: when True (default), substitute ``:global`` vars,
            strip scripting wrappers, parse + re-emit one-line-per-op.
            When False, return the raw concatenated source.
    """
    files = load_profile(profile_dir)
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
    "flatten",
    "load_profile",
]
