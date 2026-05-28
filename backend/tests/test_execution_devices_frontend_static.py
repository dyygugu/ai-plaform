from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"


def _read(relative: str) -> str:
    return (FRONTEND / relative).read_text(encoding="utf-8")


def test_execution_device_management_static_contract() -> None:
    layout = _read("layouts/AppLayout.tsx")
    workers = _read("pages/WorkersPage.tsx")

    assert "执行设备管理" in layout
    assert "本机助手套件" in workers
    assert "设备总览" in workers
    assert "需要处理" in workers
    assert "搜索设备名 / IP / 备注" in workers
    assert "批量检查更新" in workers
    assert "批量暂停接收任务" in workers
    assert "批量恢复接收任务" in workers
    assert "批量设置并发" in workers
    assert "设备回收站" in workers
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
    assert "任务操作台" not in tasks
    assert "自动生产" in tasks
    assert "能力版本" in tasks
    assert "Prompt 版本" in tasks
    assert "做题账号" in tasks
    assert "题目范围" in tasks
    assert "执行方式" in tasks
    assert all(label in tasks for label in ["平台", "平台+设备", "设备"])
    assert "执行设备" in tasks
    assert "本次最多处理" in tasks
    assert "无限" in tasks
    assert "连续失败" in tasks
    assert "启动生产" in tasks
    assert "取消" in tasks
    assert "二次确认" not in tasks
    assert "fetchTaskAutoProductionStatus" in client
    assert "startTaskProduction" in client
    assert "resumeAutoAnswerRun" in client
    assert "fetchExecutionDevicesForProduction" in client
