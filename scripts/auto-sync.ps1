param([int]$IntervalSeconds = 30)
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
if (-not (Test-Path '.git')) { throw 'Not a Git repository. Clone from GitHub first.' }
$branch = (& git branch --show-current).Trim()
if (-not $branch) { throw 'No current branch.' }
Write-Host "Auto-sync running on $branch. Ctrl+C to stop."
while ($true) {
    try {
        & git fetch origin $branch
        if ($LASTEXITCODE -ne 0) { throw 'Git fetch failed.' }
        & git add -A
        if ($LASTEXITCODE -ne 0) { throw 'Git add failed.' }
        & git diff --cached --quiet
        if ($LASTEXITCODE -ne 0) {
            & git commit -m "Auto-sync $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
            if ($LASTEXITCODE -ne 0) { throw 'Git commit failed.' }
        }
        & git pull --rebase --autostash origin $branch
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'Rebase conflict. Auto-sync stopped. Inspect git status.'
            break
        }
        & git push origin $branch
        if ($LASTEXITCODE -ne 0) { throw 'Git push failed.' }
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Synced"
    } catch {
        Write-Warning $_.Exception.Message
    }
    Start-Sleep -Seconds ([Math]::Max(10,$IntervalSeconds))
}
