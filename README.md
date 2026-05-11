# mikrotik-tools

Python CLIs + libraries for working with RouterOS `.rsc` configs: bundle a profile folder into one minimal file, diff two configs into an apply-able patch, control the router (upload, download, backup) over SSH/SFTP. All tools share a single parser/identity model.

## Tools

| Tool                                  | One-liner                                                                                                |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| [`rsc-parser`](src/rsc-parser/)       | **Library.** Parse `.rsc` → indexed `Config`, resolve stable `iac.*` ids (with synthetic fallback).      |
| [`rsc-bundle`](src/rsc-bundle/)       | **CLI + lib.** Bundle a flat `rsc/<profile>/` folder into one minimal deploy-ready `.rsc`.               |
| [`rsc-diff`](src/rsc-diff/)           | **CLI + lib.** Diff two `.rsc` files into a patch; e2e roundtrip mode emits + verifies fwd/back patches. |
| [`rsc-ctl`](src/rsc-ctl/)             | **CLI + lib.** SSH/SFTP control plane for RouterOS: upload, download, trigger router-side backup.       |

## Layout

```
tools/
├── src/
│   ├── rsc-parser/          shared library
│   ├── rsc-bundle/          depends on rsc-parser
│   ├── rsc-diff/            depends on rsc-parser
│   └── rsc-ctl/             standalone (paramiko)
├── bin/                     gitignored: tool shims (.cmd / symlink)
└── build.ps1                sync all tools, refresh shims in bin/
```

## Dependencies

- **Python ≥ 3.10**
- **[uv](https://docs.astral.sh/uv/)** for venv + dependency management (one venv per tool under `src/<tool>/.venv/`)
- Runtime:
  - `rsc-parser` / `rsc-bundle` / `rsc-diff` — zero external deps
  - `rsc-ctl` — `paramiko`
- Dev: `pytest` (per-tool dev dependency group)

The repo-root `build.ps1` runs each tool's `build.ps1` (which calls `uv sync`), then symlinks (or `.cmd`-shims, when symlink privilege is missing) each console script into `tools/bin/`.

## Install

```powershell
.\build.ps1            # syncs all tools, refreshes bin\
.\build.ps1            # idempotent; re-run after `git pull`
```

Per-tool reinstall:

```powershell
cd src\rsc-bundle
.\build.ps1            # uv sync this tool only
.\build.ps1 -Clean     # nuke .venv first
```

## Usage

End-to-end pipeline:

```powershell
# 1. bundle a profile -> single self-contained .rsc
.\bin\rsc-bundle.cmd --profile ..\rsc\segmented -o ..\out
# -> ..\out\segmented-YYMMDD-XXXXX.rsc

# 2. capture live router state
#    (in Winbox terminal): /export terse file=live
#    drag /file/live.rsc off the router into ..\out\live.rsc

# 3. emit + verify both patches in one go
$candidate = (Get-ChildItem ..\out\segmented-*.rsc | Sort Name | Select -Last 1).FullName
.\bin\rsc-diff.cmd --old ..\out\live.rsc --new $candidate `
    --rollforward ..\out\rollforward.rsc `
    --rollback   ..\out\rollback.rsc `
    --lenient
# Both legs must report "OK -- differ reports no residual drift" before proceeding.

# 4. apply on router
#    Upload the patch to flash, then import it from the router console:
.\bin\rsc-ctl.cmd upload --src ..\out\rollforward.rsc --dst rollforward.rsc
#    (in Winbox / SSH terminal): /import file-name=rollforward.rsc
```

Per-tool details, full `--help` output, and known issues live in each tool's README.

## Tests + coverage

Run all four tool test suites with combined coverage from `tools/`:

```powershell
.\test.ps1                      # all tools, combined coverage report
.\test.ps1 -Tools rsc-bundle    # one tool only (still produces coverage)
.\test.ps1 -NoCoverage          # skip the coverage step
.\test.ps1 -Html                # also write tools\.coverage-html\
.\test.ps1 -FailUnder 80        # exit 2 if total coverage < 80%
```

`test.ps1` runs each tool's `pytest` in its own venv (so dependency isolation is preserved) and points each run's `COVERAGE_FILE` at a unique file under `tools/.coverage-data/`. After all suites finish, it runs `coverage combine` + `coverage report` against the central `.coveragerc`. The HTML report (when `-Html`) lands in `tools/.coverage-html/`.

Per-tool coverage measurement is configured in each `pyproject.toml` under `[tool.coverage.run]` (just the `source =` package name and `branch = true`); the central `.coveragerc` controls report formatting and the HTML output dir.

Single-tool, ad-hoc:

```powershell
cd src\rsc-bundle
uv run pytest -q                # no coverage
uv run pytest -q --cov          # tool-local coverage report
```

## Status

| Tool         | State                                                                                                  |
| ------------ | ------------------------------------------------------------------------------------------------------ |
| `rsc-parser` | ✅ stable                                                                                              |
| `rsc-bundle` | ✅ stable                                                                                              |
| `rsc-diff`   | ✅ stable; roundtrip mode catches missing `defaults.py` entries automatically                          |
| `rsc-ctl`    | ✅ stable; SFTP upload/download + `/system/backup save` + `/export show-sensitive` working end-to-end. |
