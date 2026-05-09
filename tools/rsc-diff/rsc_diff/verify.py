"""Apply an .rsc patch on top of a Config and check semantic equality.

Used to validate that the rollforward/rollback patches emitted by ``rsc-diff``
actually transform a *live* router state into a *candidate* config and back
again.

NOT a production interpreter -- the simulator only handles the ops that
``rsc-diff`` currently emits, plus a few selector forms.

CLI
---
Without arguments the tool walks ``c:/src/mikrotik/out`` for ``live.rsc`` and
the most recent bundle (any ``*-<yymmdd>-<secs>.rsc``) plus the canonical
``rollforward.rsc`` / ``rollback.rsc`` patch pair. Override any of those by
passing flags::

    rsc-diff-verify
    rsc-diff-verify --out other-out-dir
    rsc-diff-verify --live a.rsc --candidate b.rsc \\
                    --rollforward fwd.rsc --rollback bwd.rsc
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from rsc_diff import Config, Item, Op, diff, emit, parse_file
from rsc_diff.differ import _strip_identity
from rsc_diff.parser import _logical_lines, _take_bracket, _tokenise_kv


_FIND_RE = re.compile(r'\[find\s+(?P<key>[\w@-]+)(?P<op>[=~])(?P<val>.+)\]')


def find_item(items, selector: str | None) -> Item | None:
    if selector is None:
        return None
    sel = selector.strip()
    if sel == "[find]":
        return None  # wipe sentinel
    m = _FIND_RE.match(sel)
    if not m:
        return None
    key, op, val = m.group("key"), m.group("op"), m.group("val").strip()
    if val.startswith('"') and val.endswith('"'):
        val = val[1:-1]
    if key in ("@anon", "@pos"):
        idx = int(val.lstrip("="))
        return items[idx] if 0 <= idx < len(items) else None
    for it in items:
        v = it.props.get(key, "")
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        if (op == "=" and v == val) or (op == "~" and val in v):
            return it
    return None


def parse_props(rest: str) -> dict[str, str]:
    return {k: v for k, v in _tokenise_kv(rest)}


def deep_copy(cfg: Config) -> Config:
    out = Config()
    for items in cfg.items_by_menu.values():
        for it in items:
            out.add(Item(menu=it.menu, verb=it.verb, props=dict(it.props)))
    return out


def apply_patch(base: Config, patch_path: Path) -> Config:
    cfg = deep_copy(base)
    cur_menu: str | None = None
    text = patch_path.read_text(encoding="utf-8")
    for raw in _logical_lines(text):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("/"):
            cur_menu = line.split()[0]
            continue
        if cur_menu is None:
            continue
        verb, _, rest = line.partition(" ")
        rest = rest.strip()
        items = cfg.items_by_menu.setdefault(cur_menu, [])

        if verb == "remove":
            if rest == "[find]":
                items.clear()
            elif rest.startswith("["):
                bracket, _ = _take_bracket(rest)
                target = find_item(items, bracket)
                if target is not None:
                    items.remove(target)

        elif verb == "add":
            cfg.add(Item(menu=cur_menu, verb="add", props=parse_props(rest)))

        elif verb == "set":
            if rest.startswith("["):
                bracket, after = _take_bracket(rest)
                target = find_item(items, bracket)
                props = parse_props(after.strip())
            else:
                props = parse_props(rest)
                if items:
                    target = items[0]
                else:
                    target = Item(menu=cur_menu, verb="set", props={})
                    cfg.add(target)
            if target is not None:
                target.props.update(props)

        elif verb == "reset":
            if rest.startswith("["):
                bracket, after = _take_bracket(rest)
                target = find_item(items, bracket)
                names = after.strip().split()
            else:
                names = rest.strip().split()
                target = items[0] if items else None
            if target is not None:
                for n in names:
                    target.props.pop(n, None)

    return cfg


def menu_signature(items):
    return Counter(
        tuple(sorted(_strip_identity(it.props).items())) for it in items
    )


def cfg_diff_summary(a: Config, b: Config) -> list[str]:
    """Crude signature compare. Used as a fallback summary only -- the main
    verification path runs the differ and reports residual ops, which is the
    semantically authoritative answer."""
    diffs = []
    all_menus = sorted(set(a.menus()) | set(b.menus()))
    for menu in all_menus:
        ai = menu_signature(a.items_by_menu.get(menu, []))
        bi = menu_signature(b.items_by_menu.get(menu, []))
        if ai != bi:
            only_a = ai - bi
            only_b = bi - ai
            diffs.append(
                f"{menu}: in_left_only={sum(only_a.values())} "
                f"in_right_only={sum(only_b.values())}"
            )
    return diffs


def residual_ops(result: Config, target: Config, *, strict: bool = False, lenient_defaults: bool = False) -> list[Op]:
    """What additional ops would the differ emit to get from *result* to *target*?

    Empty list = patch round-tripped semantically. Re-using the differ here is
    the gold standard: it knows about per-menu defaults, computed properties,
    and identity matching, so we don't have to reimplement any of that just
    to verify a round-trip.
    """
    return diff(result, target, strict=strict, lenient_defaults=lenient_defaults)


def _autodetect_candidate(out_dir: Path) -> Path:
    # any bundled .rsc named "<stem>-<yymmdd>-<secs>.rsc"; exclude live.rsc
    # and well-known patch outputs.
    skip = {"live.rsc", "rollforward.rsc", "rollback.rsc"}
    matches = sorted(
        p for p in out_dir.glob("*.rsc")
        if p.name not in skip and re.search(r"-\d{6}-\d+\.rsc$", p.name)
    )
    if not matches:
        raise SystemExit(f"no candidate bundle in {out_dir} (looked for *-yymmdd-secs.rsc)")
    # most recent by name (bundler suffix is sortable)
    return matches[-1]


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rsc-diff-verify",
        description="Verify rollforward/rollback patches round-trip live <-> candidate.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path(r"c:\src\mikrotik\out"),
        help=r"output dir to scan (default: c:\src\mikrotik\out)",
    )
    p.add_argument("--live", type=Path, help="path to live router export (default: <out>/live.rsc)")
    p.add_argument("--candidate", type=Path, help="path to candidate bundle (default: latest *-yymmdd-secs.rsc in --out)")
    p.add_argument("--rollforward", type=Path, help="path to rollforward patch (default: <out>/rollforward.rsc)")
    p.add_argument("--rollback", type=Path, help="path to rollback patch (default: <out>/rollback.rsc)")
    p.add_argument(
        "--strict",
        action="store_true",
        help="pass strict=True to the differ when scoring residual ops (no per-menu "
             "defaults / computed-property normalization). Useful to surface every "
             "textual delta, not just the semantically-meaningful ones.",
    )
    p.add_argument(
        "--lenient",
        action="store_true",
        help="pass lenient_defaults=True to the differ. Suppresses asymmetric "
             "drift where one side has explicit neutral value (no/false/none/0/0s/'') "
             "and the other side is silent. RISK: hides real drift if the actual "
             "default is non-neutral.",
    )
    p.add_argument(
        "--show-ops",
        action="store_true",
        help="print the full residual patch text for each leg, not just a per-menu summary.",
    )
    return p


def _summarize_ops(ops: list[Op]) -> list[str]:
    """Group residual ops by menu + kind for a compact failure report."""
    by_menu: dict[str, Counter[str]] = {}
    for op in ops:
        by_menu.setdefault(op.menu, Counter())[op.kind] += 1
    out = []
    for menu in sorted(by_menu):
        counts = by_menu[menu]
        parts = ", ".join(f"{kind}={n}" for kind, n in sorted(counts.items()))
        out.append(f"{menu}: {parts}")
    return out


def _report_leg(
    label: str, result: Config, target: Config, *, strict: bool, lenient_defaults: bool, show_ops: bool
) -> bool:
    """Run one verification leg. Returns True on pass."""
    print(f"--- {label} ---")
    ops = residual_ops(result, target, strict=strict, lenient_defaults=lenient_defaults)
    if not ops:
        print("  OK -- differ reports no residual drift")
        return True
    print(f"  DRIFT -- {len(ops)} residual op(s):")
    for line in _summarize_ops(ops):
        print(f"    {line}")
    if show_ops:
        print()
        for line in emit(ops).splitlines():
            print(f"    {line}")
    return False


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    out = args.out

    live_path = args.live or (out / "live.rsc")
    cand_path = args.candidate or _autodetect_candidate(out)
    fwd_path = args.rollforward or (out / "rollforward.rsc")
    bwd_path = args.rollback or (out / "rollback.rsc")

    for p in (live_path, cand_path, fwd_path, bwd_path):
        if not p.is_file():
            raise SystemExit(f"missing input: {p}")

    live = parse_file(live_path)
    cand = parse_file(cand_path)

    print(f"live        = {live_path}")
    print(f"candidate   = {cand_path}")
    print(f"rollforward = {fwd_path}")
    print(f"rollback    = {bwd_path}")
    print(f"strict      = {args.strict}")
    print(f"lenient     = {args.lenient}")
    print()

    passes = 0
    if _report_leg(
        "live + rollforward.rsc should equal candidate",
        apply_patch(live, fwd_path), cand,
        strict=args.strict, lenient_defaults=args.lenient, show_ops=args.show_ops,
    ):
        passes += 1
    print()
    if _report_leg(
        "candidate + rollback.rsc should equal live",
        apply_patch(cand, bwd_path), live,
        strict=args.strict, lenient_defaults=args.lenient, show_ops=args.show_ops,
    ):
        passes += 1

    return 0 if passes == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
