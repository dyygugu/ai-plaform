param(
  [string]$BaseUrl = "http://127.0.0.1:8789",
  [string]$ApiPrefix = $env:AIDP_API_PREFIX,
  [string]$ApiToken = $env:AIDP_ADMIN_API_TOKEN,
  [switch]$SeedSample
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
$env:AIDP_API_PREFIX = $ApiPrefix
$ApiToken = Resolve-ApiToken $ApiToken
$ApiHeaders = Get-ApiHeaders -ApiToken $ApiToken
if ($ApiHeaders.Count -gt 0) {
  $PSDefaultParameterValues["Invoke-RestMethod:Headers"] = $ApiHeaders
}

$containerStatus = docker ps --filter "name=aidp-monitor-next-app-1" --format "{{.Status}}"
if (-not $containerStatus) {
  throw "Docker container aidp-monitor-next-app-1 is not running"
}

if ($SeedSample) {
  & (Join-Path $PSScriptRoot "seed-local-sample.ps1") -BaseUrl $BaseUrl -ApiPrefix $ApiPrefix -ApiToken $ApiToken | Out-Host
}

$health = Invoke-RestMethod "$ApiBaseUrl/health" -TimeoutSec 15
if ($health.status -ne "ok") {
  throw "Health check failed: $($health | ConvertTo-Json -Compress)"
}

$index = Invoke-WebRequest "$BaseUrl/" -UseBasicParsing -TimeoutSec 15
if ($index.StatusCode -ne 200 -or $index.Content -notmatch 'id="root"') {
  throw "Frontend index did not return the React root"
}

$jsPath = [regex]::Match($index.Content, '/assets/[^"'']+\.js').Value
$cssPath = [regex]::Match($index.Content, '/assets/[^"'']+\.css').Value
if (-not $jsPath -or -not $cssPath) {
  throw "Frontend assets were not found in index.html"
}

$js = Invoke-WebRequest "$BaseUrl$jsPath" -UseBasicParsing -TimeoutSec 15
$css = Invoke-WebRequest "$BaseUrl$cssPath" -UseBasicParsing -TimeoutSec 15
if ($js.Content -notmatch 'AIDP Monitor' -or $js.Content -notmatch '首页总览' -or $js.Content -notmatch '生产管理看板') {
  throw "Frontend bundle does not contain expected next dashboard labels"
}

$legacyMigrationPreview = Invoke-RestMethod "$ApiBaseUrl/accounts/legacy-migration/preview" -TimeoutSec 15
if ($legacyMigrationPreview.total_candidates -ne 7 -or $legacyMigrationPreview.cookie_copy_enabled -ne $false) {
  throw "Legacy account migration preview mismatch: $($legacyMigrationPreview | ConvertTo-Json -Compress)"
}
$legacyMigrationRun = Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/accounts/legacy-migration/run" -Body '{"dry_run":false,"write_audit":true,"generate_report":true}' -ContentType "application/json" -TimeoutSec 15
if ($legacyMigrationRun.total_candidates -ne 7 -or $legacyMigrationRun.target_account_count -ne 7 -or $legacyMigrationRun.cookie_copy_enabled -ne $false) {
  throw "Legacy account migration run mismatch: $($legacyMigrationRun | ConvertTo-Json -Compress)"
}
$accounts = Invoke-RestMethod "$ApiBaseUrl/accounts" -TimeoutSec 15
if ($accounts.Count -ne 7) {
  throw "Expected 7 migrated accounts, got $($accounts.Count)"
}
$loginSlots = Invoke-RestMethod "$ApiBaseUrl/accounts/login-slots" -TimeoutSec 15
$clientSessionReject = $null
try {
  Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/client-session" -Body '{"userId":"pending","cookie":"x","referer":"https://aidp.juejin.cn/operation/task-v2"}' -ContentType "application/json" -TimeoutSec 15 | Out-Null
  throw "Invalid client session was unexpectedly accepted"
}
catch {
  $clientSessionReject = $_.Exception.Response.StatusCode.value__
  if ($clientSessionReject -ne 400) { throw }
}
$refreshResult = Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/tasks/catalog/refresh" -Body '{"use_live_readonly":false}' -ContentType "application/json" -TimeoutSec 15
if ($refreshResult.live_readonly_requested -ne $false -or -not $refreshResult.refresh_mode) {
  throw "Task refresh response missing production refresh fields: $($refreshResult | ConvertTo-Json -Compress)"
}
$catalog = Invoke-RestMethod "$ApiBaseUrl/tasks/catalog" -TimeoutSec 15
if ($SeedSample -and $catalog.items.Count -lt 1) {
  throw "Task catalog has no visible sample items after seeding"
}
$detailOk = $false
if ($catalog.items.Count -gt 0) {
  $detail = Invoke-RestMethod "$ApiBaseUrl/tasks/catalog/$($catalog.items[0].id)" -TimeoutSec 15
  $detailOk = $detail.covered_account_count -ge 1 -and $detail.timeline.Count -ge 1
}
$accountCoverageSummary = Invoke-RestMethod "$ApiBaseUrl/accounts/task-coverage/summary" -TimeoutSec 15
if ($accountCoverageSummary.account_count -ne 7 -or $accountCoverageSummary.source_task_count -lt 1 -or $accountCoverageSummary.matrix.Count -ne 7) {
  throw "Account coverage summary mismatch: $($accountCoverageSummary | ConvertTo-Json -Compress)"
}
$accountCoverageBaseline = Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/accounts/task-coverage/baseline" -Body '{"write_audit":true,"generate_report":true}' -ContentType "application/json" -TimeoutSec 15
if (-not $accountCoverageBaseline.report_path) {
  throw "Account coverage baseline report was not generated"
}
$dataQualitySummary = Invoke-RestMethod "$ApiBaseUrl/data-quality/summary" -TimeoutSec 15
if ($dataQualitySummary.account_count -ne 7 -or $dataQualitySummary.task_count -lt 1 -or $dataQualitySummary.earnings_row_count -ne 7) {
  throw "Data quality summary mismatch: $($dataQualitySummary | ConvertTo-Json -Compress)"
}
$dataQualityReport = Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/data-quality/report" -Body '{"write_audit":true,"generate_report":true,"generate_excel":true}' -ContentType "application/json" -TimeoutSec 15
if (-not $dataQualityReport.report_path -or -not $dataQualityReport.export_path) {
  throw "Data quality report/export was not generated: $($dataQualityReport | ConvertTo-Json -Compress)"
}
$incidentSummary = Invoke-RestMethod "$ApiBaseUrl/incidents/summary" -TimeoutSec 15
if ($incidentSummary.runbook_count -lt 6 -or $incidentSummary.external_send_enabled -ne $false) {
  throw "Incident summary mismatch: $($incidentSummary | ConvertTo-Json -Compress)"
}
$incidentClosure = Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/incidents/close-loop" -Body '{"dry_run":true,"write_audit":true,"generate_report":true}' -ContentType "application/json" -TimeoutSec 15
if (-not $incidentClosure.report_path) {
  throw "Incident closure report was not generated"
}
$finalMatrix = Invoke-RestMethod "$ApiBaseUrl/final-acceptance/matrix" -TimeoutSec 15
if ($finalMatrix.total_count -lt 10 -or $finalMatrix.failed_count -ne 0) {
  throw "Final acceptance matrix mismatch: $($finalMatrix | ConvertTo-Json -Compress)"
}
$finalEvidence = Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/final-acceptance/evidence" -Body '{"write_audit":true,"generate_report":true}' -ContentType "application/json" -TimeoutSec 15
if (-not $finalEvidence.report_path) {
  throw "Final acceptance evidence report was not generated"
}
$roadmapFinal = Invoke-RestMethod "$ApiBaseUrl/roadmap-final/summary" -TimeoutSec 15
if ($roadmapFinal.total_phases -ne 21 -or $roadmapFinal.production_domain -ne "manage.51gugu.uk") {
  throw "Roadmap final summary mismatch: $($roadmapFinal | ConvertTo-Json -Compress)"
}
$roadmapReport = Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/roadmap-final/report" -Body '{"write_audit":true,"generate_report":true}' -ContentType "application/json" -TimeoutSec 15
if (-not $roadmapReport.report_path) {
  throw "Roadmap final report was not generated"
}
$rules = Invoke-RestMethod "$ApiBaseUrl/tasks/rules" -TimeoutSec 15
if ($rules.prefix_rules.Count -lt 1) {
  throw "Task prefix rules are empty"
}
$abilityDrafts = Invoke-RestMethod "$ApiBaseUrl/task-abilities/drafts" -TimeoutSec 15
$abilityRunGateReady = $false
if ($catalog.items.Count -gt 0) {
  $abilityRunGate = Invoke-RestMethod "$ApiBaseUrl/task-abilities/$($catalog.items[0].task_id)/run-gate" -TimeoutSec 15
  $abilityRunGateReady = [bool]$abilityRunGate.task_id
}

[pscustomobject]@{
  docker_status = $containerStatus
  health_status = $health.status
  public_base_url = $health.public_base_url
  index_status = $index.StatusCode
  js_asset = $jsPath
  css_asset = $cssPath
  account_count = $accounts.Count
  login_slot_count = $loginSlots.Count
  client_session_reject_status = $clientSessionReject
  legacy_account_candidates = $legacyMigrationPreview.total_candidates
  legacy_account_report = $legacyMigrationRun.report_path
  account_coverage_status = $accountCoverageSummary.status
  account_coverage_source_tasks = $accountCoverageSummary.source_task_count
  account_coverage_report = $accountCoverageBaseline.report_path
  data_quality_status = $dataQualitySummary.status
  data_quality_earnings_rows = $dataQualitySummary.earnings_row_count
  data_quality_report = $dataQualityReport.report_path
  data_quality_export = $dataQualityReport.export_path
  incident_status = $incidentSummary.status
  incident_open_count = $incidentSummary.total_open
  incident_report = $incidentClosure.report_path
  final_matrix_status = $finalMatrix.status
  final_matrix_total = $finalMatrix.total_count
  final_evidence_report = $finalEvidence.report_path
  roadmap_final_status = $roadmapFinal.status
  roadmap_final_completed = "$($roadmapFinal.completed_phases)/$($roadmapFinal.total_phases)"
  roadmap_final_report = $roadmapReport.report_path
  task_refresh_mode = $refreshResult.refresh_mode
  task_count = $catalog.items.Count
  task_detail_ok = $detailOk
  prefix_rule_count = $rules.prefix_rules.Count
  ability_draft_count = $abilityDrafts.total
  ability_run_gate_endpoint = "/task-abilities/{task_id}/run-gate"
  ability_run_gate_checked = $abilityRunGateReady
  ops_job_count = (Invoke-RestMethod "$ApiBaseUrl/ops/jobs" -TimeoutSec 15).jobs.Count
  release_gate_ready = (Invoke-RestMethod "$ApiBaseUrl/ops/release-gate" -TimeoutSec 15).ready_for_manual_domain_switch
  scheduler_due_count = (Invoke-RestMethod "$ApiBaseUrl/ops/scheduler/plan" -TimeoutSec 15).due_count
  domain_runbook_manual = (Invoke-RestMethod "$ApiBaseUrl/ops/domain-switch-runbook" -TimeoutSec 15).manual_only
  observability_status = (Invoke-RestMethod "$ApiBaseUrl/observability/summary" -TimeoutSec 15).status
  collector_guard_safe = (Invoke-RestMethod "$ApiBaseUrl/observability/collector-guard" -TimeoutSec 15).safe_mode
  alert_rule_count = (Invoke-RestMethod "$ApiBaseUrl/alerts/rules" -TimeoutSec 15).Count
  alert_slo_status = (Invoke-RestMethod "$ApiBaseUrl/alerts/slo" -TimeoutSec 15).overall_status
  alert_external_send = (Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/alerts/evaluate" -Body '{"dry_run":true,"write_audit":true,"send_external":false}' -ContentType "application/json" -TimeoutSec 15).external_send_enabled
  delivery_status = (Invoke-RestMethod "$ApiBaseUrl/delivery/summary" -TimeoutSec 15).status
  delivery_manual_switch = (Invoke-RestMethod "$ApiBaseUrl/delivery/checklist" -TimeoutSec 15).manual_domain_switch_required
  inspection_status = (Invoke-RestMethod "$ApiBaseUrl/inspection/summary" -TimeoutSec 15).status
  inspection_report = (Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/inspection/run" -Body '{"write_audit":true,"generate_report":true}' -ContentType "application/json" -TimeoutSec 15).report_path
  freeze_status = (Invoke-RestMethod "$ApiBaseUrl/freeze/summary" -TimeoutSec 15).status
  freeze_manual_only = (Invoke-RestMethod "$ApiBaseUrl/freeze/checklist" -TimeoutSec 15).manual_confirmation_items.Count -ge 1
  freeze_report = (Invoke-RestMethod -Method Post -Uri "$ApiBaseUrl/freeze/baseline" -Body '{"write_audit":true,"generate_report":true}' -ContentType "application/json" -TimeoutSec 15).report_path
} | ConvertTo-Json -Compress
Write-Output "docker_smoke_ok=true"












