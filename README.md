# mikrotik-tools

[![test](https://github.com/scepion1d/mikrotik-tools/actions/workflows/test.yml/badge.svg)](https://github.com/scepion1d/mikrotik-tools/actions/workflows/test.yml)

Two Python tools for working with a MikroTik RouterOS device:

| Tool                           | Role                                                                       |
| ------------------------------ | -------------------------------------------------------------------------- |
| [`rsc`](src/rsc/)              | **Script processing.** Parser library + `bundle`, `diff`, `reverse`, `lint` subcommands. |
| [`mtctl`](src/mtctl/)          | **Router control.** SSH/SFTP: upload, download, backup, export, import (`--validate` / `--safe-mode`), rm. |

## Layout

```
tools/
├── src/
│   ├── rsc/                 one venv, one CLI ('rsc'), five subpackages
│   │   └── rsc/
│   │       ├── parser/      shared parser + identity model (library)
│   │       ├── bundle/      'rsc bundle' subcommand (--yaml, --validate, --no-flatten)
│   │       ├── diff/        'rsc diff' subcommand (single-patch + roundtrip modes)
│   │       ├── yaml/        YAML ↔ .rsc converter + JSON Schema validator
│   │       │                 (powers 'rsc bundle --yaml', 'bundle --validate', 'rsc reverse')
│   │       └── lint/        'rsc lint' semantic checks (also dispatched via rsc.lint_cli)
│   └── mtctl/               standalone (paramiko); six subcommands
├── bin/                     gitignored: tool shims (.cmd / symlink)
└── build.ps1                sync all tools, refresh shims in bin/
```

## Dependencies

- **Python ≥ 3.10**
- **[uv](https://docs.astral.sh/uv/)** for venv + dependency management (one venv per tool under `src/<tool>/.venv/`)
- Runtime:
  - `rsc` — `pyyaml` (used by the `rsc.yaml` subpackage behind `rsc bundle --yaml`); the parser, bundler, and differ themselves stay dep-free
  - `mtctl` — `paramiko`
- Dev: `pytest` (per-tool dev dependency group)

The repo-root `build.ps1` runs each tool's `build.ps1` (which calls `uv sync`), then symlinks (or `.cmd`-shims, when symlink privilege is missing) each console script into `tools/bin/`.

## Install

```powershell
.\build.ps1            # syncs all tools, refreshes bin\
.\build.ps1            # idempotent; re-run after `git pull`
```

Per-tool reinstall:

```powershell
cd src\rsc
.\build.ps1            # uv sync this tool only
.\build.ps1 -Clean     # nuke .venv first
```

## Usage

End-to-end pipeline:

```powershell
# 1. bundle a profile -> single self-contained .rsc
#    Globals at <profile-parent>/{secrets,vars}.yaml are auto-discovered.
#    `--validate` checks the YAML against src/schema.json before render.
.\bin\rsc.cmd bundle --profile ..\src\segmentedx3 --yaml --validate -o ..\out
# -> ..\out\segmentedx3-YYMMDD-XXXXX.rsc

# 1a. (optional) semantic lint of the bundled config: duplicate ids,
#     dangling cross-refs, orphan DHCP pool wiring.
.\bin\rsc.cmd lint --profile ..\src\segmentedx3 --yaml

# 2. capture live router state. Two options:
#    a) `backup` -- snapshot to router flash (recoverable .backup pair).
.\bin\mtctl.cmd backup --no-encrypt
#       -> backups/<timestamp>/{live.backup, live.rsc} on the router
.\bin\mtctl.cmd download --src backups/<timestamp>/live.rsc --dst ..\out\live.rsc
#    b) `export` -- stream /export over SSH stdout; nothing on router flash.
#       Lighter; preferred for cron / drift checks.
.\bin\mtctl.cmd export --dst ..\out\live.rsc

# 3. emit + verify both patches in one go
$candidate = (Get-ChildItem ..\out\segmentedx3-*.rsc | Sort Name | Select -Last 1).FullName
.\bin\rsc.cmd diff --old ..\out\live.rsc --new $candidate `
    --rollforward ..\out\rollforward.rsc `
    --rollback   ..\out\rollback.rsc `
    --lenient
# Both legs must report "OK -- differ reports no residual drift" before proceeding.

# 4. apply on router. Default is `/import` (you re-run if it fails);
#    add `--safe-mode` to wrap in RouterOS /safe-mode so failures or
#    session drops auto-revert via the 9-minute timer.
.\bin\mtctl.cmd upload --src ..\out\rollforward.rsc --dst rollforward.rsc
.\bin\mtctl.cmd import --src rollforward.rsc --safe-mode -v

# Inverse direction: turn a live router into YAML profile sources.
.\bin\rsc.cmd reverse --src ..\out\live.rsc -o ..\src\myrouter\
```

Per-tool details, full `--help` output, and known issues live in each tool's README.

## Tests + coverage

Run both tool test suites with combined coverage from `tools/`:

```powershell
.\test.ps1                  # both tools, combined coverage report
.\test.ps1 -Tools rsc       # one tool only (still produces coverage)
.\test.ps1 -NoCoverage      # skip the coverage step
.\test.ps1 -Html            # also write tools\.coverage-html\
.\test.ps1 -FailUnder 80    # exit 2 if total coverage < 80%
```

`test.ps1` runs each tool's `pytest` in its own venv (so dependency isolation is preserved) and points each run's `COVERAGE_FILE` at a unique file under `tools/.coverage-data/`. After all suites finish, it runs `coverage combine` + `coverage report` against the central `.coveragerc`. The HTML report (when `-Html`) lands in `tools/.coverage-html/`.

Per-tool coverage measurement is configured in each `pyproject.toml` under `[tool.coverage.run]` (just the `source =` package name and `branch = true`); the central `.coveragerc` controls report formatting and the HTML output dir.

Single-tool, ad-hoc:

```powershell
cd src\rsc
uv run pytest -q                # no coverage
uv run pytest -q --cov          # tool-local coverage report
```

## Status

| Tool   | State                                                                       |
| ------ | --------------------------------------------------------------------------- |
| `rsc`  | ✅ stable: bundle, diff, reverse, lint. See [README](src/rsc/README.md).    |
| `mtctl`| ✅ stable: upload, download, backup, export, import, rm. See [README](src/mtctl/README.md). |
