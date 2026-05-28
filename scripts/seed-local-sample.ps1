param(
  [string]$BaseUrl = "http://127.0.0.1:8789",
  [string]$SummaryPath = (Join-Path $PSScriptRoot "../data/redacted-samples/task-page-latest-summary.json")
)
$ErrorActionPreference = "Stop"

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
  Invoke-RestMethod "$BaseUrl/api/v1/tasks/catalog/seed" -Method Post -ContentType "application/json; charset=utf-8" -Body $body | Out-Null
  $count += 1
}

Write-Output "seeded_task_count=$count"
