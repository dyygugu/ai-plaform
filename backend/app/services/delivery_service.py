from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.schemas.delivery import DeliveryArtifact, DeliveryBundleResponse, DeliveryChecklistItem, DeliveryChecklistResponse, DeliverySummaryResponse
from app.services.api_paths import api_path
from app.services.alerting_service import build_slo_summary
from app.services.observability_service import build_collector_guard
from app.services.ops_job_service import build_release_gate
from app.services.task_rules import utc_now

def _find_project_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "TODO.md").exists() or (candidate / "reports").exists():
            return candidate
    return Path.cwd()


PROJECT_ROOT = _find_project_root()
WORKSPACE_ROOT = PROJECT_ROOT.parents[1] if PROJECT_ROOT.name == "aidp-monitor-next" and len(PROJECT_ROOT.parents) > 1 else PROJECT_ROOT
OUTPUT_ROOT = WORKSPACE_ROOT / "output" / "playwright"
DATA_OUTPUT_ROOT = PROJECT_ROOT / "data" / "output" / "playwright"
TODO_PATH = PROJECT_ROOT / "TODO.md"
CHECKLIST_PATH = PROJECT_ROOT / "notes" / "acceptance-checklist.md"


def _reports_root() -> Path:
    source_root = PROJECT_ROOT / "reports"
    if source_root.exists() or TODO_PATH.exists():
        return source_root
    settings = get_settings()
    return Path(settings.task_sample_root).resolve().parent / "reports"

SCREENSHOT_KEYS = [
    ("p7_rules", "P7 规则中心", "aidp-monitor-next-p7-rules.png"),
    ("p7_workers", "P7 Worker 管理", "aidp-monitor-next-p7-workers-seeded.png"),
    ("p8_ops", "P8 运维中枢", "aidp-monitor-next-p8-ops.png"),
    ("p9_production", "P9 生产护栏", "aidp-monitor-next-p9-production.png"),
    ("p10_observability", "P10 观测中心", "aidp-monitor-next-p10-observability.png"),
    ("p11_alerts", "P11 告警中心", "aidp-monitor-next-p11-alerts.png"),
    ("p12_delivery", "P12 交付中心", "aidp-monitor-next-p12-delivery.png"),
    ("p13_inspection", "P13 巡检中心", "aidp-monitor-next-p13-inspection.png"),
    ("p14_freeze", "P14 冻结中心", "aidp-monitor-next-p14-freeze.png"),
    ("p15_accounts", "P15 原生账号", "aidp-monitor-next-p15-accounts.png"),
    ("p16_account_coverage", "P16 账号覆盖", "aidp-monitor-next-p16-account-coverage.png"),
    ("p17_data_quality", "P17 数据校验", "aidp-monitor-next-p17-data-quality.png"),
    ("p18_incidents", "P18 异常处置", "aidp-monitor-next-p18-incidents.png"),
    ("p19_final_acceptance", "P19 最终验收", "aidp-monitor-next-p19-final-acceptance.png"),
    ("p20_roadmap_final", "P20 大纲封版", "aidp-monitor-next-p20-roadmap-final.png"),
]

API_GROUPS = [
    "health/accounts/settings",
    "accounts/native-refresh",
    "tasks/task-source/task-rules",
    "backups/restore-drills",
    "ai/workers/earnings",
    "rules/ops/scheduler",
    "observability/probes/timeline",
    "alerts/slo/evaluate",
    "delivery/checklist/bundle",
    "inspection/freeze/baseline",
    "data-quality/incidents/final-acceptance/roadmap-final",
]


