$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

$logsDir = Join-Path $here 'logs'
if (-not (Test-Path $logsDir)) {
  New-Item -ItemType Directory -Path $logsDir | Out-Null
}
$stdoutLog = Join-Path $logsDir 'dashboard_stdout.log'
$stderrLog = Join-Path $logsDir 'dashboard_stderr.log'
$pidFile = Join-Path $logsDir 'dashboard_server.pid'

function Load-DotEnv {
  $envFile = Join-Path $here '.env'
  if (-not (Test-Path $envFile)) { return }
  Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
      $parts = $line.Split('=', 2)
      $key = $parts[0].Trim()
      $val = $parts[1]
      if ($key) { Set-Item -Path ("Env:" + $key) -Value $val }
    }
  }
}

function Get-DashboardProcessIds {
  $ids = [System.Collections.Generic.HashSet[int]]::new()

  if (Test-Path $pidFile) {
    try {
      $pidText = (Get-Content $pidFile -ErrorAction Stop | Select-Object -First 1).Trim()
      $pidInt = [int]$pidText
      if ($pidInt -gt 0 -and $pidInt -ne $PID) { [void]$ids.Add($pidInt) }
    } catch {}
  }

  try {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
      $_.ProcessId -ne $PID -and (
        $_.CommandLine -match 'run_dashboard_server\.py' -or
        $_.CommandLine -match 'orders_dashboard\.py' -or
        $_.CommandLine -match 'src\.main\s+--mode'
      )
    } | ForEach-Object {
      if ($_.ProcessId -gt 0) { [void]$ids.Add([int]$_.ProcessId) }
    }
  } catch {}

  try {
    Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
      if ($_.OwningProcess -gt 0 -and $_.OwningProcess -ne $PID) { [void]$ids.Add([int]$_.OwningProcess) }
    }
  } catch {}

  return @($ids)
}

function Stop-DashboardProcesses {
  $ids = Get-DashboardProcessIds
  foreach ($procId in $ids) {
    try {
      Write-Host "Stopping PID $procId"
      & taskkill /PID $procId /T /F | Out-Null
    } catch {}
  }
  Start-Sleep -Milliseconds 800
  if (Test-Path $pidFile) {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  }
}

function Wait-ForDashboard([int]$timeoutSeconds = 20) {
  $deadline = (Get-Date).AddSeconds($timeoutSeconds)
  do {
    try {
      $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:8088/' -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
      if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
        return $true
      }
    } catch {}
    Start-Sleep -Milliseconds 500
  } while ((Get-Date) -lt $deadline)
  return $false
}

function Get-PortOwnerProcessId {
  try {
    $conn = Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue |
      Select-Object -First 1
    if ($conn -and $conn.OwningProcess -gt 0) {
      return [int]$conn.OwningProcess
    }
  } catch {}

  return $null
}

function Start-DashboardServer {
  Load-DotEnv
  $env:DASHBOARD_SUPERVISOR = '1'
  $env:PYTHONIOENCODING = 'utf-8'
  $env:PYTHONUTF8 = '1'

  Write-Host 'Starting dashboard server'
  $proc = Start-Process -FilePath $py `
    -ArgumentList @('run_dashboard_server.py') `
    -WorkingDirectory $here `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

  Set-Content -Path $pidFile -Value $proc.Id -Encoding ascii

  if (-not (Wait-ForDashboard 20)) {
    throw "Dashboard did not start on http://127.0.0.1:8088/"
  }

  $activeServerPid = Get-PortOwnerProcessId
  if ($activeServerPid) {
    Set-Content -Path $pidFile -Value $activeServerPid -Encoding ascii
  }

  return $proc
}

$script:serverProc = $null

function Restart-DashboardServer {
  Stop-DashboardProcesses
  $script:serverProc = Start-DashboardServer
  Write-Host "Dashboard ready (PID $($script:serverProc.Id))"
}

Restart-DashboardServer

$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $here
$watcher.IncludeSubdirectories = $true
$watcher.Filter = '*.*'
$watcher.EnableRaisingEvents = $true

$global:lastRestart = Get-Date '2000-01-01'
$debounceMs = 800

$null = Register-ObjectEvent $watcher Changed -SourceIdentifier FileChanged -Action {
  $ext = [System.IO.Path]::GetExtension($Event.SourceEventArgs.FullPath).ToLowerInvariant()
  if ($ext -notin @('.py', '.html', '.css', '.js')) { return }
  $now = Get-Date
  if (($now - $global:lastRestart).TotalMilliseconds -lt $debounceMs) { return }
  $global:lastRestart = $now
  Write-Host "File changed: $($Event.SourceEventArgs.FullPath)"
  Restart-DashboardServer
}

Write-Host 'Watching for changes... (Ctrl+C to stop)'
try {
  while ($true) {
    Start-Sleep -Seconds 1
    $serverPid = $null
    try {
      $pidText = (Get-Content $pidFile -ErrorAction Stop | Select-Object -First 1).Trim()
      $serverPid = [int]$pidText
    } catch {}

    $running = $false
    if ($serverPid) {
      try {
        $proc = Get-Process -Id $serverPid -ErrorAction Stop
        $running = -not $proc.HasExited
      } catch {}
    }

    if (-not $running) {
      Write-Host 'Dashboard process is not running. Restarting...'
      Restart-DashboardServer
    }
  }
} finally {
  Unregister-Event -SourceIdentifier FileChanged -ErrorAction SilentlyContinue
  $watcher.EnableRaisingEvents = $false
  $watcher.Dispose()
  Stop-DashboardProcesses
}
