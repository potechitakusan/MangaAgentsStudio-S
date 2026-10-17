$ErrorActionPreference = 'Stop'
$studioPidFile = Join-Path $PSScriptRoot '.runtime\server.pid'
if (!(Test-Path -LiteralPath $studioPidFile)) { Write-Host 'Studio PID file not found.'; exit 0 }
$studioPid = [int](Get-Content -LiteralPath $studioPidFile)
$studioProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$studioPid"
if (!$studioProcess) { Write-Host 'Studio is not running.'; exit 0 }
if ($studioProcess.CommandLine -notmatch '-m\s+studio\.cli\s+serve') { throw 'PID belongs to a different command. Not stopping it.' }
$studioJobs = Join-Path $PSScriptRoot '.runtime\jobs'
if (Test-Path -LiteralPath $studioJobs) {
    foreach ($studioJobFile in Get-ChildItem -LiteralPath $studioJobs -Filter '*.json') {
        $studioJob = Get-Content -Raw -Encoding UTF8 -LiteralPath $studioJobFile.FullName | ConvertFrom-Json
        if ($studioJob.status -eq 'running') { throw 'A job is still running. Wait for it to finish before stopping Studio.' }
    }
}
Stop-Process -Id $studioPid
Remove-Item -LiteralPath $studioPidFile
Write-Host 'Studio stopped. Strata remains running.'
