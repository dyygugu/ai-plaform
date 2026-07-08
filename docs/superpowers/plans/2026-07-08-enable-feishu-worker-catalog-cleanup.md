# Enable Feishu Worker Catalog Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 开启飞书真实错误提示和本机多 Worker 轮询，同时根治回收站账号独有任务仍显示，并把任务页旧“目录治理”从日常主页面收口到运维/设置语境。

**Architecture:** 飞书通知继续复用 `/api/v1/notifications` 和 `notification_service`，只通过配置开启真实发送，不改 webhook/secret。Worker 轮询继续复用本机助手 `/api/worker-runtime/start` 与平台 `/api/v1/workers/*`，只开启安全命令轮询，不放开正式提交命令。任务目录显示默认只取 active/stale/needs_login 账号来源，删除账号时同步隐藏该账号任务目录，恢复账号时恢复为需刷新状态。

**Tech Stack:** FastAPI + SQLAlchemy + pytest，React + Vite + Ant Design + Node 静态测试，PowerShell 7，NAS Docker，AIDP Local Helper WorkerRuntime。

---

## 目标

- 将当前已提交的两笔本地提交推送到 `origin/main`，避免 NAS 部署基于未推送提交。
- 飞书提示开启后，`/api/v1/notifications` 返回 `enabled=true`、`dry_run=false`、`sends_network=true`，真实错误才会推送。
- 飞书仍保持当前去重、冷却、人话文案和敏感信息脱敏；不恢复刷屏。
- 本机多 Worker 轮询开启后，本机 helper `/api/worker-runtime/status` 返回 `enabled=true` 且 `status=running` 或可解释的注册/轮询状态。
- 平台 `/api/v1/workers` 可看到本机 worker 注册/心跳；worker 只接受 `health_probe`、`dry_run_account_task_group`、`stage_only_account_task_group` 等安全命令。
- 删除/回收账号后，该账号独有任务不再出现在任务目录、AI 标注能力工作台任务选择、账号覆盖和数据质量默认统计中。
- 恢复账号后，不直接把旧任务当作当前真实待处理；恢复为可重新刷新/回填的状态，避免旧缓存误导生产。
- “任务与待处理”只保留任务级细化、账号分布、能力状态、任务操作台入口；旧“目录治理”不再作为主页面显性卡片。

## 范围

- Git/发布：
- `origin/main` 推送当前 `ahead 2` 提交。
- NAS 运行配置、容器配置或平台 API 配置。

- 飞书配置：
- `backend/app/services/notification_service.py` 现有配置读取/写入逻辑。
- `/api/v1/notifications` 配置接口。
- NAS `config/notifications.json` 或等价 API 写入结果。
- 飞书测试只做一次受控测试，避免重复刷屏。

- Worker 轮询：
- `local-agent-source/host-launcher.ps1` 现有 `/api/worker-runtime/status|start|stop`。
- 平台 `/api/v1/workers/register`、`/heartbeat`、`/{worker_id}/commands/claim`、`/commands/{id}/result`。
- 本机 helper 配置 `worker_runtime_enabled`、`worker_id`、`platform_base_url`、`platform_api_token`。

- 任务目录/账号回收：
- `backend/app/services/task_service.py`
- `backend/app/services/account_recycle_service.py`
- `backend/app/services/account_coverage_service.py`
- `backend/app/services/data_quality_service.py`
- `backend/app/services/task_ability_service.py` 中按 `task_id` 取任务目录项的默认选择。
- `backend/tests/test_task_catalog_aggregation.py`
- `backend/tests/test_account_recycle_service.py`
- 必要时补充 `backend/tests/test_task_ability_workbench_service.py` 或轻量静态测试。

- 前端职责收口：
- `frontend/src/pages/TasksPage.tsx`
- `frontend/src/pages/SettingsPage.tsx` 或 `frontend/src/pages/OpsPage.tsx`
- `frontend/src/api/client.ts`
- `frontend/tests/operation-ux-static-check.mjs`
- `backend/tests/test_execution_devices_frontend_static.py`

