import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus
from uuid import uuid4

import requests

from app.core.settings import get_settings
from app.schemas.notification import NotificationConfigRead, NotificationConfigUpdate, NotificationSendResponse
from app.services.task_rules import utc_now


LEVEL_RANK = {"debug": 0, "info": 1, "warn": 2, "warning": 2, "error": 3, "critical": 4}
DEFAULT_EVENTS = [
    "backend.error",
    "backend.unhandled_exception",
    "audit.error",
    "worker.error",
    "alert.evaluation.failed",
    "alert.evaluation.warning",
]
SECRET_PATTERN = re.compile(r"(cookie|api[_-]?key|token|secret|password|authorization|主密钥|恢复码)\s*[:=]\s*[^\s,;，]+", re.IGNORECASE)
LAST_SENT: dict[str, float] = {}


def read_notification_config() -> dict[str, Any]:
    settings = get_settings()
    config = {
        "enabled": bool(settings.notify_enabled),
        "provider": "feishu-webhook",
        "webhookUrl": settings.feishu_webhook_url,
        "secret": settings.feishu_secret,
        "minLevel": settings.notify_min_level or "warn",
        "events": _split_events(settings.notify_events) or DEFAULT_EVENTS,
        "dryRun": bool(settings.notify_dry_run),
        "cooldownSec": max(30, int(settings.notify_cooldown_seconds or 300)),
        "source": "env",
    }
    path = _resolve_path(settings.notification_config_path)
    file_config = _load_json(path)
    if isinstance(file_config, dict):
        for key, value in file_config.items():
            config[key] = value
        config["source"] = "file+env" if path.exists() else "env"
    if settings.feishu_webhook_url:
        config["webhookUrl"] = settings.feishu_webhook_url
        config["enabled"] = True
        config["source"] = "env"
    if settings.feishu_secret:
        config["secret"] = settings.feishu_secret
    if settings.notify_dry_run:
        config["dryRun"] = True
        config["enabled"] = True
    config["events"] = _normalize_events(config.get("events"))
    config["cooldownSec"] = max(30, _int(config.get("cooldownSec"), settings.notify_cooldown_seconds or 300))
    config["configPath"] = str(path)
    return config


def get_notification_config_status() -> NotificationConfigRead:
    config = read_notification_config()
    enabled = bool(config.get("enabled"))
    webhook = str(config.get("webhookUrl") or "").strip()
    dry_run = bool(config.get("dryRun"))
    sends_network = bool(enabled and webhook and not dry_run)
    return NotificationConfigRead(
        enabled=enabled,
        provider=str(config.get("provider") or "feishu-webhook"),
        webhook_url=webhook,
        webhook_configured=bool(webhook),
        secret_configured=bool(str(config.get("secret") or "").strip()),
        min_level=str(config.get("minLevel") or "warn"),
        events=_normalize_events(config.get("events")),
        dry_run=dry_run,
        cooldown_seconds=int(config.get("cooldownSec") or 300),
        sends_network=sends_network,
        config_path=str(config.get("configPath") or ""),
        source=str(config.get("source") or "env"),
        message="飞书错误通知已启用。" if sends_network else "飞书错误通知未实际发送：请配置 webhook 或关闭 dry-run。",
    )


