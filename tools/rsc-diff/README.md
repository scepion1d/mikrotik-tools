# rsc-diff

Lightweight RouterOS `.rsc` diff library + CLI. Compares two config files and
emits a minimal set of `add` / `set` / `remove` operations to transform the
**old** config into the **new** one.

Status: **MVP** — parser + naive differ + CLI + library API. No normalisation,
no ordering-aware moves, no live router mode yet. See [`ROADMAP.md`](ROADMAP.md).

## Install

Zero runtime dependencies; Python ≥ 3.10. Install from this directory:

```powershell
# with uv (recommended -- managed venv at .venv/)
uv sync

# or with pip
pip install -e .
```

## CLI usage

```powershell
# diff to stdout
uv run rsc-diff old.rsc new.rsc

# write a patch file
uv run rsc-diff old.rsc new.rsc -o patch.rsc

# CI mode -- exit 1 if any drift
uv run rsc-diff old.rsc new.rsc --check
```

## Library usage

```python
from pathlib import Path

from rsc_diff import Config, Op, diff, emit, parse_file, parse_text

# 1. Parse from disk or from a string
old: Config = parse_file("baseline.rsc")
new: Config = parse_text(Path("desired.rsc").read_text())

# 2. Compute the operation list
ops: list[Op] = diff(old, new)
print(f"{len(ops)} change(s)")

# 3. Inspect ops programmatically...
for op in ops:
    print(op.kind, op.menu, op.identity_key, op.props)

# 4. ...or render as a runnable RouterOS patch
patch_text: str = emit(ops, header="from baseline.rsc to desired.rsc")
Path("patch.rsc").write_text(patch_text)
```

### Public API

| Symbol | Purpose |
|---|---|
| `parse_file(path)` | Read `.rsc` from disk → `Config` |
| `parse_text(s)` | Parse `.rsc` string → `Config` |
| `diff(old, new)` | `Config × Config → list[Op]` |
| `emit(ops, *, header=None)` | `list[Op] → str` (runnable patch) |
| `Config` | `{menu_path: [Item]}` container |
| `Item` | One parsed config item |
| `Op` | One operation (`kind`, `menu`, `identity_key`, `props`) |
| `__version__` | Package version string |

The package ships `py.typed` so type checkers (mypy, pyright) see annotations.

## Layout

```
tools/rsc-diff/
├── README.md                  this file
├── ROADMAP.md                 staged plan (MVP -> normalisation -> live)
├── pyproject.toml             python packaging (no runtime deps)
├── rsc_diff/
│   ├── __init__.py            public API + __version__
│   ├── __main__.py            python -m rsc_diff entry point
│   ├── cli.py                 argument parsing + orchestration
│   ├── model.py               Item / Config / Op + identity_key resolution
│   ├── parser.py              .rsc -> Config (line-based; \\, "...", [find])
│   ├── differ.py              Config x Config -> [Op]
│   ├── emitter.py             [Op] -> .rsc text
│   └── py.typed               PEP 561 typing marker
└── tests/
    ├── fixtures/{empty,minimal_a,minimal_b}.rsc
    └── test_roundtrip.py
```

## Identity model

Each parsed item gets an `identity_key` derived in this order:

1. `name=iac.x.y` if the menu accepts a `name` field
2. `comment` containing `iac.x.y` if not (firewall rules, leases, ipv6 lists)
3. `default-name=etherN` for built-ins (set-only menus)
4. Position fallback (ordered menus without iac tags)
5. Menu path itself for singletons (`/system/clock`, `/ip/dns`, …)

This matches the convention enforced by `rsc/main.rsc`.
## Testing

```powershell
uv run python tests\test_roundtrip.py
```

## Caveats (MVP)

- No property normalisation -- `wpa2-psk,wpa3-psk` vs `wpa3-psk,wpa2-psk` will
  show as a diff even though RouterOS treats them as equal.
- Ordered menus emit `add` at end, no `place-before=` yet.
- Variable references (`$adminPass`) compared as literal strings.
- Helper / `:global` / `:if` lines from apply.rsc-style scripts are ignored;
  this tool diffs CONFIG, not orchestration.
- Property removals (key in old, not in new) are not emitted (RouterOS unset
  semantics differ per menu).
