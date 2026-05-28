from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4


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
        return (
            f"【{self.severity}】{self.title}\n"
            f"对象：{self.subject}\n"
            f"原因：{self.reason}\n"
            f"时间：{self.occurred_at}\n"
            f"trace_id：{self.trace_id}\n"
            f"面板：{self.panel_url}"
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
