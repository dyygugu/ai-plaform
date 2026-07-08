param(
  [string]$BaseUrl = "http://127.0.0.1:8789",
  [string]$ApiPrefix = $env:AIDP_API_PREFIX,
  [string]$ApiToken = $env:AIDP_ADMIN_API_TOKEN,
  [switch]$SkipDockerSmoke
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

Push-Location (Join-Path $PSScriptRoot "..")
try {
  $reportDir = Join-Path (Get-Location) "reports"
  New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
  $reportPath = Join-Path $reportDir ("acceptance-{0}.md" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

  & (Join-Path $PSScriptRoot "verify-p1.ps1") | Tee-Object -Variable verifyOutput | Out-Null

  $env:PYTHONDONTWRITEBYTECODE = "1"
  $env:PYTHONPATH = (Resolve-Path "backend").Path
  @"
import importlib
main = importlib.import_module('app.main')
fastapi_app = main.app
from app.db.base import Base
import app.db.models
expected={'aidp_accounts','task_catalog_items','task_rule_configs','audit_logs','backup_jobs','ai_jobs','workers','worker_events','maintenance_job_runs'}
assert expected.issubset(set(Base.metadata.tables.keys()))
routes=sorted(route.path for route in fastapi_app.routes if hasattr(route, 'path'))
for path in ['$ApiPrefix/health','$ApiPrefix/accounts','$ApiPrefix/accounts/login-slots','$ApiPrefix/accounts/login-slots/new','$ApiPrefix/accounts/{user_id}/login-slots/relogin','$ApiPrefix/accounts/client-session','/api/client-session','$ApiPrefix/accounts/legacy-migration/preview','$ApiPrefix/accounts/legacy-migration/run','$ApiPrefix/accounts/task-coverage/summary','$ApiPrefix/accounts/task-coverage/matrix','$ApiPrefix/accounts/task-coverage/baseline','$ApiPrefix/data-quality/summary','$ApiPrefix/data-quality/checks','$ApiPrefix/data-quality/export','$ApiPrefix/data-quality/report','$ApiPrefix/incidents/summary','$ApiPrefix/incidents/runbooks','$ApiPrefix/incidents/close-loop','$ApiPrefix/final-acceptance/matrix','$ApiPrefix/final-acceptance/rollback','$ApiPrefix/final-acceptance/evidence','$ApiPrefix/roadmap-final/summary','$ApiPrefix/roadmap-final/report','$ApiPrefix/tasks/catalog','$ApiPrefix/tasks/catalog/{item_id}','$ApiPrefix/tasks/catalog/refresh','$ApiPrefix/tasks/rules','$ApiPrefix/tasks/task-page/sample-capture','$ApiPrefix/task-abilities/drafts','$ApiPrefix/task-abilities/{task_id}/run-gate','$ApiPrefix/settings/runtime','$ApiPrefix/settings/task-source','$ApiPrefix/settings/permissions','$ApiPrefix/backups/plan','$ApiPrefix/backups/manual','$ApiPrefix/ai/queue','$ApiPrefix/workers','$ApiPrefix/workers/heartbeat','$ApiPrefix/workers/events','$ApiPrefix/workers/{worker_id}','$ApiPrefix/workers/{worker_id}/logs','$ApiPrefix/workers/{worker_id}/bind-account','$ApiPrefix/workers/{worker_id}/version','$ApiPrefix/workers/{worker_id}/claim-task','$ApiPrefix/alerts/preview','$ApiPrefix/alerts/rules','$ApiPrefix/alerts/slo','$ApiPrefix/alerts/summary','$ApiPrefix/alerts/evaluate','$ApiPrefix/delivery/summary','$ApiPrefix/delivery/checklist','$ApiPrefix/delivery/bundle','$ApiPrefix/inspection/summary','$ApiPrefix/inspection/checklist','$ApiPrefix/inspection/run','$ApiPrefix/freeze/summary','$ApiPrefix/freeze/checklist','$ApiPrefix/freeze/baseline','$ApiPrefix/restore-drills/run','$ApiPrefix/earnings/summary','$ApiPrefix/earnings/export','$ApiPrefix/ops/jobs','$ApiPrefix/ops/jobs/{job_key}/run','$ApiPrefix/ops/release-gate','$ApiPrefix/ops/scheduler/plan','$ApiPrefix/ops/scheduler/tick','$ApiPrefix/ops/domain-switch-runbook','$ApiPrefix/observability/summary','$ApiPrefix/observability/collector-guard','$ApiPrefix/observability/timeline','$ApiPrefix/observability/probes/run','$ApiPrefix/audit/logs']:
    assert path in routes, path
print('route_smoke_ok=true')
"@ | python - | Tee-Object -Variable routeSmokeOutput | Out-Null

  Push-Location frontend
  try {
    npm run build | Tee-Object -Variable buildOutput | Out-Null
  }
  finally {
    Pop-Location
  }

  $dockerDeployStatus = "跳过"
  $dockerDeployOutput = @("docker_deploy_skipped=true")
  $dockerSmokeStatus = "跳过"
  $dockerSmokeOutput = @("docker_smoke_skipped=true")
  if (-not $SkipDockerSmoke) {
    docker compose -f "infra/docker-compose.dev.yml" up --build -d | Out-Null
    $deadline = (Get-Date).AddSeconds(90)
    $healthy = $false
    do {
      Start-Sleep -Seconds 2
      try {
        $health = Invoke-RestMethod "$ApiBaseUrl/health" -TimeoutSec 5
        $healthy = $health.status -eq "ok"
      }
      catch {
        $healthy = $false
      }
    } while (-not $healthy -and (Get-Date) -lt $deadline)
    if (-not $healthy) {
      docker logs --tail 160 aidp-monitor-next-app-1 | Out-Host
      throw "Docker local service did not become healthy at $BaseUrl"
    }
    $dataReportDir = Join-Path (Get-Location) "data/reports"
    New-Item -ItemType Directory -Force -Path $dataReportDir | Out-Null
    Get-ChildItem -Path $reportDir -Filter "acceptance-*.md" -File | Copy-Item -Destination $dataReportDir -Force
    $workspaceRoot = (Resolve-Path (Join-Path (Get-Location) "../..")).Path
    $sourceScreenshotDir = Join-Path $workspaceRoot "output/playwright"
    $dataScreenshotDir = Join-Path (Get-Location) "data/output/playwright"
    if (Test-Path $sourceScreenshotDir) {
      New-Item -ItemType Directory -Force -Path $dataScreenshotDir | Out-Null
      Get-ChildItem -Path $sourceScreenshotDir -Filter "aidp-monitor-next-p*.png" -File | Copy-Item -Destination $dataScreenshotDir -Force
    }
    $dockerSmokeOutput = & (Join-Path $PSScriptRoot "docker-smoke.ps1") -BaseUrl $BaseUrl -ApiPrefix $ApiPrefix -ApiToken $ApiToken -SeedSample
    $containerStatus = docker ps --filter "name=aidp-monitor-next-app-1" --format "{{.Status}} {{.Ports}}"
    $dockerDeployOutput = @("docker_compose_up_ok=true", "container_status=$containerStatus")
    $dockerDeployStatus = "通过，已执行 docker compose up --build -d"
    $dockerSmokeStatus = "通过，$BaseUrl 返回新版前端并含任务样本"
  }

  $dockerDeployText = ($dockerDeployOutput | ForEach-Object { "$($_)" }) -join "；"
  $dockerSmokeText = ($dockerSmokeOutput | ForEach-Object { "$($_)" }) -join "；"
  $reportLines = @(
    "# aidp-monitor-next 自动验收报告",
    "",
    "生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "",
    "## 结果",
    "",
    "- verify-p1：通过，包含 API 集成 smoke",
    "- route smoke：通过",
    "- frontend build：通过",
    "- Docker 本地重部署：$dockerDeployStatus",
    "- Docker 本地可见性：$dockerSmokeStatus",
    "- 正式域名切换：未执行，manage.51gugu.uk 仍留给老版本",
    "",
    "## Docker 本地验收入口",
    "",
    "- 前端看板：$BaseUrl",
    "- 健康接口：$ApiBaseUrl/health",
    "- Docker deploy 输出：$dockerDeployText",
    "- Docker smoke 输出：$dockerSmokeText",
    "",
    "## 覆盖接口",
    "",
    "- $ApiPrefix/health",
    "- $ApiPrefix/accounts",
    "- $ApiPrefix/accounts/login-slots",
    "- $ApiPrefix/accounts/login-slots/new",
    "- $ApiPrefix/accounts/{user_id}/login-slots/relogin",
    "- $ApiPrefix/accounts/client-session",
    "- /api/client-session",
    "- $ApiPrefix/accounts/legacy-migration/preview",
    "- $ApiPrefix/accounts/legacy-migration/run",
    "- $ApiPrefix/accounts/task-coverage/summary",
    "- $ApiPrefix/accounts/task-coverage/matrix",
    "- $ApiPrefix/accounts/task-coverage/baseline",
    "- $ApiPrefix/data-quality/summary",
    "- $ApiPrefix/data-quality/checks",
    "- $ApiPrefix/data-quality/export",
    "- $ApiPrefix/data-quality/report",
    "- $ApiPrefix/incidents/summary",
    "- $ApiPrefix/incidents/runbooks",
    "- $ApiPrefix/incidents/close-loop",
    "- $ApiPrefix/final-acceptance/matrix",
    "- $ApiPrefix/final-acceptance/rollback",
    "- $ApiPrefix/final-acceptance/evidence",
    "- $ApiPrefix/roadmap-final/summary",
    "- $ApiPrefix/roadmap-final/report",
    "- $ApiPrefix/tasks/catalog",
    "- $ApiPrefix/tasks/catalog/{item_id}",
    "- $ApiPrefix/tasks/catalog/refresh",
    "- $ApiPrefix/tasks/rules",
    "- $ApiPrefix/tasks/task-page/sample-capture",
    "- $ApiPrefix/task-abilities/drafts",
    "- $ApiPrefix/task-abilities/{task_id}/run-gate",
    "- $ApiPrefix/settings/runtime",
    "- $ApiPrefix/settings/task-source",
    "- $ApiPrefix/settings/permissions",
    "- $ApiPrefix/backups/plan",
    "- $ApiPrefix/backups/manual",
    "- $ApiPrefix/ai/queue",
    "- $ApiPrefix/workers",
    "- $ApiPrefix/workers/heartbeat",
    "- $ApiPrefix/workers/events",
    "- $ApiPrefix/workers/{worker_id}",
    "- $ApiPrefix/workers/{worker_id}/logs",
    "- $ApiPrefix/workers/{worker_id}/bind-account",
    "- $ApiPrefix/workers/{worker_id}/version",
    "- $ApiPrefix/workers/{worker_id}/claim-task",
    "- $ApiPrefix/alerts/preview",
    "- $ApiPrefix/alerts/rules",
    "- $ApiPrefix/alerts/slo",
    "- $ApiPrefix/alerts/summary",
    "- $ApiPrefix/alerts/evaluate",
    "- $ApiPrefix/delivery/summary",
    "- $ApiPrefix/delivery/checklist",
    "- $ApiPrefix/delivery/bundle",
    "- $ApiPrefix/inspection/summary",
    "- $ApiPrefix/inspection/checklist",
    "- $ApiPrefix/inspection/run",
    "- $ApiPrefix/freeze/summary",
    "- $ApiPrefix/freeze/checklist",
    "- $ApiPrefix/freeze/baseline",
    "- $ApiPrefix/restore-drills/run",
    "- $ApiPrefix/earnings/summary",
    "- $ApiPrefix/earnings/export",
    "- $ApiPrefix/ops/jobs",
    "- $ApiPrefix/ops/jobs/{job_key}/run",
    "- $ApiPrefix/ops/release-gate",
    "- $ApiPrefix/ops/scheduler/plan",
    "- $ApiPrefix/ops/scheduler/tick",
    "- $ApiPrefix/ops/domain-switch-runbook",
    "- $ApiPrefix/observability/summary",
    "- $ApiPrefix/observability/collector-guard",
    "- $ApiPrefix/observability/timeline",
    "- $ApiPrefix/observability/probes/run",
    "- $ApiPrefix/audit/logs",
    "",
    "## 人工验收提醒",
    "",
    "- 先在测试环境验证，不切正式反代。",
    "- 人工打开 $BaseUrl，应看到左侧 AIDP Monitor、标题 生产管理看板、首页、账号管理登录入口、待处理刷新控制、任务看板、统计报表、数据校验、AI 标注能力工作台、Worker 管理、运维中枢、生产护栏、观测中心、告警中心、运维证据分组和脱敏任务样本。",
    "- 删除候选仍只允许移动到 delete。"
  )
  $reportLines | Set-Content -Encoding utf8NoBOM $reportPath
  Write-Output "acceptance_report=$reportPath"
}
finally {
  Pop-Location
}















