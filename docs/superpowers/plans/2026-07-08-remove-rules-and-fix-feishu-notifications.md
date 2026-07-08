# Remove Legacy Rules And Fix Feishu Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 彻底移除旧“题型能力库/旧 P7 规则中心”可见入口与后端路由，并把飞书错误通知改成少重复、能一句话说明问题来源的人话告警。

**Architecture:** AI 做题能力唯一入口保留 `AI 标注能力工作台`，任务页只引导到 `/ability-workbench`，旧 `/rules` 前端页面和后端 `/api/v1/rules` 不再注册。飞书通知继续复用 `notification_service` 的统一渲染、脱敏和冷却文件，但为 `AI_PROVIDER_*` 引入更长冷却，并把 Worker 上报的结构化错误字段传入摘要生成。

**Tech Stack:** FastAPI + SQLAlchemy + pytest，React + Vite + Ant Design + 静态 Node 测试，PowerShell 7 / Windows，本地 SQLite/JSON 数据。

---

## 目标

- 用户侧不再看到“题型能力库”入口、按钮、路由或旧规则中心页面。
- 旧 `/api/v1/rules` 规则中心 API 不再注册，避免继续维护无效规则版本体系。
- `/api/v1/tasks/rules` 保留，因为它是任务简称配置，不是旧能力库。
- 飞书同一类做题 AI 上游故障不再按 300 秒刷屏，至少按小时级冷却。
- 飞书文案必须能直接说明问题出在哪，例如“做题 AI 上游超时”或“模型不存在/无权限”，而不是只显示代码。
- Worker 上报的 `error_code/error_detail/stage/step/retryable/duration_ms` 要参与通知摘要和排查上下文。

## 范围

- 前端：`frontend/src/layouts/AppLayout.tsx`、`frontend/src/routes/router.tsx`、`frontend/src/pages/TasksPage.tsx`、删除旧 `frontend/src/pages/RulesPage.tsx`。
- 后端 API：`backend/app/api/v1/router.py`，移除旧 `backend/app/api/v1/routes/rules.py` 注册。
- 后端旧规则模型/服务引用：`backend/app/db/models.py`、`backend/app/services/observability_service.py`、`backend/app/services/ops_job_service.py`、`backend/app/services/delivery_service.py`、`backend/app/services/alerting_service.py`、`backend/app/services/incident_service.py`、`backend/app/services/ops_risk_service.py`、`backend/app/services/task_auto_run_service.py`、`backend/app/services/task_ability_service.py`。
- 通知链路：`backend/app/services/notification_service.py`、`backend/app/services/worker_service.py`。
- 测试：`frontend/tests/operation-ux-static-check.mjs`、`backend/tests/test_execution_devices_frontend_static.py`、`backend/tests/api_smoke.py`、`backend/tests/test_human_readable_notifications.py`、`backend/tests/test_operational_risk_adjustments.py`、`backend/tests/test_task_auto_run_service.py`。

## 不做什么

- 不删除 `/api/v1/tasks/rules`、`TaskRuleConfig`、`task_rules.py`，这些是任务简称/任务状态映射能力。
- 不修改真实飞书 webhook、secret、API key、做题 AI key。
- 不改数据库历史数据，不写迁移删除旧表；本轮只让应用不再注册和使用旧规则中心。
- 不启动真实做题、不提交题目、不触发真实上游 AI 请求。
- 不部署 NAS、不重启服务；部署需后续单独确认。

## 风险与回滚

- 风险 1：后端旧 `RuleVersion` 被观测/运维探针隐式依赖。处理方式：替换为能力工作台/任务能力检查，不保留旧规则表依赖。
- 风险 2：静态测试中大量旧“题型能力库”断言会失败。处理方式：同步改为“不存在旧入口”和“引导到 AI 标注能力工作台”。
- 风险 3：飞书冷却过长可能掩盖不同 AI 错误。处理方式：只对 `AI_PROVIDER_*` 上游故障合并长冷却，账号登录失效、提交失败等仍按账号/任务维度冷却。
- 回滚：使用 `git diff` 确认变更；若出现不可接受回归，回退本轮变更文件即可。当前基线为本地 `main` ahead 1 的提交状态。

