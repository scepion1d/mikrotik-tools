# test.ps1 -- run every tool's pytest suite, optionally with coverage.
#
# Usage:
#   .\test.ps1                       # run all tools, with coverage, console report
#   .\test.ps1 -Tools rsc-bundle     # one tool only (still produces coverage)
#   .\test.ps1 -Tools rsc-bundle,rsc-diff
#   .\test.ps1 -NoCoverage           # plain pytest, no coverage at all
#   .\test.ps1 -Html                 # also write tools/.coverage-html/
#   .\test.ps1 -FailUnder 80         # exit 2 if total coverage < 80%
#
# Coverage model
# --------------
# Each tool's pyproject.toml has [tool.coverage.run] with `source` and
# `branch` set to declare WHAT to measure. This script sets the
# COVERAGE_FILE env var to a unique per-tool path under
# tools/.coverage-data/ before each pytest run so the tools' data files
# don't overwrite each other (pytest-cov ignores `parallel=true` in the
# config; an env-driven filename is the reliable way). After all tool
# test suites finish, this script runs `coverage combine` + `coverage
# report` from tools/, where .coveragerc defines cross-tool [paths]
# mapping and report formatting.
#
# Why per-tool venvs at all? Each tool is a standalone uv project with
# its own dependency closure (rsc-deploy needs paramiko, rsc-bundle pulls
# rsc-parser as a path dep, etc.). A single shared test venv would
# entangle those. Per-tool data files + a combine step gives us one
# aggregated report without sacrificing per-tool isolation.

[CmdletBinding()]
param(
    [string[]] $Tools = @('rsc-parser','rsc-diff','rsc-bundle','rsc-deploy'),
    [switch]   $NoCoverage,
    [switch]   $Html,
    [int]      $FailUnder = 0
)

$ErrorActionPreference = 'Stop'

# uv writes progress lines to stderr even on success; PS5.1 with strict
# error handling treats those as NativeCommandError. Relax for the duration
# of this script and rely on $LASTEXITCODE for the actual outcome.
$prevPref = $ErrorActionPreference
$ErrorActionPreference = 'Continue'

$root      = $PSScriptRoot
$dataDir   = Join-Path $root '.coverage-data'
$htmlDir   = Join-Path $root '.coverage-html'

# Resolve uv: PATH first, then ~/.local/bin/uv.exe (default uv-installer location).
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
$uv    = if ($uvCmd) { $uvCmd.Source } else { $null }
if (-not $uv) {
    $candidate = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path -LiteralPath $candidate) { $uv = $candidate }
}
if (-not $uv) {
    Write-Host "uv not found. Install from https://docs.astral.sh/uv/ or add it to PATH." -ForegroundColor Red
    exit 2
}

# Wipe stale coverage data so a partial run from last time can't leak in.
if (-not $NoCoverage) {
    if (Test-Path $dataDir) { Remove-Item -Recurse -Force $dataDir }
    New-Item -ItemType Directory -Path $dataDir | Out-Null
    if (Test-Path $htmlDir) { Remove-Item -Recurse -Force $htmlDir }
}

$failed = @()

# Per-tool test runs.
foreach ($t in $Tools) {
    $toolDir = Join-Path $root "src\$t"
    if (-not (Test-Path $toolDir)) {
        Write-Host "==> $t -- NOT FOUND ($toolDir)" -ForegroundColor Yellow
        $failed += $t
        continue
    }

    Write-Host ""
    Write-Host "==> $t" -ForegroundColor Cyan
    Push-Location $toolDir
    try {
        # Skip tools without a tests/ directory (e.g. library-only with no
        # suite yet) instead of failing the whole run.
        if (-not (Test-Path 'tests')) {
            Write-Host "    (no tests/ dir, skipping)" -ForegroundColor DarkGray
            continue
        }

        if ($NoCoverage) {
            & $uv run pytest -q 2>&1 | ForEach-Object { Write-Host $_ }
        } else {
            # Redirect this tool's coverage output to a unique file under
            # the shared dir. pytest-cov picks up COVERAGE_FILE; without
            # this, every tool would clobber tools/.coverage and only the
            # last one's data would survive. The `.<tool>` suffix makes
            # `coverage combine .coverage-data` recognise the file as
            # parallel-style data.
            $env:COVERAGE_FILE = Join-Path $dataDir ".coverage.$t"
            try {
                # --cov with no value enables coverage.py via pytest-cov;
                # the tool's [tool.coverage.run] config picks the package.
                # --cov-report= suppresses pytest-cov's per-tool inline
                # report; one combined report at the end.
                & $uv run pytest -q --cov --cov-report= 2>&1 |
                    ForEach-Object { Write-Host $_ }
            } finally {
                Remove-Item env:COVERAGE_FILE -ErrorAction SilentlyContinue
            }
        }

        if ($LASTEXITCODE -ne 0) { $failed += $t }
    } finally {
        Pop-Location
    }
}

# Combined coverage report.
if (-not $NoCoverage) {
    Write-Host ""
    Write-Host "==> combine + report" -ForegroundColor Cyan
    Push-Location $root
    try {
        # Use rsc-parser's venv to drive coverage -- it's the smallest,
        # always present, and (as a library) has no platform-specific deps.
        # `uv --directory src\rsc-parser` runs coverage with that venv but
        # changes cwd to src\rsc-parser; coverage would then auto-load
        # rsc-parser's pyproject.toml. Pass --rcfile explicitly so the
        # top-level .coveragerc (with [paths] mapping + [html] dir) wins.
        $rcfile = Join-Path $root '.coveragerc'

        & $uv --directory src\rsc-parser run coverage combine `
            "--rcfile=$rcfile" $dataDir 2>&1 |
            ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "coverage combine failed (no data?)" -ForegroundColor Yellow
        }

        $reportArgs = @('coverage','report',"--rcfile=$rcfile")
        if ($FailUnder -gt 0) { $reportArgs += "--fail-under=$FailUnder" }
        & $uv --directory src\rsc-parser run @reportArgs 2>&1 |
            ForEach-Object { Write-Host $_ }
        $reportExit = $LASTEXITCODE

        if ($Html) {
            & $uv --directory src\rsc-parser run coverage html `
                "--rcfile=$rcfile" 2>&1 |
                ForEach-Object { Write-Host $_ }
            Write-Host ""
            Write-Host "HTML report: $htmlDir\index.html"
        }
    } finally {
        Pop-Location
    }
}

# Restore prior preference (cosmetic for interactive sessions).
$ErrorActionPreference = $prevPref

# Final exit code: prefer test failures, then coverage threshold failure.
if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "FAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
if (-not $NoCoverage -and $FailUnder -gt 0 -and $reportExit -ne 0) {
    Write-Host ""
    Write-Host "Coverage below threshold ($FailUnder%)" -ForegroundColor Red
    exit 2
}

Write-Host ""
Write-Host "All tests passed." -ForegroundColor Green
