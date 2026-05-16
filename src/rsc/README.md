# rsc

Python toolkit for RouterOS `.rsc` script processing. One CLI with four
subcommands plus a shared parser library.

```text
rsc bundle  --profile <folder> [--yaml] [...]  # merge profile -> single minimal .rsc
rsc diff    --old A.rsc --new B.rsc            # diff two configs into a runnable patch
rsc reverse --src live.rsc -o src/profile/     # convert .rsc back to YAML profile sources
rsc lint    --src live.rsc | --profile <folder> [--yaml]   # semantic check for duplicate ids / dangling refs
```

The optional `--yaml` flag renders YAML folders to `.rsc` via the
[`rsc.yaml`](rsc/yaml/) subpackage before bundling; output is
byte-equivalent to `.rsc` mode.

## Install

```powershell
.\build.ps1            # uv sync (pyyaml is the only runtime dep)
```

Python ≥ 3.10.

## CLI

### `rsc bundle`

Merge a flat profile folder (`NN-*.rsc`) plus a vars folder of
`:global` `.rsc` files into one minimal `/export`-style `.rsc`.
Resolves `:global` variable substitution and strips RouterOS scripting
wrappers.

```powershell
rsc bundle --profile rsc\segmented                       # vars dir auto-discovered (= rsc\)
rsc bundle --profile rsc\segmented -o builds\            # auto-named under builds\
rsc bundle --profile rsc\segmented -o my-bundle.rsc      # explicit file path
rsc bundle --profile rsc\segmented --vars rsc\           # explicit vars folder
rsc bundle --profile rsc\segmented --no-flatten          # raw concat, skip flatten/parse pipeline
rsc bundle --profile src\segmented --yaml                # YAML sources (.yaml) under src\
rsc bundle --profile src\segmented --validate            # validate vs schema.json before render (implies --yaml)
rsc bundle --profile src\segmented --validate path\to\schema.json   # explicit schema path
```

`--vars` defaults to `<profile-parent>`. Every `*.rsc` (or, with
`--yaml`, `*.yaml`) at the top level of that folder is loaded
alphabetically and prepended to the bundle. If the folder is empty
(or has no matching files), the profile still bundles -- just without
any `:global` substitution.

`--yaml` switches loader mode: the profile and vars folders are
globbed for `*.yaml`, each file is rendered to `.rsc` text via
[`rsc.yaml`](rsc/yaml/), and the rendered text feeds the same
flatten + parse + compact pipeline as the `.rsc` path. The output is
byte-equivalent to the `.rsc` mode for a correctly authored YAML
profile (verifiable with `rsc diff --check`).

`--validate` runs a JSON Schema check over every loaded YAML *before*
rendering. Default schema path is `<vars-dir>/schema.json` (so in this
repo: `src/schema.json`); pass an explicit path to override
(`--validate path/to/other-schema.json`). Implies `--yaml`. Errors are
reported with the file path, JSON-pointer-like key path, and the
source line number; the renderer aborts with exit 2 if any file
fails validation, so no partially-good bundle leaks to `-o`.

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

### `rsc reverse`

