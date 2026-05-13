"""Profile-folder loader.

Walks a flat profile directory of authored ``.rsc`` modules and returns
them in the order the bundler should concatenate them. Layout convention::

    rsc/
        secrets.rsc      # :global secret values   (shared across profiles)
        vars.rsc         # :global non-secret values (shared across profiles)
        <profile>/
            10-interfaces.rsc
            20-wifi.rsc
            30-ip.rsc
            40-firewall.rsc
            50-services.rsc
            60-system.rsc

A *profile* is a complete, named router configuration variant for the
same physical device (e.g. ``basic`` = single LAN; ``segmented`` = LAN
+ IoT VLAN). One profile folder = one apply-able config.

Globals (``:global`` definitions) live in a separate **vars folder**
that is passed explicitly to :func:`load_profile` as a directory path.
Every ``*.rsc`` at the top level of that folder is loaded, in
case-insensitive alphabetical order, before the profile's own modules.
This lets every profile share one set of ``:global`` files from a
central location while keeping per-profile folders limited to numbered
modules.

Load order is: every ``*.rsc`` at the top level of *vars_dir* (alpha,
case-insensitive) -> every ``*.rsc`` at the top level of *profile_dir*
(same sort). Vars come first because :func:`rsc.bundle.flatten.flatten`
collects ``:global NAME "value"`` assignments on a first pass and
substitutes them throughout the text on a second pass -- so the values
must appear textually before any module that references ``$NAME``.

The numeric ``NN-`` filename prefix is the operator-controlled ordering
hook: keep dependencies pointing back at lower-numbered modules.
"""

from __future__ import annotations

from pathlib import Path


class LoaderError(Exception):
    """Raised when a profile folder is missing required files or is malformed."""


def load_profile(
    profile_dir: str | Path,
    *,
    vars_dir: str | Path | None = None,
) -> list[Path]:
    """Return the ordered list of ``.rsc`` files to bundle for *profile_dir*.

    Order is: every ``*.rsc`` at the top level of *vars_dir* (when
    given), then every ``*.rsc`` at the top level of *profile_dir* --
    both groups sorted case-insensitively by filename. Subdirectories
    are NOT traversed in either folder; the layout is intentionally flat
    (see module docstring).

    *vars_dir* is optional but, when supplied, must point at an existing
    directory. It lives outside *profile_dir* (one shared folder under
    ``rsc/`` is the convention) and is passed explicitly so the same
    profile folder can be bundled against different variable sets
    without renaming files.

    An empty *vars_dir* (no ``*.rsc`` at the top level) is allowed and
    silently contributes nothing.

    Raises
    ------
    LoaderError
        If *profile_dir* is not a directory, contains no ``.rsc`` files,
        or if *vars_dir* is supplied but does not point at a directory.
    """
    root = Path(profile_dir)
    if not root.is_dir():
        raise LoaderError(f"profile folder not found: {root}")

    modules = _list_rsc(root)
    if not modules:
        raise LoaderError(f"no .rsc files in profile folder: {root}")

    ordered: list[Path] = []
    if vars_dir is not None:
        vroot = Path(vars_dir)
        if not vroot.is_dir():
            raise LoaderError(f"vars folder not found: {vroot}")
        ordered.extend(_list_rsc(vroot))

    ordered.extend(modules)
    return ordered


def _list_rsc(folder: Path) -> list[Path]:
    """Top-level ``*.rsc`` files in *folder*, alpha-sorted (case-insensitive).

    Case-insensitive sort so the same order is produced on Windows
    (NTFS) and Linux. Subdirectories are skipped; we rely on filename,
    not stat order, so the result is reproducible across runs.
    """
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".rsc"),
        key=lambda p: p.name.lower(),
    )


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
