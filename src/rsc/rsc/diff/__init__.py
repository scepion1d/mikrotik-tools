"""rsc.diff -- RouterOS ``.rsc`` configuration differ.

A lightweight library + CLI for comparing two RouterOS scripts and
emitting a minimal set of ``add`` / ``set`` / ``reset`` / ``remove``
operations needed to transform one into the other. The roundtrip mode
also verifies that applying the patch and rolling back lands you exactly
where you started.

Designed around the ``iac.<type>.<id>`` naming convention from
:mod:`rsc.parser` for stable item identity across edits.

Quick start
-----------
::

    from rsc.diff import parse_file, diff, emit

    old = parse_file("baseline.rsc")
    new = parse_file("desired.rsc")
    ops = diff(old, new)
    print(emit(ops))

CLI
---
The package installs an ``rsc.diff`` console script::

    rsc.diff --old baseline.rsc --new desired.rsc -o patch.rsc
    rsc.diff --old baseline.rsc --new desired.rsc --check    # exit 1 on drift
    rsc.diff --old live.rsc --new candidate.rsc \\
             --rollforward fwd.rsc --rollback bwd.rsc       # roundtrip mode

See :mod:`rsc.diff.cli` for full flag docs.

Public API
----------
- :func:`parse_file` -- read and parse a ``.rsc`` file from disk
- :func:`parse_text` -- parse a ``.rsc`` string in memory
- :func:`diff` -- compute operations between two :class:`Config` objects
- :func:`emit` -- render a list of :class:`Op` as a runnable patch
- :class:`Config` / :class:`Item` / :class:`Op` -- data model (re-exported
  from :mod:`rsc.parser`)
- :mod:`rsc.diff.verify` -- in-memory patch simulator
  (:func:`~rsc.diff.verify.apply_patch`, :func:`~rsc.diff.verify.residual_ops`)
"""

from .differ import diff
from .emitter import emit
from .model import Config, Item, Op
from rsc.parser import parse_file, parse_text

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
