# build.ps1 -- install / refresh this tool in-place via uv.
#
# Usage:
#   .\build.ps1            # uv sync
#   .\build.ps1 -Clean     # remove .venv first, then uv sync

[CmdletBinding()]
param(
    [switch] $Clean
)

$ErrorActionPreference = 'Stop'

$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
$uv = if ($uvCmd) { $uvCmd.Source } else { $null }
if (-not $uv) {
    $candidate = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path -LiteralPath $candidate) { $uv = $candidate }
}
if (-not $uv) {
    throw "uv not found. Install from https://docs.astral.sh/uv/ or add it to PATH."
}

Push-Location -LiteralPath $PSScriptRoot
try {
    if ($Clean -and (Test-Path .venv)) {
        Write-Host "cleaning .venv"
        Remove-Item -Recurse -Force .venv
    }
    Write-Host "uv sync ($PSScriptRoot)"
    # uv writes progress lines to stderr even on success; Windows PowerShell
    # 5.1 treats those as NativeCommandError under -ErrorAction Stop. Relax
    # for just this call and rely on $LASTEXITCODE for the actual outcome.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $uv sync 2>&1 | ForEach-Object { Write-Host $_ } }
    finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "uv sync exited $LASTEXITCODE" }
} finally {
    Pop-Location
}
