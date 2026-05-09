# build.ps1 -- run every tools/*/build.ps1, then link console scripts into bin/.

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$bin  = Join-Path $root 'bin'
New-Item -ItemType Directory -Path $bin -Force | Out-Null

foreach ($tool in Get-ChildItem -Directory (Join-Path $root 'tools')) {
    $build = Join-Path $tool.FullName 'build.ps1'
    if (-not (Test-Path -LiteralPath $build)) { continue }

    Write-Host "==> $($tool.Name)" -ForegroundColor Cyan
    & $build

    $scripts = Join-Path $tool.FullName '.venv\Scripts'
    # rsc-diff/parser.py is internal; the convention is one console script
    # per tool, named "<tool>.exe", produced by uv sync from [project.scripts].
    $exe = Join-Path $scripts "$($tool.Name).exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        Write-Warning "no $($tool.Name).exe in $scripts"
        continue
    }

    $link = Join-Path $bin "$($tool.Name).exe"
    if (Test-Path -LiteralPath $link) { Remove-Item -LiteralPath $link -Force }

    try {
        New-Item -ItemType SymbolicLink -Path $link -Target $exe | Out-Null
        Write-Host "    bin\$($tool.Name).exe -> symlink"
    } catch {
        # No SeCreateSymbolicLink (need admin or Developer Mode) -- fall back
        # to a tiny .cmd shim that forwards args.
        $cmd = [IO.Path]::ChangeExtension($link, '.cmd')
        Set-Content -LiteralPath $cmd -Value "@`"$exe`" %*" -Encoding ASCII
        Write-Host "    bin\$($tool.Name).cmd -> shim (no symlink privilege)"
    }
}

Write-Host ''
Write-Host 'done.' -ForegroundColor Green
