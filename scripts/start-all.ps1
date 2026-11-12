param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
function Test-Port([int]$Port) {
    $socket = New-Object System.Net.Sockets.TcpClient
    try {
        $result = $socket.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne(850)) { return $false }
        $socket.EndConnect($result)
        return $true
    } catch { return $false } finally { $socket.Close() }
}
function Find-Folder([string[]]$Candidates, [string]$Marker) {
    foreach ($item in $Candidates) {
        if ($item -and (Test-Path (Join-Path $item $Marker))) {
            return (Resolve-Path $item).Path
        }
    }
    return $null
}
function Launch-Batch([string]$Label, [string]$Directory, [string]$BatName, [int]$Port) {
    if (Test-Port $Port) {
        Write-Host "[RUNNING] $Label at 127.0.0.1:$Port" -ForegroundColor Green
        return
    }
    if (-not $Directory) {
        Write-Host "[NOT INSTALLED] $Label. See README setup instructions." -ForegroundColor Yellow
        return
    }
    $bat = Join-Path $Directory $BatName
    Write-Host "[STARTING] $Label from $Directory" -ForegroundColor Cyan
    # New console stays visible for diagnostics. Avoid launching a second copy on an active port.
    Start-Process -FilePath $env:ComSpec -WorkingDirectory $Directory -ArgumentList ('/k call "' + $bat + '"') | Out-Null
}
$desktop = [Environment]::GetFolderPath('Desktop')
$ace = Find-Folder @((Join-Path $root 'tools\ACE-Step-1.5'),(Join-Path $desktop 'ACE-Step-1.5'),(Join-Path $root 'ACE-Step-1.5')) 'start_api_server.bat'
$forge = Find-Folder @((Join-Path $root 'tools\stable-diffusion-webui-forge'),(Join-Path $desktop 'stable-diffusion-webui-forge'),(Join-Path $root 'stable-diffusion-webui-forge')) 'webui-user.bat'
if (-not $forge) {
    $forge = Find-Folder @((Join-Path $root 'tools\webui_forge'),(Join-Path $desktop 'webui_forge')) 'run.bat'
}
Write-Host ''
Write-Host 'Music Channel Automation - One-click launcher' -ForegroundColor Magenta
Write-Host '================================================'
Launch-Batch 'ACE-Step music AI' $ace 'start_api_server.bat' 8001

if (Test-Port 11434) {
    Write-Host '[RUNNING] Ollama at 127.0.0.1:11434' -ForegroundColor Green
} else {
    $ollamaCmd = Get-Command 'ollama.exe' -ErrorAction SilentlyContinue
    $ollamaPath = if ($ollamaCmd) { $ollamaCmd.Source } else { Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
    if (Test-Path $ollamaPath) {
        Write-Host '[STARTING] Ollama text AI' -ForegroundColor Cyan
        Start-Process -FilePath $ollamaPath -ArgumentList 'serve' -WindowStyle Minimized | Out-Null
    } else {
        Write-Host '[NOT INSTALLED] Ollama. Install Ollama to enable automatic music prompts.' -ForegroundColor Yellow
    }
}
if (Test-Port 7860) {
    Write-Host '[RUNNING] Thumbnail AI at 127.0.0.1:7860' -ForegroundColor Green
} elseif ($forge) {
    # Forge's webui-user.bat uses COMMANDLINE_ARGS; run.bat can also consume it
    # depending on the installed Forge distribution.
    $env:COMMANDLINE_ARGS = '--api'
    $forgeBat = if (Test-Path (Join-Path $forge 'webui-user.bat')) { 'webui-user.bat' } else { 'run.bat' }
    Launch-Batch 'Forge thumbnail AI' $forge $forgeBat 7860
    Write-Host '[NOTE] Verify Forge --api is enabled: http://127.0.0.1:7860/sdapi/v1/options' -ForegroundColor Yellow
} else {
    Write-Host '[OPTIONAL] Forge not found. Thumbnail templates still work.' -ForegroundColor Yellow
}
if (Test-Port 5050) {
    Write-Host '[RUNNING] Flask dashboard at 127.0.0.1:5050' -ForegroundColor Green
} else {
    Launch-Batch 'Music dashboard' $root 'start.bat' 5050
}
Write-Host ''
Write-Host 'Services run in separate windows. Close each service window to stop it.' -ForegroundColor Yellow
Write-Host 'Dashboard: http://127.0.0.1:5050'
if (-not $NoBrowser) {
    Start-Process 'http://127.0.0.1:5050' | Out-Null
}
