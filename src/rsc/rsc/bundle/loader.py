"""Profile-folder loader.

Walks a flat profile directory of authored ``.rsc`` modules (or, in
YAML mode, ``.yaml`` modules rendered to ``.rsc`` text on the fly) and
returns them in the order the bundler should concatenate them. Layout
convention::

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
Every ``*.rsc`` (or ``*.yaml``) at the top level of that folder is
loaded, in case-insensitive alphabetical order, before the profile's
own modules. This lets every profile share one set of ``:global``
files from a central location while keeping per-profile folders
limited to numbered modules.

Load order is: every file at the top level of *vars_dir* (alpha,
case-insensitive) -> every file at the top level of *profile_dir*
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
    return _load_files(profile_dir, vars_dir=vars_dir, suffix=".rsc")


def load_yaml_profile(
    profile_dir: str | Path,
    *,
    vars_dir: str | Path | None = None,
) -> list[tuple[str, str]]:
    """Same as :func:`load_profile` but for YAML sources.

    Globs ``*.yaml`` at the top level of both folders, renders each
    file via :func:`rsc.yaml.to_rsc_file`, and returns ``(name, text)``
    pairs in load order. The returned name uses a ``.rsc`` extension
    so the banners produced by :func:`concat_named` read naturally
    (``# >>> begin secrets.rsc`` rather than ``... secrets.yaml``);
    this also matches what the existing ``--no-flatten`` consumers
    expect to see.

    Raises
    ------
    LoaderError
        Same conditions as :func:`load_profile` (with ``.yaml`` instead
        of ``.rsc``), plus any :class:`~rsc.yaml.YamlError` from the
        per-file render is wrapped and re-raised as ``LoaderError``.
    """
    # Local import: keeps the YAML opt-in genuinely opt-in -- the
    # default `load_profile` path doesn't pay for pyyaml import time
    # or even require pyyaml to be installed for an `.rsc`-only build.
    from rsc.yaml import YamlError, to_rsc_file

    paths = _load_files(profile_dir, vars_dir=vars_dir, suffix=".yaml")
    rendered: list[tuple[str, str]] = []
    for path in paths:
        try:
            text = to_rsc_file(path)
        except YamlError as exc:
            raise LoaderError(str(exc)) from exc
        # Use the .rsc-named banner so downstream tooling can't tell
        # the bundle came from YAML (it shouldn't matter; the bundle
        # output is the same .rsc either way).
        rendered.append((path.with_suffix(".rsc").name, text))
    return rendered


def _load_files(
    profile_dir: str | Path,
    *,
    vars_dir: str | Path | None,
    suffix: str,
) -> list[Path]:
    """Shared discovery for ``.rsc`` and ``.yaml`` modes.

    Resolves both the vars folder (when supplied) and the profile
    folder, applies the same alphabetical ordering, and concatenates
    them so the caller gets one flat list with vars first.
    """
    root = Path(profile_dir)
    if not root.is_dir():
        raise LoaderError(f"profile folder not found: {root}")

    modules = _list_files(root, suffix)
    if not modules:
        raise LoaderError(f"no {suffix} files in profile folder: {root}")

    ordered: list[Path] = []
    if vars_dir is not None:
        vroot = Path(vars_dir)
        if not vroot.is_dir():
            raise LoaderError(f"vars folder not found: {vroot}")
        ordered.extend(_list_files(vroot, suffix))

    ordered.extend(modules)
    return ordered


def _list_files(folder: Path, suffix: str) -> list[Path]:
    """Top-level files in *folder* matching *suffix*, alpha-sorted (CI).

    Case-insensitive sort so the same order is produced on Windows
    (NTFS) and Linux. Subdirectories are skipped; we rely on filename,
    not stat order, so the result is reproducible across runs.
    """
    s = suffix.lower()
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == s),
        key=lambda p: p.name.lower(),
    )


def concat(files: list[Path]) -> str:
    """Concatenate the contents of *files* with section banners between them.

    Banners use ``# ====`` style comments which :func:`rsc.bundle.flatten`
    does not touch and the parser ignores. They make the unminified
    intermediate easier to debug; ``compact.emit`` strips them out when
    producing the final bundle.

    Convenience wrapper around :func:`concat_named` for the common case
    where the source is a list of on-disk paths.
    """
    return concat_named(
        [(p.name, p.read_text(encoding="utf-8")) for p in files],
    )


def concat_named(items: list[tuple[str, str]]) -> str:
    """Concatenate ``(name, text)`` pairs with the same banner format.

    Used by :func:`load_yaml_profile` (which has no on-disk ``.rsc``
    paths to point banners at) and reusable for any other in-memory
    source that wants to feed the bundler pipeline.
    """
    parts: list[str] = []
    for name, text in items:
        parts.append(f"# >>> begin {name}\n")
        parts.append(text if text.endswith("\n") else text + "\n")
        parts.append(f"# <<< end {name}\n")
    return "".join(parts)
