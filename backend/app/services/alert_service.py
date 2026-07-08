from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4


HUMAN_LEVELS = {
    "debug": ("提醒", "不一定要立刻处理"),
    "info": ("提醒", "不一定要立刻处理"),
    "warn": ("提醒", "不一定要立刻处理"),
    "warning": ("提醒", "不一定要立刻处理"),
    "error": ("一般", "可以稍后处理"),
    "failed": ("紧急", "必须立刻处理"),
    "critical": ("紧急", "必须立刻处理"),
}


@dataclass(frozen=True)
class AlertMessage:
    title: str
    severity: str
    subject: str
    reason: str
    occurred_at: str
    trace_id: str
    panel_url: str

    def render_feishu_text(self) -> str:
        return render_human_readable_alert_text(
            title=self.title,
            severity=self.severity,
            problem=f"{self.subject}：{self.reason}",
            impact="相关任务可能无法继续采集或完成告警闭环。",
            action=f"打开告警中心查看{self.subject}，并按提示处理。",
            occurred_at=self.occurred_at,
            trace_id=self.trace_id,
            panel_url=self.panel_url,
        )


def build_alert_message(title: str, severity: str, subject: str, reason: str, panel_url: str) -> AlertMessage:
    return AlertMessage(
        title=title,
        severity=severity,
        subject=subject,
        reason=reason,
        occurred_at=datetime.now(timezone.utc).isoformat(),
        trace_id=uuid4().hex,
        panel_url=panel_url,
    )


def human_level_label(severity: str) -> tuple[str, str]:
    return HUMAN_LEVELS.get(str(severity or "error").lower(), HUMAN_LEVELS["error"])


def render_human_readable_alert_text(
    *,
    title: str,
    severity: str,
    problem: str,
    impact: str,
    action: str,
    occurred_at: str,
    trace_id: str,
    panel_url: str,
    technical_event: Optional[str] = None,
) -> str:
    label, handling = human_level_label(severity)
    lines = [
        f"【{label}】{title}",
        f"处理级别：{handling}",
        f"问题出在：{problem}",
        f"影响：{impact}",
        f"现在要做：{action}",
        f"时间：{occurred_at}",
        f"排查编号：trace_id={trace_id}",
        f"面板：{panel_url}",
    ]
    if technical_event:
        lines.append(f"技术事件：{technical_event}")
    return "\n".join(lines)
