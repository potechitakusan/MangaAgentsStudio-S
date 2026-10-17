param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Strata is set up and started on its own (README "Strataの準備"; on this PC: start-studio-this-pc.cmd).
# Without it only the AI jobs are unavailable, so the studio still starts and just says so.
try {
    $health = Invoke-RestMethod 'http://127.0.0.1:8080/health' -TimeoutSec 2
    if ($health.loaded -ne $true) { Write-Host 'Strata is still loading. AI jobs (storyboard, review, translation) work once it is ready.' }
} catch {
    Write-Host 'Strata is not answering on 127.0.0.1:8080. The studio starts anyway; storyboards, reviews and translation need Strata (or Grok). See README "Strataの準備".'
}
Write-Host "Open http://127.0.0.1:$Port in your browser. Ctrl+C stops the studio."
python -X utf8 -m studio.cli serve --port $Port
