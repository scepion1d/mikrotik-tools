# rsc

Python toolkit for RouterOS `.rsc` script processing. One CLI with two
subcommands plus a shared parser library.

```text
rsc bundle <profile-folder> [...]    # merge profile -> single minimal .rsc
rsc diff   --old A.rsc --new B.rsc   # diff two configs into a runnable patch
```

## Install

```powershell
.\build.ps1            # uv sync (zero external runtime deps)
```

Python ≥ 3.10.

## CLI

### `rsc bundle`

Merge a flat profile folder (`secrets.rsc`, `vars.rsc`, `NN-*.rsc`) into
one minimal `/export`-style `.rsc`. Resolves `:global` variable
substitution and strips RouterOS scripting wrappers.

```powershell
rsc bundle rsc\segmented                       # -> .\out\segmented-YYMMDD-XXXXX.rsc
rsc bundle rsc\segmented -o builds\            # -> builds\segmented-YYMMDD-XXXXX.rsc
rsc bundle rsc\segmented -o my-bundle.rsc      # -> my-bundle.rsc
rsc bundle rsc\segmented --keep-comments
rsc bundle rsc\segmented --no-flatten          # raw concat, skip flatten/parse pipeline
```

### `rsc diff`

Diff two `.rsc` configs into a minimal patch (`add` / `set` / `reset` /
`remove` ops). Two modes:

```powershell
# Single-patch (default)
rsc diff --old live.rsc --new candidate.rsc -o patch.rsc
rsc diff --old live.rsc --new candidate.rsc --check          # exit 1 on drift

# Roundtrip: emit + verify both legs in-memory
rsc diff --old live.rsc --new candidate.rsc \
         --rollforward fwd.rsc --rollback bwd.rsc
# Asserts: apply(live, fwd) == candidate AND apply(candidate, bwd) == live
```

`--lenient` suppresses asymmetric "neutral" defaults drift; `--strict`
disables the per-menu defaults table entirely.

## Library

```python
from rsc import parse_file, diff
from rsc.diff import emit
from rsc.bundle import bundle

# Parser
cfg = parse_file("baseline.rsc")

# Diff
ops = diff(parse_file("a.rsc"), parse_file("b.rsc"))
print(emit(ops))

# Bundle
text = bundle("rsc/segmented")  # default pipeline: load + flatten + compact
```

## Architecture

Three subpackages under one umbrella, each independently importable:

| Subpackage     | Role                                                                  |
| -------------- | --------------------------------------------------------------------- |
| `rsc.parser`   | Lexes `.rsc` into `Config` / `Item` / `Op`. Resolves `iac.<type>.<id>` identity. **Library only.** |
| `rsc.bundle`   | Profile-folder loader + flattener + compact emitter. CLI: `rsc bundle`. |
| `rsc.diff`     | Per-menu differ + patch emitter + in-memory verifier. CLI: `rsc diff`. |

The top-level `rsc.cli.main` is a thin dispatcher:

```python
rsc bundle ...   ->   rsc.bundle.cli.main(<rest>)
rsc diff   ...   ->   rsc.diff.cli.main(<rest>)
```

Each sub-CLI keeps its own argparser and `--help`. No single merged
argparser at the top, so per-subcommand help stays clean.

## Tests

```powershell
uv run pytest -q                # all three subpackage suites
uv run pytest -q tests/parser   # one subpackage only
uv run pytest -q --cov          # coverage report
```

Tests live under `tests/{parser,bundle,diff}/` and import their
subpackage by full dotted name (`from rsc.bundle import ...` etc).

## Running router-side actions

This tool is read-only / offline-only. To push the produced `.rsc` to a
device or trigger a backup, see [`mtctl`](../mtctl/) (the SSH/SFTP
control plane).
