#Requires -Version 5.1
<#
.SYNOPSIS
  J.A.R.V.I.S. one-click launcher (start + stop in one place).

.DESCRIPTION
  Default (no flags): toggle - start if offline, stop if online.
  -Start  Force start (idempotent if already running)
  -Stop   Force stop
#>
param(
  [switch]$Start,
  [switch]$Stop
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not (Test-Path (Join-Path $Root "package.json"))) {
  $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
  if (-not (Test-Path (Join-Path $Root "package.json"))) {
    $Root = "C:\Users\saivenkat\Desktop\JARVIS"
  }
}

$BackendDir  = Join-Path $Root "backend"
$Python      = Join-Path $BackendDir ".venv\Scripts\python.exe"
$MainPy      = Join-Path $BackendDir "main.py"
$LogDir      = Join-Path $Root "logs"
$PidFile     = Join-Path $LogDir "jarvis.pids.json"
$BackendLog  = Join-Path $LogDir "backend.log"
$FrontendLog = Join-Path $LogDir "frontend.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Banner([string]$Mode = "Launcher") {
  Clear-Host
  try { $Host.UI.RawUI.WindowTitle = "J.A.R.V.I.S. - $Mode" } catch {}
  Write-Host ""
  Write-Host "  ========================================================" -ForegroundColor Cyan
  Write-Host "       J.A.R.V.I.S.  -  Just A Rather Very Intelligent System" -ForegroundColor White
  Write-Host "       $Mode  |  Local AI Assistant" -ForegroundColor DarkCyan
  Write-Host "  ========================================================" -ForegroundColor Cyan
  Write-Host ""
}

function Write-Step([string]$msg, [string]$status = "INFO") {
  $color = switch ($status) {
    "OK"    { "Green" }
    "WAIT"  { "Yellow" }
    "ERR"   { "Red" }
    "INFO"  { "Cyan" }
    default { "Gray" }
  }
  $tag = "[{0}]" -f $status.PadRight(4)
  Write-Host ("  {0} {1}" -f $tag, $msg) -ForegroundColor $color
}

function Test-PortOpen([int]$Port) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $iar = $c.BeginConnect("127.0.0.1", $Port, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(400)
    if ($ok -and $c.Connected) { $c.Close(); return $true }
    $c.Close()
    return $false
  } catch { return $false }
}

function Test-JarvisRunning {
  # Only the AI backend counts as "running" for toggle.
  # Frontend-only leftovers (Next.js still up after backend died) must NOT
  # force a full stop - they should be repaired by a fresh start.
  return (Test-PortOpen 8000)
}

function Test-BackendHealthy {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 2
    return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500)
  } catch {
    return $false
  }
}

function Wait-Http([string]$Url, [int]$TimeoutSec = 90) {
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
      if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
    } catch {}
    Start-Sleep -Milliseconds 800
  }
  return $false
}

function Save-Pids($backendPid, $frontendPid) {
  @{
    backend  = $backendPid
    frontend = $frontendPid
    started  = (Get-Date).ToString("o")
  } | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8
}

function Free-Port([int]$Port) {
  try {
    $conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
      Where-Object { $_.State -eq "Listen" -or $_.State -eq "Bound" }
    $killed = @{}
    foreach ($c in $conns) {
      $procId = $c.OwningProcess
      if ($procId -and $procId -ne 0 -and -not $killed.ContainsKey($procId)) {
        $killed[$procId] = $true
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Step "Freed port $Port (PID $procId)" "OK"
      }
    }
  } catch {}
}

function Stop-OrphanBackends {
  # Kill any leftover main.py on port 8000 or matching command lines
  # (venv + system python can both be left running and fight for the port).
  try {
    $procs = Get-CimInstance Win32_Process -Filter "name = 'python.exe' OR name = 'pythonw.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
      $cmd = $p.CommandLine
      if ($cmd -and ($cmd -match 'JARVIS\\backend\\main\.py' -or $cmd -match 'JARVIS/backend/main\.py' -or $cmd -match 'backend\\main\.py')) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Step "Stopped orphan backend PID $($p.ProcessId)" "OK"
      }
    }
  } catch {}
  Free-Port 8000
}