## 不做什么

- 不改真实飞书 webhook、secret、API key；只复用已有配置或通过配置接口保存。
- 不把飞书恢复成每题、每次正常刷新、每次成功心跳都推送。
- 不开启旧 `/api/v1/rules`、不恢复旧 `RulesPage.tsx`、不恢复“题型能力库”旧入口。
- 不删除 `/api/v1/tasks/rules`、`TaskRuleConfig`、`task_rules.py`，它们仍是任务简称/状态映射能力。
- 不删除历史 `task_catalog_items` 数据；默认隐藏/过滤，保留审计和恢复空间。
- 不执行真实做题、不正式提交、不开启旧 bon8 写入入口。
- 不绕过平台登录认证读取线上数据；需要 API token 时只用现有本机/平台配置，不在回复中泄露。
- 不做数据库破坏性迁移；如需数据修正，使用幂等 SQL 或服务逻辑更新可见性。

## 当前证据

- 本地分支：`main...origin/main [ahead 2]`，HEAD 为 `bb56e60 Fix Feishu notifications and remove legacy rules`。
- 旧 `/rules` 前端菜单、路由和旧页面已移除；后端旧 `/api/v1/rules` 未注册。
- 定向验证已通过：`npm --prefix frontend run test:operation-ux`；`pytest` 定向 53 项通过。
- 代码根因：`task_service.list_task_catalog()` 当前直接查询 `TaskCatalogItem` 全表，未排除 `AidpAccount.status == DISABLED` 的来源账号。
- 账号回收：`account_recycle_service.delete_account()` 将账号置为 `DISABLED` 并写入 `deleted_accounts`，但未同步处理该账号任务目录。
- 生产接口：公网未带平台登录时 `/api/v1/tasks/catalog`、`/api/v1/accounts/deleted`、`/api/v1/accounts/production-dashboard` 返回 401。

## 风险与回滚

- 风险 1：开启飞书真实发送后，如果上游错误仍频繁，可能恢复通知压力。缓解：保留 `AI_PROVIDER_*` 至少 3600 秒冷却、事件白名单、minLevel，并先发送一次 test 验证。
- 风险 2：WorkerRuntime 轮询注册失败会持续写本机错误日志。缓解：先检查 `platform_base_url`、`platform_api_token`、`worker_id`，再 start；失败时通过 `/api/worker-runtime/stop` 回滚。
- 风险 3：任务目录过滤 disabled 账号后，某些仅由回收账号贡献的历史能力入口会消失。缓解：这是目标行为；历史数据仍保留，恢复账号或重新刷新可恢复可见目录。
- 风险 4：账号覆盖/数据质量原本用于审计历史全量任务，默认过滤后统计口径变化。缓解：默认用户面只看活跃账号；如确需审计历史，后续新增显式 `include_disabled=true` 管理口径，不在本轮做。
- 风险 5：NAS 部署后配置覆盖本地文件。缓解：部署前备份 `/home/admin/aidp-monitor-next/data` 和 `/home/admin/aidp-monitor-next/config`，并保留当前镜像/提交回滚点。
- 回滚 1：飞书关闭：`PUT /api/v1/notifications` 写 `enabled=false` 或 `dry_run=true`。
- 回滚 2：Worker 停止：调用本机 helper `POST /api/worker-runtime/stop`，必要时将 `worker_runtime_enabled=false`。
- 回滚 3：代码回滚：回退本轮 commit，重新部署上一提交 `bb56e60`。
- 回滚 4：目录可见性恢复：恢复账号或将对应 `TaskCatalogItem.visibility` 从 `hidden` 改回 `restored`，再刷新生产数据。

## Task 1: 推送当前已完成提交

**Files:**
- No code change.

- [ ] **Step 1: 确认工作树干净且 ahead 2**

Run:

```powershell
git status --short --branch
git log --oneline --decorate -3
```

