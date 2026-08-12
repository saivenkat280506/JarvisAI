# Demo: slow scroll (5s gap) → YouTube Music play
# No Spotify login.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "backend")

Write-Host "=== JARVIS Browser Demo ===" -ForegroundColor Cyan
Write-Host "1) Scroll (5 second gaps)"
Write-Host "2) Play on YouTube Music"
Write-Host ""

python -c @"
from executor.browser_puppeteer import linkedin_browser_demo
ok, msg = linkedin_browser_demo({
    'song': 'AC/DC Back in Black',
    'times': 4,
    'pixels': 350,
    'service': 'youtube_music',
})
print('OK' if ok else 'PARTIAL', msg)
"@

Write-Host ""
Write-Host "Demo finished. Browser window left open." -ForegroundColor Green