function Stop-BackendOnly {
  try {
    Invoke-RestMethod -Uri "http://127.0.0.1:8000/shutdown" -Method POST -TimeoutSec 3 | Out-Null
  } catch {}

  if (Test-Path $PidFile) {
    try {
      $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
      if ($pids.backend) {
        Stop-Process -Id $pids.backend -Force -ErrorAction SilentlyContinue
      }
    } catch {}
  }

  Stop-OrphanBackends
  Start-Sleep -Milliseconds 800
}

function Stop-JarvisServices {
  Write-Step "Stopping J.A.R.V.I.S. services..." "WAIT"

  try {
    Invoke-RestMethod -Uri "http://127.0.0.1:8000/shutdown" -Method POST -TimeoutSec 4 | Out-Null
    Write-Step "Backend shutdown requested" "OK"
  } catch {
    Write-Step "Backend shutdown endpoint not reachable" "INFO"
  }

  if (Test-Path $PidFile) {
    try {
      $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
      foreach ($key in @("backend", "frontend")) {
        $id = $pids.$key
        if ($id) {
          Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
          Write-Step "Stopped PID $id ($key)" "OK"
        }
      }
    } catch {}
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
  }

  Stop-OrphanBackends

  foreach ($port in 8000, 3000) {
    Free-Port $port
  }

  Get-Process -Name "electron" -ErrorAction SilentlyContinue | ForEach-Object {
    try {
      $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
      if (-not $cmd -or $cmd -match 'JARVIS' -or $cmd -match [regex]::Escape($Root)) {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
      }
    } catch {
      Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
  }

  Write-Host ""
  Write-Step "J.A.R.V.I.S. stopped." "OK"
  Write-Host ""
}

function Start-BackendFresh {
  # Always launch backend from this launcher (never skip).
  Write-Step "Preparing backend (always start fresh)..." "WAIT"
  if ((Test-PortOpen 8000) -or (Test-BackendHealthy)) {
    Write-Step "Port 8000 busy / old backend present - stopping first" "WAIT"
    Stop-BackendOnly
  } else {
    # Clear zombies that hold no listen socket yet
    Stop-OrphanBackends
  }

  # Clear previous backend logs so failures are easy to read
  Remove-Item $BackendLog -Force -ErrorAction SilentlyContinue
  Remove-Item (Join-Path $LogDir "backend.err.log") -Force -ErrorAction SilentlyContinue

  if (-not (Test-Path $Python)) {
    Write-Step "Python venv missing: $Python" "ERR"
    Read-Host "  Press Enter to exit"
    exit 1
  }

  Write-Step "Starting AI backend (FastAPI :8000)..." "WAIT"
  $prevUnbuf = $env:PYTHONUNBUFFERED
  $prevCtrl  = $env:FOR_DISABLE_CONSOLE_CTRL_HANDLER
  $env:PYTHONUNBUFFERED = "1"
  $env:FOR_DISABLE_CONSOLE_CTRL_HANDLER = "T"
  try {
    $bp = Start-Process -FilePath $Python -ArgumentList "`"$MainPy`"" `
      -WorkingDirectory $BackendDir `
      -WindowStyle Hidden `
      -RedirectStandardOutput $BackendLog `
      -RedirectStandardError (Join-Path $LogDir "backend.err.log") `
      -PassThru
  } finally {
    if ($null -eq $prevUnbuf) { Remove-Item Env:PYTHONUNBUFFERED -ErrorAction SilentlyContinue } else { $env:PYTHONUNBUFFERED = $prevUnbuf }
    if ($null -eq $prevCtrl)  { Remove-Item Env:FOR_DISABLE_CONSOLE_CTRL_HANDLER -ErrorAction SilentlyContinue } else { $env:FOR_DISABLE_CONSOLE_CTRL_HANDLER = $prevCtrl }
  }

  if (-not $bp -or -not $bp.Id) {
    Write-Step "Failed to spawn Python process" "ERR"
    Read-Host "  Press Enter to exit"
    exit 1
  }

  # Wait for /health. NOTE: On Windows the listen socket owner may differ from
  # Start-Process's PID (redirected stdio / child server process). Never kill a
  # healthy listener just because PIDs differ - that was killing the real backend.
  $deadline = (Get-Date).AddSeconds(120)
  $ready = $false
  $listenPid = $null
  while ((Get-Date) -lt $deadline) {
    $spawnAlive = Get-Process -Id $bp.Id -ErrorAction SilentlyContinue
    $healthy = Test-BackendHealthy
    if (-not $spawnAlive -and -not $healthy) {
      Write-Step "Backend process exited early (PID $($bp.Id)). See logs\backend.err.log" "ERR"
      if (Test-Path (Join-Path $LogDir "backend.err.log")) {
        Get-Content (Join-Path $LogDir "backend.err.log") -Tail 25 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkRed }
      }
      if (Test-Path $BackendLog) {
        Get-Content $BackendLog -Tail 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkYellow }
      }
      Read-Host "  Press Enter to exit"
      exit 1
    }
    if ($healthy) {
      try {
        $listenPid = (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
          Select-Object -First 1).OwningProcess
      } catch { $listenPid = $null }
      $ready = $true
      break
    }
    Start-Sleep -Milliseconds 800
  }

  if (-not $ready) {
    Write-Step "Backend failed to start. See logs\backend.err.log" "ERR"
    if (Test-Path (Join-Path $LogDir "backend.err.log")) {
      Get-Content (Join-Path $LogDir "backend.err.log") -Tail 20 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkRed }
    }
    Read-Host "  Press Enter to exit"
    exit 1
  }

  # Prefer the process actually bound to :8000 for stop/tracking
  $trackPid = if ($listenPid) { $listenPid } else { $bp.Id }
  if ($listenPid -and $listenPid -ne $bp.Id) {
    Write-Step "Backend online  (listen PID $listenPid, spawn PID $($bp.Id))" "OK"
  } else {
    Write-Step "Backend online  (PID $trackPid)" "OK"
  }
  return $trackPid
}