Expected:

```text
## main...origin/main [ahead 2]
bb56e60 (HEAD -> main) Fix Feishu notifications and remove legacy rules
```

- [ ] **Step 2: 推送到远端**

Run:

```powershell
git push origin main
```

Expected:

```text
To https://github.com/dyygugu/ai-plaform.git
   <old>..bb56e60  main -> main
```

- [ ] **Step 3: 再次确认不再 ahead**

Run:

```powershell
git status --short --branch
```

Expected:

```text
## main...origin/main
```

## Task 2: 红灯测试 - 删除账号后任务目录默认不显示该账号独有任务

**Files:**
- Modify: `backend/tests/test_task_catalog_aggregation.py`
- Modify: `backend/tests/test_account_recycle_service.py`

- [ ] **Step 1: 在 `test_task_catalog_aggregation.py` 添加过滤 disabled 来源账号测试**

Add test:

```python
from app.models.account import AccountStatus, AidpAccount


def test_default_catalog_excludes_disabled_account_only_tasks() -> None:
    db = _session()
    try:
        db.add(AidpAccount(user_id="account-active", display_name="活跃账号", status=AccountStatus.ACTIVE, auth_mode="client-cookie"))
        db.add(AidpAccount(user_id="account-disabled", display_name="回收账号", status=AccountStatus.DISABLED, auth_mode="client-cookie"))
        db.add(_task("account-active", "task-shared", "3", "共享任务"))
        db.add(_task("account-disabled", "task-shared", "9", "共享任务"))
        db.add(_task("account-disabled", "task-disabled-only", "8", "回收账号独有任务"))
        db.commit()

        items = list_task_catalog(db)

        assert [item.task_id for item in items] == ["task-shared"]
        assert items[0].source_account_user_id == "account-active"
    finally:
        db.close()
```

- [ ] **Step 2: 在 `test_account_recycle_service.py` 添加删除账号隐藏目录测试**

Add imports:

```python
from app.models.task import TaskCatalogItem, TaskVisibility
```

Add test:

```python
def test_delete_account_hides_task_catalog_rows_for_recycled_account(tmp_path: Path, monkeypatch) -> None:
    state_path, session_path = _configure_paths(tmp_path, monkeypatch)
    user_id = "123456789012"
    state_path.write_text(json.dumps({"accounts": [{"userId": user_id, "name": "用户123", "cookie": "state-cookie"}]}, ensure_ascii=False), encoding="utf-8")
    session_path.write_text(json.dumps({"accounts": []}, ensure_ascii=False), encoding="utf-8")
    db = _session()
    db.add(AidpAccount(user_id=user_id, display_name="用户123", status=AccountStatus.ACTIVE, auth_mode="client-cookie"))
    db.add(TaskCatalogItem(source_account_user_id=user_id, raw_task_name="独有任务 task-1", task_short_name="独有任务", task_id="task-1", task_name_id="独有任务task-1", pending_raw="8", task_status_raw="进行中"))
    db.commit()

    try:
        delete_account(db, user_id)
        db.commit()
        row = db.query(TaskCatalogItem).filter(TaskCatalogItem.source_account_user_id == user_id).one()
    finally:
        db.close()
        get_settings.cache_clear()

    assert row.visibility == TaskVisibility.HIDDEN
    assert "回收站" in (row.last_task_page_error or "")
```

- [ ] **Step 3: 运行红灯测试**

Run:

```powershell
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_task_catalog_aggregation.py::test_default_catalog_excludes_disabled_account_only_tasks backend/tests/test_account_recycle_service.py::test_delete_account_hides_task_catalog_rows_for_recycled_account -q
```

Expected before implementation:

```text
FAILED ... task-disabled-only ...
FAILED ... visibility ...
```

## Task 3: 实现任务目录按活跃来源账号过滤

**Files:**
- Modify: `backend/app/services/task_service.py`
- Modify: `backend/app/services/account_recycle_service.py`