def update_notification_config(payload: NotificationConfigUpdate) -> NotificationConfigRead:
    path = _resolve_path(get_settings().notification_config_path)
    existing = _load_json(path)
    config = existing if isinstance(existing, dict) else {}
    webhook_url = payload.webhook_url.strip() if payload.webhook_url is not None else str(config.get("webhookUrl") or "").strip()
    secret = payload.secret.strip() if payload.secret is not None else str(config.get("secret") or "").strip()
    new_config = {
        "enabled": bool(payload.enabled),
        "provider": "feishu-webhook",
        "webhookUrl": webhook_url,
        "secret": secret,
        "minLevel": _normalize_level(payload.min_level),
        "events": _normalize_events(payload.events) or DEFAULT_EVENTS,
        "dryRun": bool(payload.dry_run),
        "cooldownSec": max(30, int(payload.cooldown_seconds or 300)),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(new_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return get_notification_config_status()


def send_error_notification(
    event: str,
    level: str,
    message: str,
    data: Optional[dict[str, Any]] = None,
    trace_id: str = "",
    force: bool = False,
) -> NotificationSendResponse:
    config = read_notification_config()
    trace = trace_id or uuid4().hex
    normalized_level = _normalize_level(level)
    normalized_event = event or "backend.error"
    if not bool(config.get("enabled")):
        return _result(False, False, True, False, normalized_level, normalized_event, trace, "通知未启用。")
    if not force and not _level_allowed(normalized_level, str(config.get("minLevel") or "warn")):
        return _result(False, False, True, False, normalized_level, normalized_event, trace, "低于通知等级阈值。")
    events = _normalize_events(config.get("events"))
    if not force and events and normalized_event not in events:
        return _result(False, False, True, False, normalized_level, normalized_event, trace, "事件未列入通知白名单。")
    key = f"{normalized_event}|{normalized_level}|{message[:180]}"
    cooldown = int(config.get("cooldownSec") or 300)
    now = time.time()
    if not force and key in LAST_SENT and now - LAST_SENT[key] < cooldown:
        return _result(False, False, True, False, normalized_level, normalized_event, trace, "命中飞书通知冷却窗口。")
    LAST_SENT[key] = now
    text = _render_message(normalized_event, normalized_level, message, trace, data)
    if bool(config.get("dryRun")):
        return _result(True, False, False, True, normalized_level, normalized_event, trace, "dry-run：已生成飞书通知文本但未发送。")
    webhook = str(config.get("webhookUrl") or "").strip()
    if not webhook:
        return _result(False, False, True, False, normalized_level, normalized_event, trace, "飞书 webhook 未配置。")
    try:
        status_code = _send_feishu_text(webhook, str(config.get("secret") or ""), text)
    except Exception as exc:  # noqa: BLE001 - notification failure must not mask original error.
        return _result(False, False, False, False, normalized_level, normalized_event, trace, f"飞书发送失败：{_redact(str(exc))}")
    return _result(True, True, False, False, normalized_level, normalized_event, trace, "飞书通知已发送。", status_code=status_code)


def send_test_notification(send: bool) -> NotificationSendResponse:
    return send_error_notification(
        event="backend.error",
        level="error",
        message="AIDP 做题生产平台飞书错误通知测试。",
        data={"source": "notifications/test", "send": send},
        force=send,
    ) if send else _result(True, False, True, False, "error", "backend.error", uuid4().hex, "未传 send=true，只检查配置。")


def _send_feishu_text(webhook: str, secret: str, text: str) -> int:
    url = _signed_url(webhook, secret)
    response = requests.post(url, json={"msg_type": "text", "content": {"text": text}}, timeout=10)
    response.raise_for_status()
    return response.status_code


def _signed_url(webhook: str, secret: str) -> str:
    if not secret:
        return webhook
    timestamp = str(int(time.time()))
    sign = base64.b64encode(hmac.new(secret.encode("utf-8"), f"{timestamp}\n{secret}".encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")
    separator = "&" if "?" in webhook else "?"
    return f"{webhook}{separator}timestamp={timestamp}&sign={quote_plus(sign)}"


def _render_message(event: str, level: str, message: str, trace_id: str, data: Optional[dict[str, Any]]) -> str:
    settings = get_settings()
    lines = [
        "AIDP 做题生产平台错误通知",
        f"级别：{level}",
        f"事件：{event}",
        f"时间：{utc_now().isoformat()}",
        f"trace_id：{trace_id}",
        f"面板：{settings.public_base_url.rstrip('/')}/alerts",
        f"内容：{_redact(message)}",
    ]
    if data:
        lines.append("数据：" + _redact(json.dumps(data, ensure_ascii=False, default=str)[:1600]))
    return "\n".join(lines)


def _result(ok: bool, sent: bool, skipped: bool, dry_run: bool, level: str, event: str, trace_id: str, reason: str, status_code: Optional[int] = None) -> NotificationSendResponse:
    return NotificationSendResponse(ok=ok, sent=sent, skipped=skipped, dry_run=dry_run, level=level, event=event, trace_id=trace_id, reason=reason, status_code=status_code, message=reason)


def _load_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _split_events(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _normalize_events(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return _split_events(str(value or ""))


def _normalize_level(value: str) -> str:
    level = str(value or "error").lower()
    return "warn" if level == "warning" else level if level in LEVEL_RANK else "error"


def _level_allowed(level: str, min_level: str) -> bool:
    return LEVEL_RANK.get(_normalize_level(level), 3) >= LEVEL_RANK.get(_normalize_level(min_level), 2)


def _int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _redact(value: str) -> str:
    return SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", value)
