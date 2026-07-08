param(
  [string]$BaseUrl = "http://127.0.0.1:8789",
  [string]$ApiPrefix = $env:AIDP_API_PREFIX,
  [string]$ApiToken = $env:AIDP_ADMIN_API_TOKEN,
  [string]$SummaryPath = (Join-Path $PSScriptRoot "../data/redacted-samples/task-page-latest-summary.json")
)
$ErrorActionPreference = "Stop"
function Normalize-ApiPrefix {
  param([string]$Value)
  $prefix = ([string]$Value).Trim()
  if (-not $prefix) { $prefix = "/api" + "/v1" }
  if (-not $prefix.StartsWith("/")) { $prefix = "/$prefix" }
  $prefix = $prefix -replace "/+", "/"
  if ($prefix.Length -gt 1) { $prefix = $prefix.TrimEnd("/") }
  if (-not $prefix -or $prefix -eq "/") { $prefix = "/api" + "/v1" }
  return $prefix
}

function Resolve-ApiToken {
  param([string]$Value)
  $token = ([string]$Value).Trim()
  if ($token) { return $token }
  $envToken = ([string]$env:AIDP_ADMIN_API_TOKEN).Trim()
  if ($envToken) { return $envToken }
  $envPath = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) ".env"
  if (Test-Path $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
      if ($line -match '^\s*AIDP_ADMIN_API_TOKEN\s*=\s*(.+?)\s*$') {
        $candidate = $matches[1].Trim().Trim('"').Trim("'")
        if ($candidate -and -not $candidate.StartsWith('${')) { return $candidate }
      }
    }
  }
  return ""
}

function Get-ApiHeaders {
  param([string]$ApiToken)
  $token = ([string]$ApiToken).Trim()
  if (-not $token) { return @{} }
  return @{ "X-AIDP-API-Token" = $token }
}

$ApiPrefix = Normalize-ApiPrefix $ApiPrefix
$ApiBaseUrl = $BaseUrl.TrimEnd("/") + $ApiPrefix
$ApiToken = Resolve-ApiToken $ApiToken
$ApiHeaders = Get-ApiHeaders -ApiToken $ApiToken
if ($ApiHeaders.Count -gt 0) {
  $PSDefaultParameterValues["Invoke-RestMethod:Headers"] = $ApiHeaders
}

if (-not (Test-Path $SummaryPath)) {
  throw "Redacted sample summary not found: $SummaryPath"
}

$summary = Get-Content -Raw $SummaryPath | ConvertFrom-Json
$count = 0
foreach ($task in $summary.tasks) {
  $pendingRaw = "$($task.pendingRaw)"
  if (-not $pendingRaw -or $pendingRaw -eq "") {
    $pendingRaw = "0"
  }
  $body = @{
    raw_task_name = "$($task.title) $($task.taskId)"
    task_status_raw = "可做"
    pending_raw = $pendingRaw
  } | ConvertTo-Json -Depth 5
  Invoke-RestMethod "$ApiBaseUrl/tasks/catalog/seed" -Method Post -ContentType "application/json; charset=utf-8" -Body $body | Out-Null
  $count += 1
}

Write-Output "seeded_task_count=$count"
