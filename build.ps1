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

    $scripts = Join-Path $tool.FullName '.venv\Scripts'

    # Source of truth for what to expose: [project.scripts] in pyproject.toml,
    # which setuptools materializes into *.egg-info/entry_points.txt as the
    # [console_scripts] section. Link every one of them, not just <tool>.exe
    # (e.g. rsc-diff also ships rsc-diff-verify).
    $names = @()
    $epFiles = Get-ChildItem -Path $tool.FullName -Filter 'entry_points.txt' -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*\*.egg-info\entry_points.txt' }
    foreach ($ep in $epFiles) {
        $inSection = $false
        foreach ($line in Get-Content -LiteralPath $ep.FullName) {
            $t = $line.Trim()
            if ($t -match '^\[(.+)\]$') { $inSection = ($Matches[1] -eq 'console_scripts'); continue }
            if (-not $inSection) { continue }
            if ($t -eq '' -or $t.StartsWith('#')) { continue }
            $name = ($t -split '=', 2)[0].Trim()
            if ($name) { $names += $name }
        }
    }
    $names = $names | Sort-Object -Unique
    if (-not $names) {
        # Fallback: legacy convention of one script named after the tool.
        $names = @($tool.Name)
    }

    foreach ($name in $names) {
        $exe = Join-Path $scripts "$name.exe"
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
}

Write-Host ''
Write-Host 'done.' -ForegroundColor Green