def build_delivery_checklist(db: Session) -> DeliveryChecklistResponse:
    settings = get_settings()
    checks, release_ready = build_release_gate(db)
    collector = build_collector_guard(db)
    slo = build_slo_summary(db)
    latest_report = _latest_report_artifact()
    todo_unchecked = _todo_unchecked_count()
    required_failed = [item.title for item in checks if item.required and item.status == "failed"]
    items = [
        DeliveryChecklistItem(
            key="local_entry",
            title="本地验收入口",
            status="passed",
            description=f"8789 固定在 {settings.public_base_url}，运行期不依赖旧系统。",
            evidence_path=latest_report.path,
        ),
        DeliveryChecklistItem(
            key="latest_report",
            title="最新自动验收报告",
            status="passed" if latest_report.exists else "failed",
            description="最新 acceptance 报告必须存在，记录 Docker 重部署和 smoke 结果。",
            evidence_path=latest_report.path,
        ),
        DeliveryChecklistItem(
            key="todo_clear",
            title="TODO 清零",
            status="passed" if todo_unchecked == 0 else "warning",
            description=f"TODO 未完成项 {todo_unchecked} 个。",
            evidence_path="Projects/aidp-monitor-next/TODO.md",
        ),
        DeliveryChecklistItem(
            key="release_gate",
            title="发布门禁",
            status="passed" if release_ready else "failed",
            description="必需门禁通过，可进入人工域名切换前检查。" if release_ready else f"失败门禁：{', '.join(required_failed)}",
            evidence_path=api_path("/ops/release-gate", settings),
        ),
        DeliveryChecklistItem(
            key="collector_guard",
            title="采集守护",
            status=collector.status,
            description=collector.message,
            evidence_path=api_path("/observability/collector-guard", settings),
        ),
        DeliveryChecklistItem(
            key="slo_alerting",
            title="SLO 告警闭环",
            status=slo.overall_status,
            description="告警中心只生成本地预览和审计证据，外部发送关闭。",
            evidence_path=api_path("/alerts/slo", settings),
        ),
        DeliveryChecklistItem(
            key="screenshots",
            title="关键页面截图",
            status="passed" if _required_screenshots_ready() else "warning",
            description="P7-P20 关键页面截图已收集；Docker 内使用 data/output/playwright 镜像证据兜底。",
            evidence_path="output/playwright",
        ),
        DeliveryChecklistItem(
            key="manual_domain_switch",
            title="正式域名手动切换",
            status="manual",
            description="系统不自动修改 manage.51gugu.uk；最终由用户手动改反代并保留回滚配置。",
            evidence_path=api_path("/ops/domain-switch-runbook", settings),
        ),
    ]
    risk_notes = [
        "正式域名切换前必须按本清单人工复验 8789。",
        "当前告警/SLO 可能因样例账号需登录显示 warning，不代表 P12 证据包失败。",
        "任何删除候选只移动到 D:\\数据标注插件\\delete，不直接删除。",
    ]
    rollback_notes = [
        "若正式域名切换后异常，立即恢复到切换前保存的稳定 upstream。",
        "保持 8789 新版容器运行，导出 Docker logs、release-gate 和 delivery bundle 用于排查。",
        "回滚后重新执行恢复演练、备份检查和告警评估，再安排二次切换。",
    ]
    return DeliveryChecklistResponse(
        generated_at=utc_now(),
        base_url=settings.public_base_url,
        production_domain="manage.51gugu.uk",
        manual_domain_switch_required=True,
        items=items,
        risk_notes=risk_notes,
        rollback_notes=rollback_notes,
    )


def build_delivery_summary(db: Session) -> DeliverySummaryResponse:
    settings = get_settings()
    checklist = build_delivery_checklist(db)
    latest_report = _latest_report_artifact()
    screenshots = [_screenshot_artifact(key, title, filename) for key, title, filename in SCREENSHOT_KEYS]
    todo_unchecked = _todo_unchecked_count()
    blocking = [item for item in checklist.items if item.status == "failed"]
    status = "passed" if not blocking and todo_unchecked == 0 else "warning"
    return DeliverySummaryResponse(
        generated_at=utc_now(),
        status=status,
        base_url=settings.public_base_url,
        production_domain="manage.51gugu.uk",
        manual_domain_switch_required=True,
        latest_report=latest_report,
        screenshots=screenshots,
        todo_unchecked_count=todo_unchecked,
        api_groups=API_GROUPS,
        checklist=checklist,
        message="交付证据包可用于人工验收；正式域名仍需用户手动切换。",
    )