## Task 1: 写失败测试，锁定旧入口必须消失

**Files:**
- Modify: `frontend/tests/operation-ux-static-check.mjs`
- Modify: `backend/tests/test_execution_devices_frontend_static.py`
- Modify: `backend/tests/api_smoke.py`

- [ ] Step 1: 把前端静态测试从“必须有题型能力库”改成“不得存在题型能力库和 `/rules` 入口”。
- [ ] Step 2: 把任务页断言改成“无能力任务必须引导到 AI 标注能力工作台”。
- [ ] Step 3: 从 API smoke 删除旧 `/api/v1/rules/*` 断言，保留 `/api/v1/tasks/rules`。
- [ ] Step 4: 运行失败测试，期望当前代码失败：

```powershell
npm --prefix frontend run test:operation-ux
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_execution_devices_frontend_static.py backend/tests/api_smoke.py -q
```

Expected: 至少因 `AppLayout.tsx`、`RulesPage.tsx`、`TasksPage.tsx` 和旧 `/api/v1/rules` 注册而失败。

## Task 2: 写失败测试，锁定飞书人话摘要和长冷却

**Files:**
- Modify: `backend/tests/test_human_readable_notifications.py`
- Modify: `backend/tests/test_operational_risk_adjustments.py`

- [ ] Step 1: 增加 `AI_PROVIDER_502` 文案测试，要求 `error_detail` 进入“问题出在”，且能识别 502/Bad Gateway。
- [ ] Step 2: 增加 `AI_PROVIDER_TIMEOUT` 文案测试，要求说明“做题 AI 上游超时”。
- [ ] Step 3: 增加 provider outage 冷却测试，配置 `cooldownSec=300` 时，冷却文件中 10 分钟前的同类 AI provider 错误仍必须跳过。
- [ ] Step 4: 增加 Worker 上报通知测试，要求 `report_worker_event()` 触发通知时 data 包含 `error_code/error_detail/stage/step/retryable/duration_ms`。
- [ ] Step 5: 运行失败测试：

```powershell
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_human_readable_notifications.py backend/tests/test_operational_risk_adjustments.py -q
```

Expected: 当前实现因摘要泛化、冷却只有 300 秒、Worker 通知 data 缺结构化字段而失败。

## Task 3: 移除前端旧题型能力库

**Files:**
- Modify: `frontend/src/layouts/AppLayout.tsx`
- Modify: `frontend/src/routes/router.tsx`
- Modify: `frontend/src/pages/TasksPage.tsx`
- Delete: `frontend/src/pages/RulesPage.tsx`

- [ ] Step 1: 从左侧菜单删除 `/rules` 项。
- [ ] Step 2: 从 router 删除 `RulesPage` import 和 `path: "rules"`。
- [ ] Step 3: 删除 `RulesPage.tsx`。
- [ ] Step 4: 将任务页所有“题型能力库”用户可见文案改为“AI 标注能力工作台”。
- [ ] Step 5: 将任务页所有 `/rules` 按钮链接改为 `/ability-workbench`，有 `task_id` 时保留查询参数。
- [ ] Step 6: 运行：

```powershell
npm --prefix frontend run test:operation-ux
```

Expected: 前端旧入口相关断言通过。

## Task 4: 移除后端旧 `/api/v1/rules` 注册和旧规则依赖

**Files:**
- Modify: `backend/app/api/v1/router.py`
- Modify: `backend/app/db/models.py`
- Modify: `backend/app/services/observability_service.py`
- Modify: `backend/app/services/ops_job_service.py`
- Modify: `backend/app/services/delivery_service.py`
- Modify: `backend/app/services/alerting_service.py`
- Modify: `backend/app/services/incident_service.py`
- Modify: `backend/app/services/ops_risk_service.py`
- Modify: `backend/app/services/task_auto_run_service.py`
- Modify: `backend/app/services/task_ability_service.py`
- Delete if unused: `backend/app/api/v1/routes/rules.py`
- Delete if unused: `backend/app/models/rule.py`
- Delete if unused: `backend/app/schemas/rule.py`
- Delete if unused: `backend/app/services/rule_service.py`