- [ ] **Step 1: 在 `task_service.py` 引入账号模型并构建可见来源集合**

Add imports:

```python
from app.models.account import AccountStatus, AidpAccount
```

Add helper:

```python
def _active_task_source_ids(db: Session) -> set[str]:
    rows = db.scalars(select(AidpAccount.user_id).where(AidpAccount.status != AccountStatus.DISABLED))
    return {str(user_id).strip() for user_id in rows if str(user_id or "").strip()}
```

- [ ] **Step 2: 修改 `list_task_catalog()` 默认过滤逻辑**

Replace body with equivalent behavior:

```python
def list_task_catalog(db: Session, source_account_user_id: Optional[str] = None) -> list[TaskCatalogItem]:
    source = str(source_account_user_id or "").strip()
    query = select(TaskCatalogItem)
    if source:
        query = query.where(TaskCatalogItem.source_account_user_id == source)
    else:
        active_sources = _active_task_source_ids(db)
        if active_sources:
            query = query.where(TaskCatalogItem.source_account_user_id.in_(active_sources))
    items = list(db.scalars(query.order_by(TaskCatalogItem.updated_at.desc())))
    items = [item for item in items if item.visibility != TaskVisibility.HIDDEN]
    items = _drop_masked_duplicates(items)
    items = sorted(items, key=lambda item: (_pending_sort_value(item.pending_raw), item.task_status_raw, item.updated_at), reverse=True)
    return items if source else _deduplicate_catalog_by_task_id(items)
```

Rationale:
- 显式 `source_account_user_id` 保留单账号调试能力。
- 默认全账号聚合只看非 disabled 来源账号。
- `visibility=hidden` 不进入默认任务目录。

- [ ] **Step 3: 在 `account_recycle_service.py` 删除时隐藏该账号任务目录**

Add import:

```python
from app.models.task import TaskCatalogItem, TaskVisibility
```

Add helper:

```python
def _hide_recycled_account_tasks(db: Session, user_id: str) -> int:
    rows = list(db.scalars(select(TaskCatalogItem).where(TaskCatalogItem.source_account_user_id == user_id)))
    for row in rows:
        row.visibility = TaskVisibility.HIDDEN
        row.last_task_page_error = "来源账号已移入回收站；该账号独有任务默认隐藏，恢复账号或重新刷新后再参与展示。"
    return len(rows)
```

Call in `delete_account()` after `db_account.status = AccountStatus.DISABLED`:

```python
hidden_task_count = _hide_recycled_account_tasks(db, normalized)
```

Then include count in message:

```python
return AccountRecycleActionResponse(ok=True, user_id=normalized, message=f"账号已移入回收站，Cookie 和本机 profile 未清理；已隐藏该账号任务目录 {hidden_task_count} 条。")
```

- [ ] **Step 4: 恢复账号时不直接当作真实待处理，改为 restored/需刷新**

Add helper:

```python
def _restore_recycled_account_tasks(db: Session, user_id: str) -> int:
    rows = list(db.scalars(select(TaskCatalogItem).where(TaskCatalogItem.source_account_user_id == user_id)))
    for row in rows:
        if row.visibility == TaskVisibility.HIDDEN:
            row.visibility = TaskVisibility.RESTORED
        row.last_task_page_error = "账号已从回收站恢复；请刷新生产数据后确认当前真实待处理。"
    return len(rows)
```

Call in `restore_account()` before flush:

```python
restored_task_count = _restore_recycled_account_tasks(db, normalized)
```

Append count to restore message.

- [ ] **Step 5: 运行绿灯测试**

Run:

```powershell
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_task_catalog_aggregation.py backend/tests/test_account_recycle_service.py -q
```

Expected:

```text
... passed
```

## Task 4: 收口账号覆盖/数据质量/能力工作台的默认任务目录口径