The inverse of `rsc bundle --yaml`: parse a `.rsc` file (typically
`mtctl backup`'s `live.rsc`) and emit YAML profile sources under
`src/<profile>/`. Bootstraps a fresh profile from an existing router.

```powershell
rsc reverse --src live.rsc -o src\myrouter\          # writes 10-interface.yaml, 30-ip.yaml, ...
rsc reverse --src live.rsc -o src\myrouter\ --overwrite   # replace any existing files
```

Output is one `NN-<top-menu>.yaml` per top-level RouterOS menu (same
`NN-` numbering as the hand-authored profiles). Items keep their
`iac.*` identity tokens; `set` rows preserve their `[find ...]`
selector as `filter:`; multi-space column padding around `--` in
comments is preserved via `id_pad`. Round-trip through
`rsc bundle --yaml` is byte-equivalent to the input (verifiable with
`rsc diff --check`).

Workflow:
```powershell
mtctl backup                            # router-side .backup + live.rsc
mtctl download live.rsc -o live.rsc     # pull it down
rsc reverse --src live.rsc -o src\myrouter\
git add src\myrouter\
```

### `rsc lint`

Semantic checks against a parsed config. Catches the structural problems
that make `/import` blow up at runtime: duplicate `iac.*` ids, dangling
cross-references, orphan DHCP-pool wiring.

```powershell
rsc lint --src live.rsc                                # lint a single .rsc file
rsc lint --profile src\segmentedx3 --yaml              # bundle YAML, then lint
rsc lint --profile rsc\basic                          # bundle .rsc, then lint
rsc lint --profile src\segmentedx3 --yaml -v          # also print clean-result line
```

Exit codes:

- `0` -- no issues, or only warnings.
- `1` -- at least one error-severity issue.
- `2` -- setup error (bad path, bundle failure, empty config).

Checks today:

| Code      | Severity | What it catches                                                          |
| --------- | -------- | ------------------------------------------------------------------------ |
| `LINT001` | error    | Two items in the same menu sharing one `iac.*` token                     |
| `LINT002` | error    | A prop like `interface=iac.bridge.foo` where no `iac.bridge.foo` exists  |
| `LINT005` | error    | `/ip/dhcp-server address-pool=iac.pool.X` where no matching `/ip/pool` defined |

Sample output for a broken profile:

```
rsc lint: 3 issue(s) -- 3 error(s), 0 warning(s)
  LINT001 error: /interface/list[1] (iac.list.wan): duplicate id 'iac.list.wan' (appears at positions [0, 1] in this menu)
  LINT002 error: /ip/address[0] (iac.addr.lan): property interface='iac.bridge.missing' references an iac.* name not defined in any of /interface/ethernet, /interface/vlan, /interface/bridge, /interface/wifi
  LINT005 error: /ip/dhcp-server[0] (iac.dhcp.lan): address-pool='iac.pool.gone' doesn't match any /ip/pool entry; the DHCP server will start but lease nothing
```

Deferred (need pre-flatten text; not yet wired): `LINT003` (unused
`:global`), `LINT004` (undefined `$varname` ref).

## Library

```python
from rsc import parse_file, diff
from rsc.diff import emit
from rsc.bundle import bundle
from rsc.yaml import to_rsc, to_rsc_file

# Parser
cfg = parse_file("baseline.rsc")

# Diff
ops = diff(parse_file("a.rsc"), parse_file("b.rsc"))
print(emit(ops))

# Bundle
text = bundle("rsc/segmented", vars_dir="rsc/")
text = bundle("src/segmented", vars_dir="src/", yaml=True)  # YAML mode

# YAML -> .rsc (used internally by bundle(..., yaml=True), but exposed
# for ad-hoc rendering of a single file).
rsc_text = to_rsc_file("src/segmented/40-firewall.yaml")

# .rsc -> YAML (the inverse; used by `rsc reverse`).
from rsc.yaml import to_yaml_files
written = to_yaml_files(parse_file("live.rsc"), "src/new_profile/")

# Lint a parsed config.
from rsc.lint import lint, format_issues, Severity
issues = lint(parse_file("live.rsc"))
print(format_issues(issues))
has_error = any(i.severity is Severity.ERROR for i in issues)
```

## Architecture

Four subpackages under one umbrella, each independently importable:

| Subpackage     | Role                                                                  |
| -------------- | --------------------------------------------------------------------- |
| `rsc.parser`   | Lexes `.rsc` into `Config` / `Item` / `Op`. Resolves `iac.<type>.<id>` identity. **Library only.** |
| `rsc.bundle`   | Profile-folder loader + flattener + compact emitter. CLI: `rsc bundle`. |
| `rsc.diff`     | Per-menu differ + patch emitter + in-memory verifier. CLI: `rsc diff`. |
| `rsc.yaml`     | YAML → `.rsc` renderer + `.rsc` → YAML reverser + JSON Schema validator. CLIs: `rsc bundle --yaml`, `rsc bundle --validate`, `rsc reverse`. |
| `rsc.lint`     | Semantic checks on a parsed config (duplicate ids, dangling refs, orphan pool refs). CLI: `rsc lint`. |

The top-level `rsc.cli.main` is a thin dispatcher:

```python
rsc bundle  ...  ->   rsc.bundle.cli.main(<rest>)
rsc diff    ...  ->   rsc.diff.cli.main(<rest>)
rsc reverse ...  ->   rsc.yaml.reverse_cli.main(<rest>)
rsc lint    ...  ->   rsc.lint_cli.main(<rest>)
```

Each sub-CLI keeps its own argparser and `--help`. No single merged
argparser at the top, so per-subcommand help stays clean.

## Tests

```powershell
uv run pytest -q                # all three subpackage suites
uv run pytest -q tests/parser   # one subpackage only
uv run pytest -q --cov          # coverage report
```

Tests live under `tests/{parser,bundle,diff,yaml_subpkg}/` and import
their subpackage by full dotted name (`from rsc.bundle import ...`
etc). The `yaml_subpkg/` directory holds the `rsc.yaml` tests — the
odd suffix avoids shadowing the installed `pyyaml` package when
pytest puts `tests/` on `sys.path`.

## Running router-side actions

This tool is read-only / offline-only. To push the produced `.rsc` to a
device or trigger a backup, see [`mtctl`](../mtctl/) (the SSH/SFTP
control plane).