- [ ] Step 1: 从 `router.py` import 和 include 中移除 `rules`。
- [ ] Step 2: 从 DB model registry 移除 `RuleHitStat/RulePublishEvent/RuleVersion`。
- [ ] Step 3: 把观测服务的 `published_rules`、`_probe_rules`、`RulePublishEvent` timeline 替换为能力工作台/任务能力草稿检查。
- [ ] Step 4: 把运维 gate `_rules_check` 改成能力工作台检查，不再要求 published rule。
- [ ] Step 5: 把 `/rules` 风险跳转替换为 `/ability-workbench`。
- [ ] Step 6: 把后端用户文案里的“题型能力库”改为“AI 标注能力工作台”。
- [ ] Step 7: 删除旧规则中心文件。
- [ ] Step 8: 运行：

```powershell
$env:PYTHONPATH='backend'; python -m pytest backend/tests/api_smoke.py backend/tests/test_task_auto_run_service.py backend/tests/test_operational_risk_adjustments.py -q
python -m compileall -q backend/app
```

Expected: 旧 `/api/v1/rules` 不再参与 smoke，后端编译无旧规则 import 错误。

## Task 5: 修复飞书重复推送和错误不明确

**Files:**
- Modify: `backend/app/services/notification_service.py`
- Modify: `backend/app/services/worker_service.py`
- Modify: `backend/app/core/settings.py` if a setting is needed

- [ ] Step 1: 在 `worker_service.add_worker_event()` 增加 `notification_data` 参数，合并到 `send_error_notification(data=...)`。
- [ ] Step 2: 在 `report_worker_event()` 传入结构化字段，包括 `message/stage/step/error_code/error_detail/retryable/duration_ms`。
- [ ] Step 3: 在 `notification_service._build_human_summary()` 中让 `AI_PROVIDER_502/TIMEOUT` 使用 `error_detail` 生成一句话问题说明。
- [ ] Step 4: 增加 `_provider_outage_cooldown_seconds()`，对 `AI_PROVIDER_*` 使用 `max(config.cooldownSec, 3600)`。
- [ ] Step 5: 确保 `_redact()` 仍处理 token/cookie/webhook/sign。
- [ ] Step 6: 运行：

```powershell
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_human_readable_notifications.py backend/tests/test_operational_risk_adjustments.py -q
```

Expected: provider 错误文案清晰、同类故障小时级冷却、Worker 结构化字段进入通知 data。

## Task 6: 全面验证和独立 review

**Files:**
- No planned code file changes unless review finds valid issues.

- [ ] Step 1: 运行定向后端验证：

```powershell
$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_human_readable_notifications.py backend/tests/test_operational_risk_adjustments.py backend/tests/test_task_auto_run_service.py backend/tests/test_execution_devices_frontend_static.py backend/tests/api_smoke.py -q
```

- [ ] Step 2: 运行前端静态和构建：

```powershell
npm --prefix frontend run test:operation-ux
npm --prefix frontend run build
```

- [ ] Step 3: 运行后端编译：

```powershell
python -m compileall -q backend/app
```

- [ ] Step 4: 搜索确认旧入口：

```powershell
rg -n "题型能力库|/rules|RulesPage|rules\\.router|RuleVersion|RuleHitStat|RulePublishEvent" frontend/src backend/app backend/tests frontend/tests -S
```

Expected: 只允许 `/api/v1/tasks/rules`、`/alerts/rules`、任务简称相关、历史文档或明确非旧能力库引用存在。

- [ ] Step 5: 调用独立 review，输入方案、diff、测试结果和风险点。
- [ ] Step 6: 若 review 发现真实问题，修复后重跑相关测试并再次 review，直到无阻断问题。

## 验收要求

- 前端菜单和路由不再暴露旧 `/rules`。
- 任务页所有无能力/补能力引导都指向 `AI 标注能力工作台`。
- 后端不再注册旧 `/api/v1/rules`。
- 任务简称配置 `/api/v1/tasks/rules` 正常保留。
- 飞书通知对做题 AI 错误能一句话说明“上游 502/超时/模型不可用”等具体问题。
- 同一类 AI provider 故障在持续异常时不再 5 分钟刷屏。
- 敏感字段不出现在飞书正文。
- 本地测试、构建、编译通过。
- 独立 review 无真实阻断问题。

