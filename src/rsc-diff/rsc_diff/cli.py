"""Command-line entry point for rsc-diff.

Two modes
---------

**Single patch** (default)::

    rsc-diff --old OLD --new NEW [-o OUT] [--strict|--lenient] [--check]

Emits one patch that transforms ``OLD`` into ``NEW``. ``-o``/``--out``
accepts a file path or directory; if omitted (or pointed at a directory)
the patch lands at
``<dir-or-./out>/<old-stem>-<new-stem>-<yymmdd>-<secs>.rsc``.

**End-to-end roundtrip**::

    rsc-diff --old OLD --new NEW --rollforward FWD --rollback BWD
             [--strict|--lenient]

Emits BOTH the rollforward (``OLD -> NEW``) and rollback (``NEW -> OLD``)
patches, then replays them in-memory using :func:`rsc_diff.verify.apply_patch`
to assert::

    apply(OLD, FWD) == NEW
    apply(NEW, BWD) == OLD

Exits non-zero on any residual drift in either leg. This subsumes the
former ``rsc-diff-verify`` command. ``--rollforward`` and ``--rollback``
must both be given (and write to file paths, not directories).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from .differ import diff
from .emitter import emit
from .model import Config
from .parser import parse_file
from .verify import apply_patch, residual_ops


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    old_path = args.old
    new_path = args.new

    if not old_path.is_file():
        print(f"rsc-diff: old file not found: {old_path}", file=sys.stderr)
        return 2
    if not new_path.is_file():
        print(f"rsc-diff: new file not found: {new_path}", file=sys.stderr)
        return 2

    old_cfg = parse_file(old_path)
    new_cfg = parse_file(new_path)

    # Two-leg roundtrip mode: both --rollforward and --rollback must be set.
    if args.rollforward or args.rollback:
        if not (args.rollforward and args.rollback):
            print(
                "rsc-diff: --rollforward and --rollback must be used together",
                file=sys.stderr,
            )
            return 2
        return _run_roundtrip(
            old_path, new_path, old_cfg, new_cfg,
            fwd_path=args.rollforward, bwd_path=args.rollback,
            strict=args.strict, lenient=args.lenient,
        )

    # Single-direction mode.
    return _run_single(
        old_path, new_path, old_cfg, new_cfg,
        out=args.out, check=args.check,
        strict=args.strict, lenient=args.lenient,
    )


# --------------------------------------------------------------------------
# argparse
# --------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rsc-diff",
        description=(
            "Diff two RouterOS .rsc configs into a patch. With "
            "--rollforward/--rollback also produces the reverse patch and "
            "verifies both legs in-memory."
        ),
    )

    p.add_argument(
        "--old", type=Path, required=True,
        help="path to baseline (current) .rsc",
    )
    p.add_argument(
        "--new", type=Path, required=True,
        help="path to target (desired) .rsc",
    )
    p.add_argument(
        "-o", "--out", dest="out", type=Path, default=None,
        help=(
            "where to write the patch in single-patch mode. File path or "
            "directory accepted; if omitted (or a dir), the file lands at "
            "<dir-or-./out>/<old-stem>-<new-stem>-<yymmdd>-<secs>.rsc."
        ),
    )
    p.add_argument(
        "--rollforward", type=Path, default=None,
        help=(
            "roundtrip mode: write the OLD->NEW patch here. Requires "
            "--rollback. File path expected (no auto-naming)."
        ),
    )
    p.add_argument(
        "--rollback", type=Path, default=None,
        help=(
            "roundtrip mode: write the NEW->OLD patch here. Requires "
            "--rollforward. File path expected (no auto-naming)."
        ),
    )
    p.add_argument(
        "--check", action="store_true",
        help=(
            "single-patch mode only: exit 1 if any operations would be "
            "emitted (suitable for CI)."
        ),
    )
    p.add_argument(
        "--strict", action="store_true",
        help=(
            "disable per-menu defaults + computed-property normalisation. "
            "Use this for the FIRST diff against an unfamiliar router so "
            "any defaults-table miscalibration surfaces as visible drift."
        ),
    )
    p.add_argument(
        "--lenient", action="store_true",
        help=(
            "suppress asymmetric drift where one side has an explicit "
            "neutral value (no/false/none/0/0s/empty) and the other side "
            "is silent. Useful for diffing authored configs against /export "
            "output that omits default-valued props. RISK: hides real drift "
            "if the actual default is non-neutral. Prefer extending "
            "defaults.py once verified."
        ),
    )
    return p


# --------------------------------------------------------------------------
# single-patch mode
# --------------------------------------------------------------------------


def _run_single(
    old_path: Path, new_path: Path, old_cfg: Config, new_cfg: Config,
    *, out: Path | None, check: bool, strict: bool, lenient: bool,
) -> int:
    ops = diff(old_cfg, new_cfg, strict=strict, lenient_defaults=lenient)

    if check:
        if ops:
            print(f"rsc-diff: {len(ops)} operation(s) pending", file=sys.stderr)
            return 1
        return 0

    header_lines = [f"old: {old_path}", f"new: {new_path}"]
    if strict:
        header_lines.append("strict mode: defaults + computed normalisation OFF")
    if lenient:
        header_lines.append("lenient mode: explicit-neutral vs missing suppressed")
    text = emit(ops, header="\n".join(header_lines))

    out_path = _resolve_out_path(out, old_path, new_path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"rsc-diff: cannot create output dir {out_path.parent}: {exc}",
            file=sys.stderr,
        )
        return 2
    out_path.write_text(text, encoding="utf-8")
    print(out_path)
    return 0


def _resolve_out_path(out: Path | None, old: Path, new: Path) -> Path:
    """Decide where to write the single-direction patch.

    Rules
    -----
    - ``out is None``                  -> ``./out/<old>-<new>-<stamp>.rsc``
    - ``out`` exists as a directory    -> ``<out>/<old>-<new>-<stamp>.rsc``
    - bare name w/o extension and the
      path doesn't exist               -> treated as a directory, same as above
    - otherwise                        -> ``out`` used verbatim as a file path
    """
    stamp_name = _build_output_name(old.stem, new.stem)
    if out is None:
        return Path("out") / stamp_name
    if out.is_dir():
        return out / stamp_name
    if out.suffix == "" and not out.exists():
        return out / stamp_name
    return out


def _build_output_name(old_stem: str, new_stem: str) -> str:
    """``<old>-<new>-<yymmdd>-<seconds-since-midnight>.rsc``."""
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    secs = int((now - midnight).total_seconds())
    stamp = now.strftime("%y%m%d") + f"-{secs}"
    return f"{old_stem}-{new_stem}-{stamp}.rsc"


# --------------------------------------------------------------------------
# end-to-end roundtrip mode
# --------------------------------------------------------------------------


def _run_roundtrip(
    old_path: Path, new_path: Path, old_cfg: Config, new_cfg: Config,
    *, fwd_path: Path, bwd_path: Path, strict: bool, lenient: bool,
) -> int:
    """Emit both patches, replay them in-memory, report residual drift.

    Returns 0 iff both legs round-trip cleanly.
    """
    fwd_ops = diff(old_cfg, new_cfg, strict=strict, lenient_defaults=lenient)
    bwd_ops = diff(new_cfg, old_cfg, strict=strict, lenient_defaults=lenient)

    fwd_header = f"rollforward: {old_path} -> {new_path}"
    bwd_header = f"rollback: {new_path} -> {old_path}"

    for path, ops, header in (
        (fwd_path, fwd_ops, fwd_header),
        (bwd_path, bwd_ops, bwd_header),
    ):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(
                f"rsc-diff: cannot create output dir {path.parent}: {exc}",
                file=sys.stderr,
            )
            return 2
        path.write_text(emit(ops, header=header), encoding="utf-8")

    print(f"rollforward = {fwd_path} ({len(fwd_ops)} ops)")
    print(f"rollback    = {bwd_path} ({len(bwd_ops)} ops)")
    print(f"strict      = {strict}")
    print(f"lenient     = {lenient}")
    print()

    # Replay leg 1: apply(old, fwd) should equal new.
    leg1_ok = _verify_leg(
        f"apply({old_path.name}, {fwd_path.name}) == {new_path.name}",
        base=old_cfg, patch=fwd_path, target=new_cfg,
        strict=strict, lenient=lenient,
    )
    print()
    # Replay leg 2: apply(new, bwd) should equal old.
    leg2_ok = _verify_leg(
        f"apply({new_path.name}, {bwd_path.name}) == {old_path.name}",
        base=new_cfg, patch=bwd_path, target=old_cfg,
        strict=strict, lenient=lenient,
    )

    return 0 if (leg1_ok and leg2_ok) else 1


def _verify_leg(
    label: str, *, base: Config, patch: Path, target: Config,
    strict: bool, lenient: bool,
) -> bool:
    """Apply *patch* on *base* in-memory and report residual ops vs *target*.

    Returns True on round-trip clean, False on residual drift.
    """
    print(f"--- {label} ---")
    result = apply_patch(base, patch)
    drift = residual_ops(result, target, strict=strict, lenient_defaults=lenient)
    if not drift:
        print("  OK -- differ reports no residual drift")
        return True
    print(f"  DRIFT -- {len(drift)} residual op(s)")
    # Group by menu + kind for a compact failure report.
    by_menu: dict[str, Counter[str]] = {}
    for op in drift:
        by_menu.setdefault(op.menu, Counter())[op.kind] += 1
    for menu in sorted(by_menu):
        counts = by_menu[menu]
        parts = ", ".join(f"{kind}={n}" for kind, n in sorted(counts.items()))
        print(f"    {menu}: {parts}")
    return False
