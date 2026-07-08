# AIDP 3D HTTP 自动做题链路

## 目标

把 3D Rubric 任务 `7658232870117527347` 接入平台原生自动做题链路，实现：

- 使用 `qwen3-vl-plus` 生成结构化 3D 标注答案。
- 通过平台后端发起真实 HTTP：trial 只执行 `SubmitTempItemAnswer`；production 才执行 `SubmitTempItemAnswer -> SubmitItemAndReceive -> search_item/category 回读`。
- 多账号并行、账号内串行，每个 tick 每账号最多提交 1 题。
- 后台循环持续执行，停止按钮阻止下一轮 tick。
- ledger 防止重复提交和未知状态自动重提。
- 账号级错误写入平台 worker 日志与提交证据，错误码明确。

## 范围

- 任务：`Blender_3D 人标支持-0703 / 7658232870117527347`。
- 执行器：`3d_rubric`，接入现有 `/task-auto-runs` 通用自动运行框架。
- AI：读取 `data/ai-runtime-config.json` 的 `task_ai` provider，模型固定为 `qwen3-vl-plus`。
- 入口：任务生产工作台的“AI 自动做题”区。

## 不做什么

- 不恢复旧 P7 规则版本。
- 不把“题型能力库”旧逻辑搬回前台。
- 不新增外部脚本作为生产入口。
- 不绕过平台停止按钮做不可控死循环。
- 不在缺图片、低置信度、rubric 不一致、payload 空、回读异常时继续提交下一题。

## 实现文件

- `backend/app/services/aidp_3d_http_answer_service.py`
- `backend/app/services/task_auto_run_service.py`
- `backend/app/api/v1/routes/task_auto_runs.py`
- `backend/app/schemas/worker.py`
- `frontend/src/pages/TasksPage.tsx`
- `backend/tests/test_aidp_3d_http_answer_service.py`
- `backend/tests/test_task_auto_run_3d_adapter.py`

## HTTP 链路

1. 从运行账号读取 `cookie/referer/userAgent`。
2. 如果账号没有 mark 页 `templateID`，使用已验证 3D 模板 `7658120776411467566`。
3. POST `/dispatcher/search_item/category` 读取当前 live item。
4. 校验题面必须包含：
   - `ref_img.tos_url`
   - `latest_screenshot.tos_url`
   - 至少 3 个 `artifact_views.*.tos_url`
   - `rubrics.rubrics`
5. 调 `qwen3-vl-plus`，要求只返回 JSON。
6. 校验 qwen 输出：
   - `confidence == high`
   - `rubrics_reasonable` 是布尔值
   - rubric 数量和 id 顺序完全一致
   - `unsatisfied` 必须有原因
   - `S1/S2/A` 分数在 1-5 且原因非空
7. 构造 `SubmitTempItemAnswer` payload：
   - `dataMap == data`
   - `rubricResults` 非空
   - `rubricsReason/s1Reason/s2Reason/aReason` 非空
8. POST `/api/dispatch/SubmitTempItemAnswer`。
9. 如果 `ability_run_mode=trial`：到此停止，ledger 标记 `temp_saved`，不调用正式提交。
10. 如果 `ability_run_mode=production`：先重校验 Step4 production gate、账号提交上限和速率限制，再 POST `/api/dispatch/SubmitItemAndReceive`。
11. 校验：
    - HTTP 200
    - `SubmitItemResponse.BaseResp.StatusCode == 0`
    - `ReceiveResponse.BaseResp.StatusCode == 0`
    - `SubmitItemResponse.Errors == []`
    - `AnsVersions` 包含当前 item 且 `AnsModified != false`
12. 再读 `/dispatcher/search_item/category`，确认当前题不再是刚提交的 item；如有 receive next item，则和 search 回读一致。

## Ledger

目录：`data/production-runs/3d-rubric-ledger/{task_id}/{account_user_id}/{item_id}.json`

状态：

- `in_progress`：提交前先写入。
- `temp_saved`：trial 或不正式提交链路已暂存成功，允许后续 production 重新进入同题正式提交。
- `submitted`：提交和回读成功后写入。
- `failed`：AI、图片、payload、暂存前后确定失败。
- `blocked_unknown`：发现旧 `in_progress`，或正式提交/回读状态不明。

规则：

- `submitted` 禁止重复提交。
- 旧 `in_progress` 不自动重提，转为 `blocked_unknown`。
- 正式提交请求发出后，任何提交响应、AnsVersions、receive/search 回读或鉴权异常都按状态不明处理，写 `blocked_unknown`，隔离账号并写明确错误。
- 暂存失败、AI 失败、图片缺失、payload 校验失败发生在正式提交前，写 `failed`，允许人工确认后再处理。