## 验收目标

用户看到的平台只剩“AI 标注能力工作台”作为做题能力建设入口；飞书通知从“代码堆叠/重复刷屏”变成“少量关键通知 + 一句话讲清问题 + 排查编号可追踪”。

---

## 2026-07-08 追加范围：根治飞书误报与本机助手 Worker 日志归档

## 追加目标

- `WEB_LOGIN_RATE_LIMIT` 不再渲染成“平台内部服务异常”，改成“平台登录连续失败，被系统临时限流”。
- 本地测试默认不能真实发送飞书；只有显式设置 `AIDP_ALLOW_TEST_NOTIFICATION_SEND=true` 时才允许测试进程外发。
- 本机助手的 warning/error/critical 日志必须上报到平台中配置的 `worker_id`，不再写死为 `aidp-local-helper`。
- 本机助手上报必须保留 `stage/step/error_code/error_detail/retryable`，方便 Worker 日志和飞书摘要定位。

## 追加范围

- 后端通知：`backend/app/services/notification_service.py`
- 后端测试：`backend/tests/test_human_readable_notifications.py`
- 本机助手：`local-agent-source/host-launcher.ps1`
- 本机助手静态测试：`backend/tests/test_local_helper_integration_static.py`

## 追加不做什么

- 不开启 NAS 真实飞书发送。
- 不改真实 webhook、secret、做题 AI key。
- 不新增 Worker 日志表；复用现有 `/api/v1/workers/events` 和 `WorkerEvent`。
- 不改变自动做题提交和领取逻辑。

## 追加执行步骤

1. 先写 `WEB_LOGIN_RATE_LIMIT` 文案测试，要求标题、问题、影响、动作都说清楚登录限流。
2. 写测试环境防真发测试：pytest 进程中即使配置 `enabled=true/dryRun=false/webhookUrl`，未显式允许时也应跳过真实发送。
3. 写本机助手静态测试：`Invoke-AidpWorkerEventReport` 必须从 `Get-HelperSettings.worker_id` 读取 worker，而不是硬编码。
4. 实现通知服务：新增 `WEB_LOGIN_RATE_LIMIT` 错误码摘要；新增 `_test_network_send_blocked()` 防线；仅跳过网络发送，不阻断 dry-run 和配置保存。
5. 实现本机助手：新增 `Get-ConfiguredWorkerId`；上报 payload 使用配置 `worker_id`；保留错误码识别和结构化字段。
6. 跑定向测试：通知、人话文案、本机助手静态解析。
7. 跑全量验证、独立 review。
8. 部署 NAS，保持 `AIDP_NOTIFY_ENABLED=false`，验证容器内 `sendsNetwork=false` 和本机助手包可构建。

## 追加风险与回滚

- 风险：防真发逻辑影响测试中模拟真实发送的用例。控制：测试可通过 monkeypatch `_send_feishu_text` 或设置 `AIDP_ALLOW_TEST_NOTIFICATION_SEND=true`；生产不受影响。
- 风险：本机助手读取配置失败导致不上报。控制：失败时回退 `aidp-local-helper-<MachineName>`，同时本地日志仍写入。
- 回滚：回退 `notification_service.py`、`test_human_readable_notifications.py`、`host-launcher.ps1`、`test_local_helper_integration_static.py` 即可，不涉及数据库。

## 追加验证方式

```powershell
$env:PYTHONPATH='backend'
python -m pytest backend/tests/test_human_readable_notifications.py backend/tests/test_local_helper_integration_static.py -q
python -m compileall -q backend/app
pwsh -NoProfile -Command "$tokens=$null;$errors=$null;$null=[System.Management.Automation.Language.Parser]::ParseFile('local-agent-source/host-launcher.ps1',[ref]$tokens,[ref]$errors); if($errors.Count){$errors | ForEach-Object Message; exit 1}"
```

## 追加验收要求

- 飞书登录限流告警必须一眼看懂，不再出现“平台内部服务异常”。
- 测试环境默认不可能误发真实飞书。
- 本机助手 warning/error/critical 日志进入平台对应 `worker_id` 的 Worker 日志。
- NAS 部署后仍默认不外发飞书，除非后续单独开启。
