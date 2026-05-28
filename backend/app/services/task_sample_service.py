import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.core.settings import get_settings

SENSITIVE_KEYS = {"cookie", "cookies", "authorization", "token", "api_key", "apiKey", "password", "secret"}
USER_ID_PATTERN = re.compile(r"\b\d{16,22}\b")


def redact_value(key: str, value: Any) -> Any:
    if key.lower() in {item.lower() for item in SENSITIVE_KEYS}:
        return "[REDACTED]"
    if isinstance(value, str):
        return USER_ID_PATTERN.sub(lambda match: f"{match.group(0)[:4]}***{match.group(0)[-4:]}", value)
    return value


def redact_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {key: redact_payload(redact_value(key, value)) for key, value in payload.items()}
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    return payload


def build_placeholder_sample(source_account_user_id: str) -> dict[str, Any]:
    return {
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "sourceAccountUserId": source_account_user_id,
        "redacted": True,
        "sampleType": "placeholder-until-real-readonly-client",
        "tasks": [
            {
                "rawTaskName": "RFT人标_美观度（6.5万）7634515789236309806",
                "taskStatusRaw": "等待真实样本",
                "pendingRaw": "页面原样采集",
            }
        ],
        "notes": "真实只读采集接入旧版 Cookie/HTTP 客户端后覆盖该脱敏样本。",
    }


def save_redacted_task_sample(payload: Optional[Any] = None, source_account_user_id: Optional[str] = None) -> Path:
    settings = get_settings()
    source = source_account_user_id or settings.task_source_account_user_id
    sample_root = Path(settings.task_sample_root)
    sample_root.mkdir(parents=True, exist_ok=True)
    sample = payload if payload is not None else build_placeholder_sample(source)
    redacted = redact_payload(sample)
    latest_path = sample_root / "task-page-latest.json"
    timestamp_path = sample_root / f"task-page-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    text = json.dumps(redacted, ensure_ascii=False, indent=2)
    latest_path.write_text(text, encoding="utf-8")
    timestamp_path.write_text(text, encoding="utf-8")
    return latest_path