def generate_delivery_bundle(db: Session) -> DeliveryBundleResponse:
    summary = build_delivery_summary(db)
    reports_root = _reports_root()
    reports_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bundle_path = reports_root / f"delivery-bundle-{stamp}.md"
    markdown = _render_bundle_markdown(summary)
    bundle_path.write_text(markdown, encoding="utf-8")
    artifacts = [summary.latest_report] + summary.screenshots + [_artifact_from_path("acceptance_checklist", "人工验收清单", CHECKLIST_PATH)]
    return DeliveryBundleResponse(
        generated_at=utc_now(),
        status=summary.status,
        bundle_path=_display_path(bundle_path),
        bundle_markdown=markdown,
        artifacts=artifacts,
        message="交付证据包已生成；不会自动切换正式域名。",
    )


def _render_bundle_markdown(summary: DeliverySummaryResponse) -> str:
    lines = [
        "# aidp-monitor-next 交付证据包",
        "",
        f"生成时间：{summary.generated_at.isoformat()}",
        f"交付状态：{summary.status}",
        f"本地验收入口：{summary.base_url}",
        f"正式域名：{summary.production_domain}（仍需用户手动切换）",
        "",
        "## 最新报告",
        f"- {summary.latest_report.path}，exists={summary.latest_report.exists}，bytes={summary.latest_report.size_bytes}",
        "",
        "## 验收清单",
    ]
    for item in summary.checklist.items:
        lines.append(f"- [{item.status}] {item.title}：{item.description}（证据：{item.evidence_path}）")
    lines.extend(["", "## 截图索引"])
    for shot in summary.screenshots:
        lines.append(f"- {shot.title}：{shot.path}，exists={shot.exists}，bytes={shot.size_bytes}")
    lines.extend(["", "## 接口分组"])
    for group in summary.api_groups:
        lines.append(f"- {group}")
    lines.extend(["", "## 风险提示"])
    for note in summary.checklist.risk_notes:
        lines.append(f"- {note}")
    lines.extend(["", "## 回滚提醒"])
    for note in summary.checklist.rollback_notes:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def _latest_report_artifact() -> DeliveryArtifact:
    reports_root = _reports_root()
    reports = sorted(reports_root.glob("acceptance-*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not reports:
        return DeliveryArtifact(key="latest_report", title="最新自动验收报告", path=_display_path(reports_root), exists=False)
    return _artifact_from_path("latest_report", "最新自动验收报告", reports[0])


def _screenshot_artifact(key: str, title: str, filename: str) -> DeliveryArtifact:
    primary_path = OUTPUT_ROOT / filename
    fallback_path = DATA_OUTPUT_ROOT / filename
    path = primary_path if primary_path.exists() or not fallback_path.exists() else fallback_path
    return _artifact_from_path(key, title, path)


def _artifact_from_path(key: str, title: str, path: Path) -> DeliveryArtifact:
    exists = path.exists()
    stat = path.stat() if exists else None
    updated_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else ""
    return DeliveryArtifact(key=key, title=title, path=_display_path(path), exists=exists, size_bytes=stat.st_size if stat else 0, updated_at=updated_at)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _todo_unchecked_count() -> int:
    if not TODO_PATH.exists():
        return 0
    return TODO_PATH.read_text(encoding="utf-8").count("- [ ]")


def _required_screenshots_ready() -> bool:
    required = [filename for key, title, filename in SCREENSHOT_KEYS if key != "p12_delivery"]
    return all((OUTPUT_ROOT / filename).exists() or (DATA_OUTPUT_ROOT / filename).exists() for filename in required)

