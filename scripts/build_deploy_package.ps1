$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'dist'
$stage = Join-Path $dist 'ozon-dashboard'
$archive = Join-Path $dist 'ozon-dashboard-deploy.tar.gz'

if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Path $stage | Out-Null
New-Item -ItemType Directory -Path $dist -Force | Out-Null

$excludeDirs = @(
  '.git', '.venv', '.pytest_cache', '__pycache__', 'node_modules',
  'logs', 'exports', 'uploads', 'tmp', 'tmp_wb_reports', 'api_cache',
  'dist'
)
$excludeExt = @(
  '.db', '.sqlite', '.sqlite3', '.log', '.out', '.err', '.pid', '.jsonl',
  '.har', '.png', '.jpg', '.jpeg', '.zip', '.xlsx', '.xls', '.csv'
)
$excludeFiles = @('.env', 'cookies.txt', 'ACCESS.local.txt')

Get-ChildItem -Path $root -Force | ForEach-Object {
  $name = $_.Name
  if ($excludeDirs -contains $name -or $excludeFiles -contains $name) { return }
  if (-not $_.PSIsContainer -and ($excludeExt -contains $_.Extension.ToLowerInvariant())) { return }
  Copy-Item -Path $_.FullName -Destination (Join-Path $stage $name) -Recurse -Force
}

$localAccessFile = Join-Path $stage 'deploy/ACCESS.local.txt'
if (Test-Path $localAccessFile) { Remove-Item -Force $localAccessFile }

if (Test-Path $archive) { Remove-Item -Force $archive }
Push-Location $stage
try {
  & tar -czf $archive .
} finally {
  Pop-Location
}

Write-Host "Created $archive"
