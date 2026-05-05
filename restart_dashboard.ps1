$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$logsDir = Join-Path $here 'logs'
$pidFile = Join-Path $logsDir 'dashboard_server.pid'
$py = Join-Path $here '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }

function Get-DashboardProcessIds {
  $ids = [System.Collections.Generic.HashSet[int]]::new()

  if (Test-Path $pidFile) {
    try {
      $pidText = (Get-Content $pidFile -ErrorAction Stop | Select-Object -First 1).Trim()
      $pidInt = [int]$pidText
      if ($pidInt -gt 0 -and $pidInt -ne $PID) {
        [void]$ids.Add($pidInt)
      }
    } catch {}
  }

  try {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
      $_.ProcessId -ne $PID -and (
        $_.CommandLine -match 'run_dashboard_server\.py' -or
        $_.CommandLine -match 'orders_dashboard\.py' -or
        $_.CommandLine -match 'run_dashboard\.ps1' -or
        $_.CommandLine -match 'src\.main\s+--mode'
      )
    } | ForEach-Object {
      if ($_.ProcessId -gt 0) {
        [void]$ids.Add([int]$_.ProcessId)
      }
    }
  } catch {}

  try {
    Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
      if ($_.OwningProcess -gt 0 -and $_.OwningProcess -ne $PID) {
        [void]$ids.Add([int]$_.OwningProcess)
      }
    }
  } catch {}

  return @($ids)
}

function Stop-DashboardProcesses {
  $ids = Get-DashboardProcessIds
  if (-not $ids -or $ids.Count -eq 0) {
    Write-Host 'Dashboard processes are not running.'
  }

  foreach ($procId in $ids) {
    try {
      Write-Host "Stopping PID $procId"
      & taskkill /PID $procId /T /F | Out-Null
    } catch {}
  }

  Start-Sleep -Seconds 1

  if (Test-Path $pidFile) {
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  }
}

function Wait-ForDashboard([int]$timeoutSeconds = 25) {
  $deadline = (Get-Date).AddSeconds($timeoutSeconds)
  do {
    try {
      $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8088/' -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
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

Write-Host 'Restarting dashboard...' -ForegroundColor Yellow

# If port is already listening, just sync the PID file and exit
$existingPid = Get-PortOwnerProcessId
if ($existingPid) {
  Write-Host "Dashboard already running on port 8088 (PID $existingPid), updating PID file." -ForegroundColor Cyan
  Set-Content -Path $pidFile -Value $existingPid -Encoding ascii
  exit 0
}

Stop-DashboardProcesses

if (-not (Test-Path $logsDir)) {
  New-Item -ItemType Directory -Path $logsDir | Out-Null
}

# Remove stale sync lock from a killed previous run
$syncLock = Join-Path $logsDir 'dashboard_sync.lock'
if (Test-Path $syncLock) {
  Remove-Item $syncLock -Force -ErrorAction SilentlyContinue
}

$stdoutLog = Join-Path $logsDir 'dashboard_stdout.log'
$stderrLog = Join-Path $logsDir 'dashboard_stderr.log'

$serverProc = Start-Process -FilePath $py `
  -ArgumentList @('run_dashboard_server.py') `
  -WorkingDirectory $here `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog `
  -WindowStyle Hidden `
  -PassThru

Set-Content -Path $pidFile -Value $serverProc.Id -Encoding ascii

if (-not (Wait-ForDashboard 25)) {
  throw 'Dashboard did not respond on http://127.0.0.1:8088/ after restart.'
}

$activeServerPid = Get-PortOwnerProcessId
if ($activeServerPid) {
  Set-Content -Path $pidFile -Value $activeServerPid -Encoding ascii
}

Write-Host "Dashboard restarted successfully. Server PID: $((Get-Content $pidFile | Select-Object -First 1).Trim())" -ForegroundColor Green
