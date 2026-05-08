"""rsc-diff -- RouterOS ``.rsc`` configuration differ.

A lightweight, dependency-free library for comparing two RouterOS scripts
and emitting a minimal set of ``add`` / ``set`` / ``remove`` operations
needed to transform one into the other.

Designed around an ``iac.<type>.<id>`` naming convention for stable item
identity across edits.

Quick start
-----------
::

    from rsc_diff import parse_file, diff, emit

    old = parse_file("baseline.rsc")
    new = parse_file("desired.rsc")
    ops = diff(old, new)
    print(emit(ops))

CLI
---
The package also installs an ``rsc-diff`` console script::

    rsc-diff old.rsc new.rsc -o patch.rsc
    rsc-diff old.rsc new.rsc --check    # exit 1 on drift, for CI

Public API
----------
- :func:`parse_file` -- read and parse a ``.rsc`` file from disk
- :func:`parse_text` -- parse a ``.rsc`` string in memory
- :func:`diff` -- compute operations between two :class:`Config` objects
- :func:`emit` -- render a list of :class:`Op` as a runnable patch
- :class:`Config` -- parsed config (``{menu_path: [Item]}``)
- :class:`Item` -- one parsed config item with identity resolution
- :class:`Op` -- one diff operation (``add`` / ``set`` / ``remove``)
"""

from .differ import diff
from .emitter import emit
from .model import Config, Item, Op
from .parser import parse_file, parse_text

__version__ = "0.1.0"

__all__ = [
    "Config",
    "Item",
    "Op",
    "__version__",
    "diff",
    "emit",
    "parse_file",
    "parse_text",
]
