"""Profile-folder loader.

Walks a flat profile directory of authored ``.rsc`` files and returns
them in the order the bundler should concatenate them. Layout convention
(set in phase 2 of the refactor)::

    rsc/<profile>/
        secrets.rsc      # :global secret values
        vars.rsc         # :global non-secret values
        10-interfaces.rsc
        20-wifi.rsc
        30-ip.rsc
        40-firewall.rsc
        50-services.rsc
        60-system.rsc

A *profile* is a complete, named router configuration variant for the
same physical device (e.g. ``basic`` = single LAN; ``segmented`` = LAN
+ IoT VLAN). One profile folder = one apply-able config.

Load order is ``secrets.rsc`` -> ``vars.rsc`` -> every other ``.rsc`` in
**alphabetical** filename order. Putting secrets/vars first matters
because :func:`rsc.bundle.flatten.flatten` collects ``:global NAME
"value"`` assignments on a first pass and substitutes them throughout the
text on a second pass -- so the values must appear textually before any
module that references ``$NAME``.

The numeric ``NN-`` filename prefix is the operator-controlled ordering
hook: keep dependencies pointing back at lower-numbered modules.
"""

from __future__ import annotations

from pathlib import Path


# Filenames that must be loaded before any other module so that
# ``:global NAME "value"`` assignments are seen before ``$NAME`` is
# referenced anywhere downstream. Order within this list is the load
# order used.
_GLOBAL_FILES: tuple[str, ...] = ("secrets.rsc", "vars.rsc")


class LoaderError(Exception):
    """Raised when a profile folder is missing required files or is malformed."""


def load_profile(profile_dir: str | Path) -> list[Path]:
    """Return the ordered list of ``.rsc`` files to bundle for *profile_dir*.

    Order is: ``secrets.rsc``, ``vars.rsc``, then every other ``*.rsc``
    file at the top level of *profile_dir* in alphabetical
    (case-insensitive) order. Subdirectories are NOT traversed -- the
    layout is intentionally flat (see module docstring).

    Raises
    ------
    LoaderError
        If *profile_dir* is not a directory, or contains no ``.rsc`` files.
    """
    root = Path(profile_dir)
    if not root.is_dir():
        raise LoaderError(f"profile folder not found: {root}")

    # Top-level *.rsc only; case-insensitive alphabetical sort so the same
    # order is produced on Windows (NTFS) and Linux. We rely on filename,
    # not stat order, so the result is reproducible across runs.
    all_rsc = sorted(
        (p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".rsc"),
        key=lambda p: p.name.lower(),
    )
    if not all_rsc:
        raise LoaderError(f"no .rsc files in profile folder: {root}")

    by_name = {p.name: p for p in all_rsc}

    ordered: list[Path] = []
    for name in _GLOBAL_FILES:
        if name in by_name:
            ordered.append(by_name.pop(name))
    # Everything else, in the alphabetical order from above (minus the
    # ones we just consumed). by_name preserves insertion order from
    # the sorted iteration -- so this is still deterministic.
    ordered.extend(by_name.values())
    return ordered


def concat(files: list[Path]) -> str:
    """Concatenate the contents of *files* with section banners between them.

    Banners use ``# ====`` style comments which :func:`rsc.bundle.flatten`
    does not touch and the parser ignores. They make the unminified
    intermediate easier to debug; ``compact.emit`` strips them out when
    producing the final bundle.
    """
    parts: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        parts.append(f"# >>> begin {path.name}\n")
        parts.append(text if text.endswith("\n") else text + "\n")
        parts.append(f"# <<< end {path.name}\n")
    return "".join(parts)
