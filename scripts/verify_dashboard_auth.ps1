$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$accessPath = Join-Path $root 'deploy\ACCESS.local.txt'

if (-not (Test-Path $accessPath)) {
  throw "Missing $accessPath"
}

$access = Get-Content -Raw -Path $accessPath

function Get-AccessValue([string]$Label) {
  $match = [regex]::Match($access, [regex]::Escape($Label) + '\s*([^\r\n]+)')
  if (-not $match.Success) {
    throw "Missing $Label in $accessPath"
  }
  return $match.Groups[1].Value.Trim()
}

$url = Get-AccessValue 'URL:'
$user = Get-AccessValue 'Basic Auth user:'
$password = Get-AccessValue 'Basic Auth password:'

$healthUrl = $url.TrimEnd('/') + '/api/health'
$authCode = & curl.exe -sS -o NUL -w '%{http_code}' -u "${user}:${password}" $healthUrl
$noAuthCode = & curl.exe -sS -o NUL -w '%{http_code}' $healthUrl

Write-Host "dashboard_auth=$authCode noauth=$noAuthCode url=$healthUrl"

if ($authCode -ne '200') {
  throw "Dashboard Basic Auth check failed: expected auth=200, got auth=$authCode"
}
if ($noAuthCode -ne '401') {
  throw "Dashboard Basic Auth guard failed: expected noauth=401, got noauth=$noAuthCode"
}
