# aidp-monitor-next

AIDP 管理看板下一版工程骨架。该目录用于新主控服务开发，不影响旧版 `Projects/aidp-monitor` 的运行与线上 `manage.51gugu.uk`。

## P0 范围

- 后端：FastAPI、Pydantic Settings、SQLAlchemy/Alembic、PostgreSQL、Redis 配置骨架。
- 前端：React、TypeScript、Vite、Ant Design、React Router 骨架。
- 部署：测试环境 Docker Compose；正式 Cloudflare Tunnel/反代最后切换。
- 健康检查：`/api/v1/health` 返回环境、版本和依赖配置摘要。

## 快速启动

```powershell
cd Projects/aidp-monitor-next
copy .env.example .env
docker compose -f infra/docker-compose.dev.yml up --build -d
```

本地测试访问：

- 前端看板：`http://127.0.0.1:8789`
- 后端 API：`http://127.0.0.1:8789/api/v1/health`

注意：`manage.51gugu.uk` 当前仍指向老版本，等人工验收通过后再手动切换反代。

前端开发：

```powershell
cd Projects/aidp-monitor-next/frontend
npm install
npm run dev
```

后端开发：

```powershell
cd Projects/aidp-monitor-next/backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8787
```

## 双内置 AI 权限与上下文

- 系统处理 AI：最高权限 + 前置上下文 + 护栏，负责内置聊天、运维排障、系统配置、事故评估和高危动作确认。
- 做题 AI：受限权限，只能在做题/评分草稿链路调用，用于生成脱敏题面的答案/理由草稿，不处理运维、密钥、删除、切域名或真实提交。
- 双 AI 配置入口：`GET/PUT /api/v1/ai/config` 和前端 `/ai`，可分别填写系统 AI、做题 AI 的 Base URL、API Key、模型和超时时间。
- 做题 AI 前置资料：系统 AI 可为做题 AI 维护前置提示词、skills 和 md 文件路径，这些只会在做题草稿调用中注入。
- 系统前置上下文：`backend/app/prompts/incident_ai_operator.md`，用于让无聊天上下文的系统 AI provider 先恢复项目功能地图、职责边界、执行顺序和高危动作。
- 事故评估入口：`POST /api/v1/ai/incidents/review`；未配置系统 AI provider 时走本地护栏策略，配置 provider 时也必须先注入前置上下文。
- 高危动作：真实提交、删除/覆盖数据、改密钥、切正式域名、批量停用、改安全策略、清日志/备份等，只能生成确认项，除非存在二次确认或明确授权开关。
## AI 高危动作确认流

- 确认队列：`GET /api/v1/ai/confirmations` 查看待确认、已授权、已驳回的 AI 高危动作。
- 人工授权：`POST /api/v1/ai/confirmations/{id}/approve` 需要输入返回的 `confirm_phrase`，只记录授权和审计，不自动执行破坏性动作。
- 人工驳回：`POST /api/v1/ai/confirmations/{id}/reject` 会写审计并阻止该高危动作继续执行。
- 前端入口：`/ai` 页面展示高危动作确认队列、确认短语和批准/驳回操作。
## 评分题生产闭环

- 首版支持题型：`rft_aesthetic_v1` / `RFT人标_美观度`；未知题型默认 `unsupported_paused`。
- API：`GET /api/v1/score-loop/summary`、`POST /api/v1/score-loop/cases/capture`、`POST /api/v1/score-loop/cases/{id}/draft`、`POST /api/v1/score-loop/cases/{id}/review`。
- 安全链路：采集脱敏题面 -> AI 草稿 -> 人工确认 -> 真实提交进入高危确认队列。
- 自动提交闸门：需要 3 个稳定人工样本；即使闸门开启，真实提交仍必须经过 AI 高危动作确认流。
- 前端入口：`/ai` 页面展示评分题生产闭环、稳定样本、自动提交闸门和样本操作。