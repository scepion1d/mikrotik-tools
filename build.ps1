# build.ps1 -- run every src/*/build.ps1, then link console scripts into bin/.

$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$bin  = Join-Path $root 'bin'
New-Item -ItemType Directory -Path $bin -Force | Out-Null

foreach ($tool in Get-ChildItem -Directory (Join-Path $root 'src')) {
    $build = Join-Path $tool.FullName 'build.ps1'
    if (-not (Test-Path -LiteralPath $build)) { continue }

    Write-Host "==> $($tool.Name)" -ForegroundColor Cyan
    & $build

    # Library-only packages (e.g. rsc-parser) declare no [project.scripts]
    # and intentionally produce no console exe. Detect that and skip the
    # bin-linking step; otherwise the missing .exe would print a warning.
    $pyproject = Join-Path $tool.FullName 'pyproject.toml'
    if (Test-Path -LiteralPath $pyproject) {
        $hasScripts = Select-String -LiteralPath $pyproject `
            -Pattern '^\s*\[project\.scripts\]' -Quiet
        if (-not $hasScripts) {
            Write-Host "    (library-only, no console script)"
            continue
        }
    }

    $scripts = Join-Path $tool.FullName '.venv\Scripts'

    # Convention: one console script per tool, named "<tool>.exe", produced
    # by uv sync from [project.scripts] in pyproject.toml.
    $name = $tool.Name
    $exe  = Join-Path $scripts "$name.exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        Write-Warning "no $name.exe in $scripts"
        continue
    }

    # Wipe any stale shim/symlink for this name (.exe and .cmd) before relinking.
    foreach ($ext in '.exe', '.cmd') {
        $stale = Join-Path $bin "$name$ext"
        if (Test-Path -LiteralPath $stale) { Remove-Item -LiteralPath $stale -Force }
    }

    $link = Join-Path $bin "$name.exe"
    try {
        New-Item -ItemType SymbolicLink -Path $link -Target $exe | Out-Null
        Write-Host "    bin\$name.exe -> symlink"
    } catch {
        # No SeCreateSymbolicLink (need admin or Developer Mode) -- fall back
        # to a tiny .cmd shim that forwards args.
        $cmd = [IO.Path]::ChangeExtension($link, '.cmd')
        Set-Content -LiteralPath $cmd -Value "@`"$exe`" %*" -Encoding ASCII
        Write-Host "    bin\$name.cmd -> shim (no symlink privilege)"
    }
}

Write-Host ''
Write-Host 'done.' -ForegroundColor Green