## 多账号循环

- Step4 trial：`POST /api/v1/task-abilities/{task_id}/trial-run`，只暂存不正式提交，成功后状态为 `completed`。
- Step4 production：`POST /api/v1/task-abilities/{task_id}/production-run`，首轮真实提交成功后进入后台循环。
- 旧 `/api/v1/task-auto-runs/start` 生产入口保持受保护，不作为公网自动做题入口。
- 后台循环：由 Step4 production 内部启动通用 worker。
- 停止：`/api/v1/task-auto-runs/runs/{run_id}/worker/stop` + `/stop`
- adapter：`TaskAutoRun3DRubricAdapter`
- 并发：账号间最多 5 并行；账号内每 tick 只做 1 题。
- 结果归并：3D 使用 `as_completed` 处理账号结果，慢账号不阻塞其它账号状态落盘。
- 无当前题：账号进入 `waiting_items`，后台继续下一轮等待。
- 单账号失败：该账号 `isolated_failed`，不拖垮其它账号。
- 全账号失败：run 标记 `blocked`，写平台 worker 日志，并让后台循环停止，避免同一故障重复刷告警。

## 平台操作链路

1. 进入 `/tasks`，选择 3D 任务 `7658232870117527347`。
2. 打开“任务操作台”，平台识别为 `3d_rubric` 内建 HTTP 执行器，不再要求旧题型能力库发布。
3. 点击“选择当前可自动运行账号”，默认只选 Cookie 正常且有当前题的账号。
4. 点击“启动前自检”，只检查账号、执行器和证据目录，不暂存、不提交。
5. 点击“启动自动做题”，后端创建 run 并立即执行首个 tick。
6. 先执行 trial：读当前题、qwen 判题、暂存、写 `temp_saved` 证据；不正式提交。
7. trial 状态 `completed` 且 Step4 gate 放行后，执行 production：每个账号读当前题、qwen 判题、暂存、正式提交、回读确认、写 ledger 和证据。
8. production 每轮都会重新校验 Step4 production gate、`production_max_items_per_account` 和 `rate_limit_per_minute`。
9. 首个 production tick 成功后后台 worker 进入循环；每轮每账号最多 1 题，直到无题、达到上限、停止或异常隔离。
10. 需要停机时先点“立即停止”；停止后不会再进入下一轮 tick。

## 开发到自主做题快速路径

1. 证据先行：必须有学习包或实测 HTTP 证据证明题面读取、暂存、提交和回读。
2. 写 task-specific writer：只放本题型 prompt、字段映射、payload 校验和回读校验。
3. 接入通用 `task_auto_runs` adapter：复用 start/preflight/tick/stop，不新增生产脚本入口。
4. 先加测试：payload、AI 输出、提交响应、ledger、adapter tick、异常分流。
5. 前端只放在任务操作台：内建执行器可绕过旧题型能力库，但必须有明确任务 ID 白名单。
6. 实际部署使用 `frontend/dist`：如果当前包没有完整前端构建环境，必须同步更新 dist bundle 或在完整源仓库 build 后再发布。
7. 小批量验收：先 1 号 1-5 题，再多号并行，再打开后台循环；任一异常先停该账号。

## Worker 日志

平台本机执行器：`platform-worker`

3D 相关错误码：

- `NO_CURRENT_ITEM`
- `MISSING_REQUIRED_IMAGE`
- `LOW_CONFIDENCE`
- `AI_RESPONSE_INVALID`
- `AI_PROVIDER_TIMEOUT`
- `AI_PROVIDER_502`
- `TASK_PAGE_AUTH_EXPIRED`
- `SUBMIT_FAILED`
- `READBACK_MISMATCH`
- `DUPLICATE_SUBMITTED`
- `LEDGER_IN_PROGRESS_UNKNOWN`
- `WORKER_EXCEPTION`

账号级失败默认记为 warning，避免同类错误疯狂打飞书；整轮失败才记 error。

## 新题型复用步骤

1. 先录制学习包，拿到真实题面、暂存请求、成功响应和回读证据。
2. 抽象 task-specific writer，只保留该题型字段映射、AI prompt、payload 校验。
3. 接入 `task_auto_runs` adapter，不新增独立生产脚本入口。
4. 加 ledger：`task_id/account_user_id/item_id` 维度防重复。
5. 先写测试：payload shape、AI 输出校验、submit/receive 回读、ledger 阻断、adapter tick。
6. 前端只在任务操作台放行对应执行器，不恢复旧规则库。
7. 本地验证后再做小批量真实提交，最后再放开后台循环。
