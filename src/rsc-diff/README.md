# rsc-diff

Diffs two RouterOS `.rsc` configs (file vs file) into an apply-able patch of `add`/`set`/`reset`/`remove` ops. With `--rollforward`/`--rollback` it also produces the reverse patch and replays both in-memory to assert `apply(old, fwd) == new` and `apply(new, bwd) == old` (subsumes the former `rsc-diff-verify`).

## Install

```powershell
.\build.ps1            # uv sync (depends on rsc-parser sibling path)
```

Python ≥ 3.10.

## CLI

```text
usage: rsc-diff [-h] --old OLD --new NEW [-o OUT]
                [--rollforward ROLLFORWARD] [--rollback ROLLBACK]
                [--check] [--strict] [--lenient]

Diff two RouterOS .rsc configs into a patch. With --rollforward/--rollback
also produces the reverse patch and verifies both legs in-memory.

options:
  -h, --help            show this help message and exit
  --old OLD             path to baseline (current) .rsc
  --new NEW             path to target (desired) .rsc
  -o, --out OUT         where to write the patch in single-patch mode. File
                        path or directory accepted; if omitted (or a dir),
                        the file lands at <dir-or-./out>/<old-stem>-<new-
                        stem>-<yymmdd>-<secs>.rsc.
  --rollforward ROLLFORWARD
                        roundtrip mode: write the OLD->NEW patch here.
                        Requires --rollback.
  --rollback ROLLBACK   roundtrip mode: write the NEW->OLD patch here.
                        Requires --rollforward.
  --check               single-patch mode only: exit 1 if any operations
                        would be emitted (suitable for CI).
  --strict              disable per-menu defaults + computed-property
                        normalisation. Use this for the FIRST diff against
                        an unfamiliar router so any defaults-table
                        miscalibration surfaces as visible drift.
  --lenient             suppress asymmetric drift where one side has an
                        explicit neutral value (no/false/none/0/0s/empty)
                        and the other side is silent. RISK: hides real
                        drift if the actual default is non-neutral.
```

### Roundtrip mode example

```powershell
rsc-diff --old live.rsc --new candidate.rsc `
    --rollforward fwd.rsc --rollback bwd.rsc
# rollforward = fwd.rsc (91 ops)
# rollback    = bwd.rsc (85 ops)
# --- apply(live.rsc, fwd.rsc) == candidate.rsc ---
#   OK -- differ reports no residual drift
# --- apply(candidate.rsc, bwd.rsc) == live.rsc ---
#   OK -- differ reports no residual drift
```

A `DRIFT -- N residual op(s)` report on either leg is the canonical signal that `defaults.py` is missing an entry. Each `defaults.py` entry should cite the e2e test or `/export` evidence that justified it.

## Library

```python
from rsc_diff import parse_file, diff, emit
from rsc_diff.verify import apply_patch, residual_ops

old = parse_file("live.rsc")
new = parse_file("candidate.rsc")
ops = diff(old, new, lenient_defaults=True)
patch_text = emit(ops, header="live -> candidate")

# verify roundtrip
result = apply_patch(old, "rollforward.rsc")
drift = residual_ops(result, new)        # empty list = round-trip clean
```

| Symbol                                                    | Purpose                                          |
| --------------------------------------------------------- | ------------------------------------------------ |
| `parse_file(path)` / `parse_text(s)`                      | `.rsc` → `Config` (re-export from `rsc_parser`)  |
| `diff(old, new, *, strict=False, lenient_defaults=False)` | `Config × Config → list[Op]`                     |
| `emit(ops, *, header=None)`                               | `list[Op] → str`                                 |
| `Config` / `Item` / `Op`                                  | Data model (re-exported from `rsc_parser`)       |
| `verify.apply_patch(cfg, patch_path)`                     | Simulator: replay patch ops on a `Config`        |
| `verify.residual_ops(result, target)`                     | Re-runs `diff()`; empty = semantic equality      |

## Identity model

Each parsed item gets an `identity_key`:

1. `name=iac.x.y` if the menu accepts a `name=` field
2. `comment` containing an `iac.x.y` token (firewall rules, leases, IPv6 lists)
3. `default-name=etherN` for built-in interfaces
4. `@anon=N` positional fallback (unnamed items, no `iac.*` token)
5. menu path itself for singletons

Ordered menus (firewall chains, address-list) use **wipe-then-add**: any drift triggers `remove [find]` + full re-add in declaration order.

| Mode        | Behavior                                                                                                                                                                              |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| default     | Strip identity props, computed props (`network=` derived from `address=`), and known defaults from comparison.                                                                        |
| `--strict`  | Disable both. Surfaces every textual delta. Use for the first diff against an unfamiliar router.                                                                                      |
| `--lenient` | Also suppress drift where one side has explicit `no`/`false`/`none`/`0`/`0s`/`""` and the other side is silent. **Hides real drift if the actual default is non-neutral.** Verify-only — don't generate patches with this. |

## Known issues

- **`/ip/service` positional drift.** Items have no `name=` and no `iac.*` comment, so the differ falls back to `@anon=N` — which mis-aligns when live router and authored config emit services in different orders. `--lenient` masks the most common symptom.
- **RouterOS expression literals.** `admin-mac=[/interface/ethernet get [find name=iac.ether.lan1] mac-address]` is stored as the literal string; the verify simulator can't evaluate it.
- **Singleton-menu upsert.** `apply_patch` doesn't auto-create singleton items (e.g. `/system/identity`) when the live side omits them. The differ emits the `set` correctly; the simulator just needs an explicit upsert path.
- **No `place-before=` for ordered menus.** Always wipe-then-add.
- **No live-router mode.** All diffs are file-vs-file. Capture live state via `/export terse file=live` then drag off the router.