**Files:**
- Modify: `backend/app/services/account_coverage_service.py`
- Modify: `backend/app/services/data_quality_service.py`
- Modify: `backend/app/services/task_ability_service.py`
- Test: `backend/tests/test_task_catalog_aggregation.py` or focused existing tests.

- [ ] **Step 1: 账号覆盖默认排除 disabled 账号和 hidden 任务**

Change `build_account_coverage_summary()`:

```python
accounts = list(db.scalars(select(AidpAccount).where(AidpAccount.status != AccountStatus.DISABLED).order_by(AidpAccount.is_task_source.desc(), AidpAccount.user_id.asc())))
tasks = list(db.scalars(select(TaskCatalogItem).where(TaskCatalogItem.visibility != TaskVisibility.HIDDEN).order_by(TaskCatalogItem.source_account_user_id.asc(), TaskCatalogItem.task_id.asc())))
```

- [ ] **Step 2: 数据质量默认排除 hidden 任务**

Change `data_quality_service.py` task queries used for summary/export:

```python
select(TaskCatalogItem).where(TaskCatalogItem.visibility != TaskVisibility.HIDDEN)
```

If the function joins account counts, also keep:

```python
account.status != AccountStatus.DISABLED
```

- [ ] **Step 3: 能力工作台按 task_id 选择任务目录时避开 hidden/disabled 来源**

Change `task_ability_service.py` near `select(TaskCatalogItem).where(TaskCatalogItem.task_id == task_id)` to prefer:

```python
select(TaskCatalogItem)
.where(TaskCatalogItem.task_id == task_id, TaskCatalogItem.visibility != TaskVisibility.HIDDEN)
.order_by(TaskCatalogItem.updated_at.desc())
```

If feasible, join/validate `AidpAccount.status != DISABLED`; otherwise依赖 `visibility=hidden` 和 `list_task_catalog()` 默认过滤。

- [ ] **Step 4: 补测试**

Add assertion to catalog/ability focused tests:

```python
assert all(item.visibility != TaskVisibility.HIDDEN for item in list_task_catalog(db))
```

- [ ] **Step 5: 运行相关测试**

Run:

```powershell
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_task_catalog_aggregation.py backend/tests/test_account_recycle_service.py backend/tests/test_task_ability_workbench_service.py -q
```

Expected: all pass.

## Task 5: 前端“任务与待处理”移除明面目录治理，迁入设置/运维语境

**Files:**
- Modify: `frontend/src/pages/TasksPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx` or `frontend/src/pages/OpsPage.tsx`
- Modify: `frontend/tests/operation-ux-static-check.mjs`

- [ ] **Step 1: 写红灯静态测试**

In `frontend/tests/operation-ux-static-check.mjs`, add:

```javascript
assert.doesNotMatch(tasks, /<Card title="目录治理"/, "任务页不能继续明面展示目录治理，目录来源和简称规则应进入系统设置或运维入口");
assert.doesNotMatch(tasks, /保存来源账号/, "任务页不能继续承担来源账号配置保存");
assert.doesNotMatch(tasks, /保存规则/, "任务页不能继续承担简称规则维护保存");
```

Expected before implementation: fails on `目录治理` / `保存来源账号` / `保存规则`。

- [ ] **Step 2: 从 `TasksPage.tsx` 删除目录治理卡片**

Remove card:

```tsx
<Card title="目录治理">...</Card>
```

Keep:
- 任务队列总表。
- 真实任务目录明细。
- 任务操作台。
- 跳转 `AI 标注能力工作台`。

- [ ] **Step 3: 保留 API 调用但从任务页移除编辑状态**

Remove unused state/effects/handlers from `TasksPage.tsx`:

```tsx
fetchTaskRules
updateTaskRules
updateTaskSourceAccount
rules
prefixRulesText
manualShortNamesText
taskSourceAccountUserId
handleSourceSubmit
handleRuleSubmit
loadRules
```

Keep `fetchTaskCatalog()` because任务页仍展示任务目录。

