param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Set up Strata separately; see web/guide/setup.html#strata. Grok users do not need Strata.
# This check is advisory: the studio starts even when Strata is unavailable.
# Use the same .env parser and endpoint as production; never print its values.
python -X utf8 -m studio.cli check-local-ai
Write-Host "Open http://127.0.0.1:$Port in your browser. Ctrl+C stops the studio."
python -X utf8 -m studio.cli serve --port $Port
