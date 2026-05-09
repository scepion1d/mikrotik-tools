# rsc-diff

RouterOS `.rsc` differ + round-trip verifier. Compares two configs (file-vs-file) and emits an apply-able patch of `add` / `set` / `reset` / `remove` ops. The companion `rsc-diff-verify` command checks that applying the patch and rolling back lands you exactly where you started.

## Install

```powershell
.\build.ps1            # uv sync; or run repo-root build.ps1 to also link bin\rsc-diff.cmd + bin\rsc-diff-verify.cmd
```

Python ≥ 3.10. Zero runtime deps.

## CLI

```powershell
# diff
..\..\bin\rsc-diff.cmd old.rsc new.rsc -o patch.rsc
..\..\bin\rsc-diff.cmd old.rsc new.rsc --check         # exit 1 on drift
..\..\bin\rsc-diff.cmd old.rsc new.rsc --strict        # disable defaults + computed normalization
..\..\bin\rsc-diff.cmd old.rsc new.rsc --lenient       # explicit-neutral vs missing -> equal

# verify (defaults to out/live.rsc + latest out/<site>-YYMMDD-XXXXX.rsc bundle + out/rollforward.rsc + out/rollback.rsc)
..\..\bin\rsc-diff-verify.cmd
..\..\bin\rsc-diff-verify.cmd --lenient --show-ops
..\..\bin\rsc-diff-verify.cmd --live live.rsc --candidate candidate.rsc --rollforward fwd.rsc --rollback bwd.rsc
```

## Library

```python
from rsc_diff import parse_file, diff, emit
from rsc_diff.verify import apply_patch, residual_ops

old = parse_file("live.rsc")
new = parse_file("candidate.rsc")
ops = diff(old, new, lenient_defaults=True)
patch_text = emit(ops, header="live -> candidate")

# verify
result = apply_patch(old, "rollforward.rsc")
drift = residual_ops(result, new)        # empty list = round-trip clean
```

| Symbol | Purpose |
|---|---|
| `parse_file(path)` / `parse_text(s)` | `.rsc` → `Config` |
| `diff(old, new, *, strict=False, lenient_defaults=False)` | `Config × Config → list[Op]` |
| `emit(ops, *, header=None)` | `list[Op] → str` |
| `Config` / `Item` / `Op` | Data model |
| `verify.apply_patch(cfg, patch_path)` | Simulator: replay patch ops on a `Config` |
| `verify.residual_ops(result, target)` | Re-runs `diff()`; empty = semantic equality |

## Identity model

Each parsed item gets an `identity_key`:

1. `name=iac.x.y` if the menu accepts a `name` field
2. `comment` containing `iac.x.y` token (firewall rules, leases, ipv6 lists)
3. `default-name=etherN` for built-ins
4. `@anon=N` positional fallback (unnamed items, no `iac.*` token)
5. menu path itself for singletons

Ordered menus (firewall chains, address-list) use **wipe-then-add**: any drift triggers `remove [find]` + full re-add in declaration order.

## Defaults & normalization

`defaults.py` lists per-menu property defaults verified against `/export` output (export omits a prop iff it equals the default). Adding a wrong entry silently erases real drift, so be conservative — comments document the evidence path.

| Mode | Behavior |
|---|---|
| default | Strip identity props, computed props (`network=` derived from `address=`), and known defaults from comparison. |
| `--strict` | Disable both. Surfaces every textual delta. Use for the first diff against an unfamiliar router. |
| `--lenient` | On top of default: suppress drift where one side has explicit `no` / `false` / `none` / `0` / `0s` / `""` and the other side is silent. **Hides real drift if the actual default is non-neutral.** Verification-only; don't generate patches with this. |

## Caveats

- **`/ip/service` positional drift.** Items have no `name=` and no `iac.*` comment, so the differ falls back to `@anon=N` — which mis-aligns when live router and authored config emit services in different orders. `--lenient` masks the most common symptom.
- **RouterOS expression literals.** `admin-mac=[/interface/ethernet get [find name=iac.ether.lan1] mac-address]` is stored as the literal string; the verify simulator can't evaluate it.
- **Singleton-menu upsert.** `apply_patch` doesn't auto-create singleton items (e.g. `/system/identity`) when the live side omits them. The differ emits the `set` correctly; the simulator just needs an explicit upsert path.
- **No `place-before=` for ordered menus.** Always wipe-then-add.
- **No live-router mode.** All diffs are file-vs-file. Capture live state via `/export terse file=live` then drag off the router.

## Tests

```powershell
.\.venv\Scripts\python.exe tests\test_roundtrip.py
```
