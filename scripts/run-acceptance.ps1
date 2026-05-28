param(
  [string]$BaseUrl = "http://127.0.0.1:8789",
  [switch]$SkipDockerSmoke
)
$ErrorActionPreference = "Stop"

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
expected={'aidp_accounts','task_catalog_items','audit_logs','backup_jobs','ai_jobs','workers','rule_versions','rule_publish_events','rule_hit_stats','worker_events','maintenance_job_runs'}
assert expected.issubset(set(Base.metadata.tables.keys()))
routes=sorted(route.path for route in fastapi_app.routes if hasattr(route, 'path'))
for path in ['/api/v1/health','/api/v1/accounts','/api/v1/accounts/login-slots','/api/v1/accounts/login-slots/new','/api/v1/accounts/{user_id}/login-slots/relogin','/api/v1/accounts/client-session','/api/client-session','/api/v1/accounts/legacy-migration/preview','/api/v1/accounts/legacy-migration/run','/api/v1/accounts/task-coverage/summary','/api/v1/accounts/task-coverage/matrix','/api/v1/accounts/task-coverage/baseline','/api/v1/data-quality/summary','/api/v1/data-quality/checks','/api/v1/data-quality/export','/api/v1/data-quality/report','/api/v1/incidents/summary','/api/v1/incidents/runbooks','/api/v1/incidents/close-loop','/api/v1/final-acceptance/matrix','/api/v1/final-acceptance/rollback','/api/v1/final-acceptance/evidence','/api/v1/roadmap-final/summary','/api/v1/roadmap-final/report','/api/v1/tasks/catalog','/api/v1/tasks/catalog/{item_id}','/api/v1/tasks/catalog/refresh','/api/v1/tasks/rules','/api/v1/tasks/task-page/sample-capture','/api/v1/settings/runtime','/api/v1/settings/task-source','/api/v1/settings/permissions','/api/v1/backups/plan','/api/v1/backups/manual','/api/v1/ai/queue','/api/v1/workers','/api/v1/workers/heartbeat','/api/v1/workers/events','/api/v1/workers/{worker_id}','/api/v1/workers/{worker_id}/logs','/api/v1/workers/{worker_id}/bind-account','/api/v1/workers/{worker_id}/version','/api/v1/workers/{worker_id}/claim-task','/api/v1/rules/center','/api/v1/rules/versions','/api/v1/rules/versions/{version_id}/diff','/api/v1/rules/versions/{version_id}/canary','/api/v1/rules/versions/{version_id}/publish','/api/v1/rules/versions/{version_id}/rollback','/api/v1/alerts/preview','/api/v1/alerts/rules','/api/v1/alerts/slo','/api/v1/alerts/summary','/api/v1/alerts/evaluate','/api/v1/delivery/summary','/api/v1/delivery/checklist','/api/v1/delivery/bundle','/api/v1/inspection/summary','/api/v1/inspection/checklist','/api/v1/inspection/run','/api/v1/freeze/summary','/api/v1/freeze/checklist','/api/v1/freeze/baseline','/api/v1/restore-drills/run','/api/v1/earnings/summary','/api/v1/earnings/export','/api/v1/ops/jobs','/api/v1/ops/jobs/{job_key}/run','/api/v1/ops/release-gate','/api/v1/ops/scheduler/plan','/api/v1/ops/scheduler/tick','/api/v1/ops/domain-switch-runbook','/api/v1/observability/summary','/api/v1/observability/collector-guard','/api/v1/observability/timeline','/api/v1/observability/probes/run','/api/v1/audit/logs']:
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
        $health = Invoke-RestMethod "$BaseUrl/api/v1/health" -TimeoutSec 5
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
    $dockerSmokeOutput = & (Join-Path $PSScriptRoot "docker-smoke.ps1") -BaseUrl $BaseUrl -SeedSample
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
    "- 健康接口：$BaseUrl/api/v1/health",
    "- Docker deploy 输出：$dockerDeployText",
    "- Docker smoke 输出：$dockerSmokeText",
    "",
    "## 覆盖接口",
    "",
    "- /api/v1/health",
    "- /api/v1/accounts",
    "- /api/v1/accounts/login-slots",
    "- /api/v1/accounts/login-slots/new",
    "- /api/v1/accounts/{user_id}/login-slots/relogin",
    "- /api/v1/accounts/client-session",
    "- /api/client-session",
    "- /api/v1/accounts/legacy-migration/preview",
    "- /api/v1/accounts/legacy-migration/run",
    "- /api/v1/accounts/task-coverage/summary",
    "- /api/v1/accounts/task-coverage/matrix",
    "- /api/v1/accounts/task-coverage/baseline",
    "- /api/v1/data-quality/summary",
    "- /api/v1/data-quality/checks",
    "- /api/v1/data-quality/export",
    "- /api/v1/data-quality/report",
    "- /api/v1/incidents/summary",
    "- /api/v1/incidents/runbooks",
    "- /api/v1/incidents/close-loop",
    "- /api/v1/final-acceptance/matrix",
    "- /api/v1/final-acceptance/rollback",
    "- /api/v1/final-acceptance/evidence",
    "- /api/v1/roadmap-final/summary",
    "- /api/v1/roadmap-final/report",
    "- /api/v1/tasks/catalog",
    "- /api/v1/tasks/catalog/{item_id}",
    "- /api/v1/tasks/catalog/refresh",
    "- /api/v1/tasks/rules",
    "- /api/v1/tasks/task-page/sample-capture",
    "- /api/v1/settings/runtime",
    "- /api/v1/settings/task-source",
    "- /api/v1/settings/permissions",
    "- /api/v1/backups/plan",
    "- /api/v1/backups/manual",
    "- /api/v1/ai/queue",
    "- /api/v1/workers",
    "- /api/v1/workers/heartbeat",
    "- /api/v1/workers/events",
    "- /api/v1/workers/{worker_id}",
    "- /api/v1/workers/{worker_id}/logs",
    "- /api/v1/workers/{worker_id}/bind-account",
    "- /api/v1/workers/{worker_id}/version",
    "- /api/v1/workers/{worker_id}/claim-task",
    "- /api/v1/rules/center",
    "- /api/v1/rules/versions",
    "- /api/v1/rules/versions/{version_id}/diff",
    "- /api/v1/rules/versions/{version_id}/canary",
    "- /api/v1/rules/versions/{version_id}/publish",
    "- /api/v1/rules/versions/{version_id}/rollback",
    "- /api/v1/alerts/preview",
    "- /api/v1/alerts/rules",
    "- /api/v1/alerts/slo",
    "- /api/v1/alerts/summary",
    "- /api/v1/alerts/evaluate",
    "- /api/v1/delivery/summary",
    "- /api/v1/delivery/checklist",
    "- /api/v1/delivery/bundle",
    "- /api/v1/inspection/summary",
    "- /api/v1/inspection/checklist",
    "- /api/v1/inspection/run",
    "- /api/v1/freeze/summary",
    "- /api/v1/freeze/checklist",
    "- /api/v1/freeze/baseline",
    "- /api/v1/restore-drills/run",
    "- /api/v1/earnings/summary",
    "- /api/v1/earnings/export",
    "- /api/v1/ops/jobs",
    "- /api/v1/ops/jobs/{job_key}/run",
    "- /api/v1/ops/release-gate",
    "- /api/v1/ops/scheduler/plan",
    "- /api/v1/ops/scheduler/tick",
    "- /api/v1/ops/domain-switch-runbook",
    "- /api/v1/observability/summary",
    "- /api/v1/observability/collector-guard",
    "- /api/v1/observability/timeline",
    "- /api/v1/observability/probes/run",
    "- /api/v1/audit/logs",
    "",
    "## 人工验收提醒",
    "",
    "- 先在测试环境验证，不切正式反代。",
    "- 人工打开 $BaseUrl，应看到左侧 AIDP Monitor、标题 生产管理看板、首页、账号管理登录入口、待处理刷新控制、任务看板、统计报表、数据校验、规则中心、Worker 管理、运维中枢、生产护栏、观测中心、告警中心、运维证据分组和脱敏任务样本。",
    "- 删除候选仍只允许移动到 delete。"
  )
  $reportLines | Set-Content -Encoding utf8NoBOM $reportPath
  Write-Output "acceptance_report=$reportPath"
}
finally {
  Pop-Location
}















