"""rsc-bundle -- inline RouterOS ``/import`` directives into a single ``.rsc``.

Walks an entry script, replaces each ``/import file-name=NAME.rsc`` with the
contents of NAME.rsc resolved by basename, and writes one self-contained file.

Quick start
-----------
::

    from rsc_bundle import bundle_file

    text = bundle_file("rsc/apply-full.rsc", root="rsc")
    Path("bundled.rsc").write_text(text)

CLI
---
The package installs an ``rsc-bundle`` console script::

    rsc-bundle rsc/apply-full.rsc --root rsc -o bundled.rsc

Public API
----------
- :func:`bundle_file` -- read entry from disk, resolve via filesystem walk
- :func:`bundle` -- bundle from an explicit ``{basename: text}`` map
- :func:`flatten` -- post-pass that resolves ``:global`` vars and strips RouterOS
  scripting wrappers (``:if`` / ``:foreach`` / helper invocations)
- :class:`BundleError` -- raised on missing import / cycle
"""

from .bundler import BundleError, bundle, bundle_file
from .flatten import flatten

__version__ = "0.1.0"

__all__ = [
    "BundleError",
    "__version__",
    "bundle",
    "bundle_file",
]