- [ ] **Step 4: 在 `SettingsPage.tsx` 增加“任务目录治理（运维）”折叠或卡片**

Implement:

```tsx
<Card title="任务目录治理（运维）">
  <Alert type="info" showIcon message="日常任务调控请去任务与待处理；这里仅维护来源账号和简称规则。" />
  ...
</Card>
```

Use existing APIs:

```tsx
fetchTaskRules()
updateTaskRules()
updateTaskSourceAccount()
```

Fields:
- 来源账号。
- 自动去除前缀，每行一个。
- 单任务手动简称 JSON。

- [ ] **Step 5: 运行前端静态测试**

Run:

```powershell
npm --prefix frontend run test:operation-ux
```

Expected: pass.

## Task 6: 开启飞书提示

**Files:**
- No source code change expected.
- Runtime config write: `/api/v1/notifications` or `config/notifications.json`.

- [ ] **Step 1: 读取当前飞书状态**

Run:

```powershell
$headers = @{ 'X-AIDP-API-Token' = $env:AIDP_PLATFORM_API_TOKEN }
Invoke-RestMethod -Uri 'https://platform.51gugu.uk/api/v1/notifications' -Headers $headers
```

Expected:
- `webhook_configured=true`
- `dry_run` 当前可能为 `true`
- `sends_network=false` 时说明尚未开启真实发送。

- [ ] **Step 2: 如果本机没有 token，则从本机 helper/平台配置读取，不在日志输出 token**

Allowed local read:

```powershell
Get-Content -LiteralPath 'D:\aidp-local-helper\config\helper-settings.json' -Encoding UTF8
```

Only inspect keys; never print full token in final report.

- [ ] **Step 3: 写入开启配置**

Payload:

```json
{
  "enabled": true,
  "min_level": "warn",
  "events": [
    "backend.error",
    "backend.unhandled_exception",
    "audit.error",
    "worker.error",
    "alert.evaluation.failed",
    "alert.evaluation.warning"
  ],
  "dry_run": false,
  "cooldown_seconds": 300
}
```

Run:

```powershell
Invoke-RestMethod -Method Put -Uri 'https://platform.51gugu.uk/api/v1/notifications' -Headers $headers -ContentType 'application/json;charset=UTF-8' -Body ($payload | ConvertTo-Json -Depth 10)
```

Expected:
- `enabled=true`
- `dry_run=false`
- `sends_network=true`
- webhook masked, not printed完整。

- [ ] **Step 4: 只发送一次测试通知**

Run:

```powershell
Invoke-RestMethod -Method Post -Uri 'https://platform.51gugu.uk/api/v1/notifications/test' -Headers $headers -ContentType 'application/json;charset=UTF-8' -Body '{"send":true}'
```

Expected:
- `sent=true` or clear failure reason.
- If failure: do not retry repeatedly; inspect reason first.

## Task 7: 开启多 Worker 轮询

**Files:**
- No source code change expected unless config persistence bug is found.
- Runtime local helper config/status.

- [ ] **Step 1: 检查本机 helper 健康**

Run:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8790/api/health'
```

Expected:
- `workerRuntimeSupported=true`
- `workerRuntimeId` 非空。

- [ ] **Step 2: 检查 WorkerRuntime 状态**

Run:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8790/api/worker-runtime/status'
```

Expected:
- `enabled=true`
- `allowed_commands` 只包含安全命令。

- [ ] **Step 3: 如 helper 未运行，启动本机 helper**

Run:

```powershell
Start-Process -FilePath 'pwsh.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','D:\aidp-local-helper\host-launcher.ps1') -WindowStyle Hidden
Start-Sleep -Seconds 3
Invoke-RestMethod -Uri 'http://127.0.0.1:8790/api/health'
```

Expected: health 200。

- [ ] **Step 4: 启动 WorkerRuntime**