function Start-JarvisServices {
  if (-not (Test-Path $Python)) {
    Write-Step "Python venv missing: $Python" "ERR"
    Write-Host "  Run setup in backend first." -ForegroundColor Red
    Read-Host "  Press Enter to exit"
    exit 1
  }
  if (-not (Test-Path $MainPy)) {
    Write-Step "Backend entry missing: $MainPy" "ERR"
    Read-Host "  Press Enter to exit"
    exit 1
  }
  $npm = Get-Command npm -ErrorAction SilentlyContinue
  if (-not $npm) {
    Write-Step "npm not found in PATH" "ERR"
    Read-Host "  Press Enter to exit"
    exit 1
  }

  Write-Step "Project root: $Root" "INFO"
  Write-Step "Starting all services..." "WAIT"

  # ALWAYS start backend with the launcher
  $backendPid = Start-BackendFresh
  $frontendPid = $null

  if (Test-PortOpen 3000) {
    Write-Step "Frontend already online (port 3000)" "OK"
  } else {
    Write-Step "Starting UI (Next.js :3000)..." "WAIT"
    $npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $npmCmd) { $npmCmd = "npm.cmd" }
    $fp = Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$npmCmd`" run dev" `
      -WorkingDirectory $Root `
      -WindowStyle Hidden `
      -RedirectStandardOutput $FrontendLog `
      -RedirectStandardError (Join-Path $LogDir "frontend.err.log") `
      -PassThru
    $frontendPid = $fp.Id
    if (-not (Wait-Http "http://127.0.0.1:3000" 120)) {
      Write-Step "Frontend failed to start. See logs\frontend.err.log" "ERR"
      if (Test-Path (Join-Path $LogDir "frontend.err.log")) {
        Get-Content (Join-Path $LogDir "frontend.err.log") -Tail 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkRed }
      }
      Read-Host "  Press Enter to exit"
      exit 1
    }
    Write-Step "Frontend online (PID $frontendPid)" "OK"
  }

  Save-Pids $backendPid $frontendPid

  Write-Host ""
  Write-Step "Launching Electron desktop app..." "WAIT"

  # Tell Electron: backend is managed by this launcher - do not spawn/kill it.
  $prevWeb = $env:JARVIS_WEB_URL
  $prevExt = $env:JARVIS_EXTERNAL_BACKEND
  $env:JARVIS_WEB_URL = "http://127.0.0.1:3000"
  $env:JARVIS_EXTERNAL_BACKEND = "1"
  try {
    $electronBin = Join-Path $Root "node_modules\electron\dist\electron.exe"
    if (Test-Path $electronBin) {
      # Close any existing Electron for this app so we do not hit second-instance focus-only
      Get-Process -Name "electron" -ErrorAction SilentlyContinue | ForEach-Object {
        try {
          $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
          if ($cmd -and $cmd -match [regex]::Escape($Root)) {
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
          }
        } catch {}
      }
      Start-Sleep -Milliseconds 400
      Start-Process -FilePath $electronBin -ArgumentList "`"$Root`"" -WorkingDirectory $Root
      Write-Step "Electron desktop app launched (external backend mode)" "OK"
    } else {
      $npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
      if (-not $npmCmd) { $npmCmd = "npm.cmd" }
      Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c set JARVIS_WEB_URL=http://127.0.0.1:3000& set JARVIS_EXTERNAL_BACKEND=1& `"$npmCmd`" run desktop" `
        -WorkingDirectory $Root
      Write-Step "Electron launched via npm (external backend mode)" "OK"
    }
  } catch {
    Write-Step "Electron launch failed: $($_.Exception.Message)" "ERR"
    Write-Step "Install electron: npm install electron --save-dev" "ERR"
  } finally {
    if ($null -eq $prevWeb) { Remove-Item Env:JARVIS_WEB_URL -ErrorAction SilentlyContinue } else { $env:JARVIS_WEB_URL = $prevWeb }
    if ($null -eq $prevExt) { Remove-Item Env:JARVIS_EXTERNAL_BACKEND -ErrorAction SilentlyContinue } else { $env:JARVIS_EXTERNAL_BACKEND = $prevExt }
  }

  # Re-verify backend still healthy after UI launch
  Start-Sleep -Seconds 2
  if (Test-BackendHealthy) {
    Write-Step "Backend still healthy after UI launch" "OK"
  } else {
    Write-Step "Backend dropped after UI launch - restarting backend only..." "WAIT"
    $backendPid = Start-BackendFresh
    Save-Pids $backendPid $frontendPid
    if (Test-BackendHealthy) {
      Write-Step "Backend recovered" "OK"
    } else {
      Write-Step "Backend still offline. Check logs\backend.err.log" "ERR"
    }
  }

  Write-Host ""
  Write-Host "  --------------------------------------------------------" -ForegroundColor DarkCyan
  Write-Host "   SYSTEMS ONLINE" -ForegroundColor Green
  Write-Host "   UI:      http://127.0.0.1:3000" -ForegroundColor White
  Write-Host "   API:     http://127.0.0.1:8000/health" -ForegroundColor White
  Write-Host "   Logs:    $LogDir" -ForegroundColor DarkGray
  Write-Host ""
  Write-Host "   DEMO IDEAS (type or speak):" -ForegroundColor Yellow
  Write-Host "     - Hello Jarvis, introduce yourself" -ForegroundColor Gray
  Write-Host "     - What time is it?" -ForegroundColor Gray
  Write-Host "     - Open notepad" -ForegroundColor Gray
  Write-Host "     - Tell me a joke" -ForegroundColor Gray
  Write-Host "     - Set volume to 40" -ForegroundColor Gray
  Write-Host "     - send message to +91... (confirm with yes)" -ForegroundColor Gray
  Write-Host "  --------------------------------------------------------" -ForegroundColor DarkCyan
  Write-Host ""
  Write-Host "  Leave this window open during the demo." -ForegroundColor DarkGray
  Write-Host "  Run this launcher again to STOP J.A.R.V.I.S." -ForegroundColor Yellow
  Write-Host ""

  try {
    while ($true) {
      $b = Test-PortOpen 8000
      $f = Test-PortOpen 3000
      $bLabel = if ($b) { "ON" } else { "OFF" }
      $fLabel = if ($f) { "ON" } else { "OFF" }
      $clock = Get-Date -Format "HH:mm:ss"
      $status = "Backend:$bLabel  Frontend:$fLabel  $clock"
      try { $Host.UI.RawUI.WindowTitle = "J.A.R.V.I.S.  |  $status" } catch {}
      Start-Sleep -Seconds 5
    }
  } catch {
    # exit quietly
  }
}

# --- Main ---
if ($Start -and $Stop) {
  Write-Host "  Use either -Start or -Stop, not both." -ForegroundColor Red
  exit 1
}

$running = Test-JarvisRunning

if ($Stop -or (-not $Start -and $running)) {
  Write-Banner "Shutdown"
  if (-not $running -and $Stop) {
    Write-Step "Services already offline - cleaning leftovers..." "INFO"
  }
  Stop-JarvisServices
  if ($Host.Name -eq "ConsoleHost") {
    Start-Sleep -Seconds 2
  }
  exit 0
}

Write-Banner "Presentation Launcher"
Start-JarvisServices
