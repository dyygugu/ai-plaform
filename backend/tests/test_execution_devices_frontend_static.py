from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"


def _read(relative: str) -> str:
    return (FRONTEND / relative).read_text(encoding="utf-8")


def test_execution_device_management_static_contract() -> None:
    layout = _read("layouts/AppLayout.tsx")
    workers = _read("pages/WorkersPage.tsx")

    assert "多 Worker" in layout
    assert "执行设备管理" in workers
    assert "本机助手套件" in workers
    assert "设备总览" in workers
    assert "需要处理" in workers
    assert "搜索设备名 / IP / 备注" in workers
    assert "批量检查更新" in workers
    assert "批量暂停接收任务" in workers
    assert "批量恢复接收任务" in workers
    assert "批量设置并发" in workers
    assert "设备回收站" in workers
    assert "Worker 生产调度控制" in workers
    assert "选择生产账号" in workers
    assert "选择任务" in workers
    assert "账号任务组租约" in workers
    assert "命令队列" in workers
    assert "preflight_only" in workers
    assert "做题链路事件上报规范" in workers
    assert "恢复" in workers
    assert "pageSizeOptions: [20, 50, 100]" in workers
    assert 'size="small"' in workers
    assert 'fixed: "right"' in workers
    assert "删除" in workers
    assert "deleteExecutionDevice" in _read("api/client.ts")
    assert "restoreExecutionDevice" in _read("api/client.ts")
    assert all(title in workers for title in ["设备", "状态", "当前运行", "版本", "并发", "最近活动", "需处理", "操作"])
    assert "高级调试" in workers
    assert "启动任务" not in workers


def test_production_control_drawer_static_contract() -> None:
    tasks = _read("pages/TasksPage.tsx")
    client = _read("api/client.ts")

    assert "生产控制" in tasks
    assert "任务操作台" in tasks
    assert "自动生产" in tasks
    assert "能力版本" in tasks
    assert "Prompt 版本" in tasks
    assert "做题账号" in tasks
    assert "题目范围" in tasks
    assert "执行方式" in tasks
    assert all(label in tasks for label in ["平台", "平台+设备", "设备"])
    assert "执行设备" in tasks
    assert "本次最多处理" in tasks
    assert "无限" not in tasks
    assert "连续失败" in tasks
    assert "去能力工作台启动生产" in tasks
    assert "去 AI 标注能力工作台制作" in tasks
    assert "题型能力库" not in tasks
    assert 'href="/rules"' not in tasks
    assert "启动自动做题" not in tasks
    assert "批准首题审核" not in tasks
    assert "正式提交首题并进入自动做题" not in tasks
    assert "startAutoTaskRun = async" not in tasks
    assert "startTaskAutoRun(" not in tasks
    assert "prepareBon8FirstItemReviewWithAi" not in tasks
    assert "approveBon8ProductionRun" not in tasks
    assert "submitBon8FirstItem" not in tasks
    assert "取消" in tasks
    assert "二次确认" not in tasks
    assert "fetchTaskAutoProductionStatus" in client
    assert "startTaskProduction" not in client
    assert "function startTaskAutoRun(" not in client
    assert "startBon8Production" not in client
    assert "prepareBon8FirstItemReviewWithAi" not in client
    assert "approveBon8ProductionRun" not in client
    assert "submitBon8FirstItem" not in client
    assert "resumeAutoAnswerRun" in client
    assert "fetchExecutionDevicesForProduction" in client


def test_account_task_open_preopens_window_before_async_request_for_chrome() -> None:
    dashboard = _read("pages/DashboardPage.tsx")
    accounts = _read("pages/AccountsPage.tsx")

    assert dashboard.index('window.open("about:blank", "_blank")') < dashboard.index("await openAccountTarget")
    assert accounts.index('window.open("about:blank", "_blank")') < accounts.index("await openAccountTarget")
    assert "popup.location.replace(result.open_url)" in dashboard
    assert "popup.location.replace(result.open_url)" in accounts
