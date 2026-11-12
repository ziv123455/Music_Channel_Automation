$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tools = Join-Path $root 'tools'
$desktop = [Environment]::GetFolderPath('Desktop')
New-Item -ItemType Directory -Path $tools -Force | Out-Null

function Port-Used([int]$Port) {
    $tcp = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $tcp.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne(800)) { return $false }
        $tcp.EndConnect($ar)
        return $true
    } catch { return $false } finally { $tcp.Close() }
}
if ((Port-Used 8001) -or (Port-Used 7860) -or (Port-Used 5050)) {
    throw 'Stop Music Channel Automation, ACE-Step and Forge before physically moving files. Close their terminal windows first.'
}
Write-Host 'MOVE INSTALLED AI FOLDERS INTO THE PROJECT' -ForegroundColor Cyan
Write-Host 'This MOVES real folders, it does not create shortcuts.'
Write-Host 'Ollama remains a separately installed Windows application.'
Write-Host ''
$items = @(
    @{Name='ACE-Step-1.5'; Sources=@((Join-Path $desktop 'ACE-Step-1.5')); Marker='start_api_server.bat'},
    @{Name='stable-diffusion-webui-forge'; Sources=@((Join-Path $desktop 'stable-diffusion-webui-forge'),(Join-Path $desktop 'webui_forge')); Marker='run.bat'}
)
foreach ($item in $items) {
    $dest = Join-Path $tools $item.Name
    if (Test-Path -LiteralPath $dest) {
        $entry = Get-Item -LiteralPath $dest -Force
        if ($entry.LinkType -eq 'Junction') {
            # setup-workspace.bat previously made a shortcut; convert it only after confirmation.
            $target = [string]$entry.Target
            $expected = @($item.Sources | Where-Object { Test-Path -LiteralPath $_ })
            $match = $false
            foreach ($candidate in $expected) {
                if ([string]::Equals([IO.Path]::GetFullPath($candidate).TrimEnd('\'),
                                      [IO.Path]::GetFullPath($target).TrimEnd('\'),
                                      [StringComparison]::OrdinalIgnoreCase)) { $match=$true }
            }
            if (-not $match) {
                Write-Warning "Unexpected junction target for $dest. Not touching it: $target"
                continue
            }
            Write-Host "[EXISTING JUNCTION] $dest -> $target"
            $confirm=Read-Host 'Convert this link into a REAL moved folder? Type CONVERT'
            if ($confirm -cne 'CONVERT') { Write-Host '[SKIPPED]'; continue }
            # Windows rmdir removes a junction link only; it does NOT remove the target contents.
            & cmd.exe /d /c ('rmdir "' + $dest + '"')
            if ($LASTEXITCODE -ne 0 -or (Test-Path -LiteralPath $dest)) {
                Write-Warning 'Could not remove the folder junction. Source remains untouched.'
                continue
            }
            Write-Host '[JUNCTION REMOVED] Actual AI files remain at their original path.'
        } else {
            Write-Host "[ALREADY PRESENT] $dest" -ForegroundColor Green
            continue
        }
    }
    $source = $null
    foreach ($path in $item.Sources) {
        if (Test-Path -LiteralPath (Join-Path $path $item.Marker)) {
            $source = $path
            break
        }
        if (Test-Path -LiteralPath (Join-Path $path 'webui-user.bat')) {
            $source = $path
            break
        }
    }
    if (-not $source) {
        Write-Host "[NOT FOUND] $($item.Name) on Desktop. Skipping." -ForegroundColor Yellow
        continue
    }
    $sourceEntry = Get-Item -LiteralPath $source -Force
    if ($sourceEntry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Write-Warning "Source is itself a link. Not moving: $source"
        continue
    }
    Write-Host ""
    Write-Host "From: $source"
    Write-Host "To:   $dest"
    $answer = Read-Host "MOVE this folder and all its downloaded models? Type MOVE to proceed"
    if ($answer -cne 'MOVE') {
        Write-Host '[SKIPPED] No changes made.'
        continue
    }
    try {
        Move-Item -LiteralPath $source -Destination $dest -ErrorAction Stop
        if (-not (Test-Path -LiteralPath (Join-Path $dest $item.Marker)) -and
            -not (Test-Path -LiteralPath (Join-Path $dest 'webui-user.bat'))) {
            Write-Warning "Folder moved but startup script not found at destination. Inspect manually: $dest"
        } else {
            Write-Host "[MOVED] $($item.Name)" -ForegroundColor Green
        }
    } catch {
        Write-Warning ("Move failed: " + $_.Exception.Message)
        Write-Warning 'Check BOTH the original and destination folders before attempting another move. Do not delete either copy.'
    }
}
Write-Host ''
Write-Host 'Done. Your GitHub .gitignore excludes tools/ including all model files.' -ForegroundColor Cyan
Write-Host 'IMPORTANT: ACE-Step Python .venv may refer to its old path after relocation.'
Write-Host 'If ACE-Step fails to start: cd into tools\ACE-Step-1.5 and run uv sync (or reinstall its venv).'
Write-Host 'Do not delete local data/, and do not move Ollama installation files manually.'
