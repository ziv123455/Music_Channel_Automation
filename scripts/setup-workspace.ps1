$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tools = Join-Path $root 'tools'
New-Item -ItemType Directory -Path $tools -Force | Out-Null
$desktop = [Environment]::GetFolderPath('Desktop')
$items = @(
    @{ Name='ACE-Step-1.5'; Paths=@((Join-Path $desktop 'ACE-Step-1.5')); Marker='start_api_server.bat' },
    @{ Name='stable-diffusion-webui-forge'; Paths=@((Join-Path $desktop 'stable-diffusion-webui-forge'),(Join-Path $desktop 'webui_forge')); Marker='run.bat' }
)
foreach ($item in $items) {
    $destination = Join-Path $tools $item.Name
    if (Test-Path $destination) { Write-Host "[EXISTS] $destination"; continue }
    $origin = $null
    foreach ($candidate in $item.Paths) {
        if ((Test-Path (Join-Path $candidate $item.Marker)) -or
            (Test-Path (Join-Path $candidate 'webui-user.bat'))) {
            $origin = $candidate
            break
        }
    }
    if (-not $origin) {
        Write-Host "[SKIP] $($item.Name) not found on Desktop. You can install it later or create a junction manually."
        continue
    }
    New-Item -ItemType Junction -Path $destination -Target $origin | Out-Null
    Write-Host "[LINKED] $destination -> $origin"
}
Write-Host ''
Write-Host 'Workspace ready. These are folder junctions, not copies or moves.'
Write-Host 'No existing model data has been transferred or deleted.'
Write-Host 'Double-click start-all.bat to launch installed services.'