Run:

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8790/api/worker-runtime/start'
```

Expected:
- `status=running` or immediate status indicates polling/registering.
- `worker_id` matches configured helper worker id.

- [ ] **Step 5: 平台侧确认 worker 注册/心跳**

Run:

```powershell
Invoke-RestMethod -Uri 'https://platform.51gugu.uk/api/v1/workers' -Headers $headers
```

Expected:
- list includes local helper worker id.
- status is not disabled/rejected.

- [ ] **Step 6: 创建安全 health_probe 命令验证轮询**

Run:

```powershell
$payload = @{
  worker_id = '<local-worker-id>'
  command_type = 'health_probe'
  account_user_id = ''
  task_id = ''
  payload = @{}
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Post -Uri 'https://platform.51gugu.uk/api/v1/workers/commands' -Headers $headers -ContentType 'application/json;charset=UTF-8' -Body $payload
Start-Sleep -Seconds 15
Invoke-RestMethod -Uri 'http://127.0.0.1:8790/api/worker-runtime/status'
```

Expected:
- command 被 helper 领取并完成。
- result contains `writes_remote=false`、`submits_remote=false`、`starts_run=false`。

## Task 8: 本地验证

**Files:**
- No new code unless test failures require fix.

- [ ] **Step 1: 后端定向测试**

Run:

```powershell
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_task_catalog_aggregation.py backend/tests/test_account_recycle_service.py backend/tests/test_task_ability_workbench_service.py backend/tests/test_human_readable_notifications.py backend/tests/test_operational_risk_adjustments.py backend/tests/test_execution_devices_frontend_static.py -q
```

Expected: all pass.

- [ ] **Step 2: 前端静态测试与 build**

Run:

```powershell
npm --prefix frontend run test:operation-ux
npm --prefix frontend run build
```

Expected: exit 0.

- [ ] **Step 3: 后端编译**

Run:

```powershell
python -m compileall -q backend/app
```

Expected: exit 0.

- [ ] **Step 4: 旧入口搜索**

Run:

```powershell
rg -n "题型能力库|RulesPage|rules\\.router|RuleVersion|RuleHitStat|RulePublishEvent|href=\"/rules|to=\"/rules" frontend/src backend/app backend/tests frontend/tests -S
```

Expected:
- 不出现旧 `/rules` 入口。
- 允许 `/tasks/rules`、`/alerts/rules`、历史文档和测试说明。

## Task 9: 独立 review

**Files:**
- No planned code changes unless review finds valid issue.

- [ ] **Step 1: 准备 review 输入**

Collect:

```powershell
git diff --stat origin/main..HEAD
git diff origin/main..HEAD -- backend/app/services/task_service.py backend/app/services/account_recycle_service.py backend/app/services/account_coverage_service.py backend/app/services/data_quality_service.py backend/app/services/task_ability_service.py frontend/src/pages/TasksPage.tsx frontend/src/pages/SettingsPage.tsx frontend/tests/operation-ux-static-check.mjs backend/tests/test_task_catalog_aggregation.py backend/tests/test_account_recycle_service.py
```

- [ ] **Step 2: 调用另一个 AI 做独立代码 review**

Review prompt must include:
- 目标和范围。
- 本方案路径。
- diff。
- 测试结果。
- 风险点：账号回收、任务目录口径、飞书真实推送、Worker 轮询。

- [ ] **Step 3: 处理 review 结果**

Rules:
- Critical/Important 成立则修复并重跑相关测试。
- 不成立则说明原因和证据。
- 修复后再次 review，直到无真实阻断问题。

## Task 10: 提交、部署 NAS、线上验收

**Files:**
- Commit source changes.
- NAS runtime deploy.

- [ ] **Step 1: 提交本轮代码**

Run:

```powershell
git status --short
git add backend/app/services/task_service.py backend/app/services/account_recycle_service.py backend/app/services/account_coverage_service.py backend/app/services/data_quality_service.py backend/app/services/task_ability_service.py backend/tests/test_task_catalog_aggregation.py backend/tests/test_account_recycle_service.py frontend/src/pages/TasksPage.tsx frontend/src/pages/SettingsPage.tsx frontend/tests/operation-ux-static-check.mjs docs/superpowers/plans/2026-07-08-enable-feishu-worker-catalog-cleanup.md
git commit -m "Fix recycled account task visibility and enable ops controls"
git push origin main
```

- [ ] **Step 2: NAS 部署前备份**

Run on NAS:

```bash
cd /home/admin/aidp-monitor-next
mkdir -p backups/pre-feishu-worker-catalog-$(date +%Y%m%d%H%M%S)
cp -a data config backups/pre-feishu-worker-catalog-$(date +%Y%m%d%H%M%S)/
```

- [ ] **Step 3: NAS 拉取并重建**

Run on NAS:

```bash
cd /home/admin/aidp-monitor-next
git pull --ff-only
docker compose up -d --build
docker compose ps
```

- [ ] **Step 4: 线上接口验收**

Run:

```powershell
Invoke-RestMethod -Uri 'https://platform.51gugu.uk/api/v1/health'
Invoke-RestMethod -Uri 'https://platform.51gugu.uk/api/v1/notifications' -Headers $headers
Invoke-RestMethod -Uri 'https://platform.51gugu.uk/api/v1/tasks/catalog' -Headers $headers
Invoke-RestMethod -Uri 'https://platform.51gugu.uk/api/v1/accounts/deleted' -Headers $headers
Invoke-RestMethod -Uri 'https://platform.51gugu.uk/api/v1/workers' -Headers $headers
```

Expected:
- health 200。
- notifications `sends_network=true`。
- deleted account 独有任务不在默认 catalog。
- worker 列表有本机 worker 心跳。

- [ ] **Step 5: 浏览器验收**

Use Browser/Playwright:
- 打开 `/tasks`，确认没有明面“目录治理”卡片。
- 打开 `/settings` 或 `/ops`，确认目录治理配置迁入运维/设置语境。
- 打开 `/ability-workbench`，确认任务选择不含回收账号独有任务。
- 打开 `/workers`，确认本机 worker 轮询/心跳可见。
- 打开 `/alerts` 或通知配置页，确认飞书状态为开启且不暴露 webhook/secret。

## 验证方式

- TDD 红灯：删除账号后独有任务仍显示、删除账号未隐藏任务目录、任务页仍有目录治理卡片。
- 绿灯：后端定向测试、前端静态测试、前端 build、后端 compileall。
- 运行态：飞书 test 只发一次；Worker health_probe 安全命令完成；NAS health 200。
- 数据口径：默认任务目录不显示 disabled/recycled 来源账号；显式 source 调试仍可用于管理员排查。
- 安全口径：worker result 必须包含 `writes_remote=false/submits_remote=false/starts_run=false`；飞书正文脱敏。

## 验收要求

- `origin/main` 包含当前已完成的旧规则移除和飞书通知修复提交。
- 飞书通知真实开启：`enabled=true`、`dry_run=false`、`sends_network=true`。
- 飞书只推关键 warning/error 事件，不推正常心跳、普通刷新、成功做题流水。
- WorkerRuntime 开启：本机 helper 返回 running/polling，平台 worker 列表可见心跳。
- WorkerRuntime 不执行正式生产命令。
- 删除/回收账号后，其独有任务不再出现在默认任务目录和能力工作台任务选择。
- 恢复账号后，需要重新刷新生产数据才能作为真实待处理依据。
- “任务与待处理”不再展示“目录治理/来源账号保存/简称规则保存”。
- 目录治理配置在设置或运维语境可用，不丢功能。
- 本地验证通过，独立 review 无真实阻断问题。
- NAS 端部署后接口和页面验收通过。

## 验收目标

平台进入可运维状态：飞书只在需要处理时推送明确错误；本机 Worker 轮询在线但不越权执行生产写入；任务目录不再被回收账号历史数据污染；旧目录治理不再压在日常任务页上，用户看到的是任务级生产调控主线。
