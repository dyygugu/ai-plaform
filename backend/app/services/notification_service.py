import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import requests

from app.core.settings import get_settings
from app.schemas.notification import NotificationConfigRead, NotificationConfigUpdate, NotificationSendResponse
from app.services.alert_service import render_human_readable_alert_text
from app.services.task_rules import utc_now


LEVEL_RANK = {"debug": 0, "info": 1, "warn": 2, "warning": 2, "error": 3, "failed": 4, "critical": 4}
DEFAULT_EVENTS = [
    "backend.error",
    "backend.unhandled_exception",
    "audit.error",
    "worker.error",
    "alert.evaluation.failed",
    "alert.evaluation.warning",
]
SECRET_KEY_PATTERN = r"(?:cookie|api[_-]?key|token|secret|password|authorization|主密钥|恢复码)"
AUTH_BEARER_PATTERN = re.compile(rf"(?P<prefix>[\"']?authorization[\"']?\s*[:=]\s*[\"']?bearer\s+)[^\"',;\s，}}]+", re.IGNORECASE)
SECRET_PATTERN = re.compile(rf"(?P<prefix>[\"']?{SECRET_KEY_PATTERN}[\"']?\s*[:=]\s*[\"']?)[^\"',;\s，}}]+", re.IGNORECASE)
FEISHU_HOOK_PATTERN = re.compile(r"(?P<prefix>/hook/)[^?\s]+", re.IGNORECASE)
SIGNED_QUERY_PATTERN = re.compile(r"(?P<prefix>[?&]sign=)[^&\s]+", re.IGNORECASE)
PROVIDER_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{7,}\b")
AWS_ACCESS_KEY_PATTERN = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
LAST_SENT: dict[str, float] = {}
COOLDOWN_LOCKS: dict[str, threading.Lock] = {}
COOLDOWN_LOCKS_GUARD = threading.Lock()
KNOWN_ERROR_CODES = {
    "AI_PROVIDER_502": {
        "severity": "error",
        "title": "做题 AI 服务请求失败",
        "problem": "做题 AI 接口暂时不可用，当前这题拿不到可靠答案。",
        "impact": "本次做题会暂停或等待重试，不会自动提交没有把握的答案。",
        "action": "稍后重试；如果连续出现，检查做题 AI 配置和供应商状态。",
    },
    "AI_PROVIDER_TIMEOUT": {
        "severity": "error",
        "title": "做题 AI 响应超时",
        "problem": "做题 AI 在规定时间内没有返回结果。",
        "impact": "当前题不会继续自动提交，相关账号可能等待下一轮重试。",
        "action": "先观察是否自动恢复；连续出现时降低并发或检查 AI 服务。",
    },
    "TASK_PAGE_AUTH_EXPIRED": {
        "severity": "critical",
        "title": "账号登录失效",
        "problem": "账号 {account} 的登录状态失效，平台打不开做题页面。",
        "impact": "这个账号不会继续自动做题或提交。",
        "action": "去账号管理重新登录该账号，再回到运行页继续任务。",
    },
    "TASK_PAGE_TIMEOUT": {
        "severity": "error",
        "title": "做题页面打开超时",
        "problem": "账号 {account} 的做题页面长时间没有响应。",
        "impact": "该账号本轮做题会暂停，避免在页面状态不明时继续提交。",
        "action": "打开账号任务页检查网络和页面是否正常；恢复后再继续任务。",
    },
    "SUBMIT_FAILED": {
        "severity": "critical",
        "title": "答案提交失败",
        "problem": "平台向 AIDP 提交答案时失败。",
        "impact": "当前题可能停在未提交状态，需要确认后再继续，避免重复提交或漏提交。",
        "action": "打开对应账号任务页核对当前题状态，再决定重试或人工处理。",
    },
    "READBACK_MISMATCH": {
        "severity": "critical",
        "title": "提交后回读不一致",
        "problem": "提交后读回来的结果和本次提交内容不一致。",
        "impact": "不能确认答案是否正确保存，自动做题必须停止等待人工核对。",
        "action": "打开题目页面核对实际保存结果，并把排查编号发给我定位。",
    },
    "CONFIRMATION_PENDING": {
        "severity": "error",
        "title": "有操作等待人工确认",
        "problem": "系统遇到需要你授权的高风险动作，当前正在等待确认。",
        "impact": "相关自动流程会暂停，但不会擅自执行真实提交或高危操作。",
        "action": "打开 AI 确认队列，批准或驳回待处理项。",
    },
    "EXECUTION_GATE_BLOCKED": {
        "severity": "critical",
        "title": "安全闸门阻止执行",
        "problem": "执行前安全检查没有通过，系统已阻止继续运行。",
        "impact": "相关任务不会继续自动做题或提交，避免错误扩大。",
        "action": "打开能力工作台或生产护栏，按阻断原因补齐审核、回读或授权。",
    },
    "WEB_LOGIN_RATE_LIMIT": {
        "severity": "warn",
        "title": "平台登录连续失败",
        "problem": "平台登录连续失败，系统已临时限流；涉及账号 {account}。",
        "impact": "只是登录入口被临时保护，不会直接影响已在运行的自动做题；连续失败可能说明有人输错密码或在尝试登录。",
        "action": "确认是否有人输错密码；如果不是本人操作，先不要继续尝试，检查访问来源和账号安全。",
    },
}


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
        config["source"] = "env"
    if settings.feishu_secret:
        config["secret"] = settings.feishu_secret
    if "AIDP_NOTIFY_ENABLED" in os.environ and bool(settings.notify_enabled):
        config["enabled"] = True
    if settings.notify_dry_run:
        config["dryRun"] = True
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
        webhook_url=_mask_webhook_url(webhook),
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
    webhook_url = _preserve_blank_update(payload.webhook_url, str(config.get("webhookUrl") or "").strip())
    secret = _preserve_blank_update(payload.secret, str(config.get("secret") or "").strip())
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
    notification_data = data or {}
    summary = _build_human_summary(normalized_event, normalized_level, message, notification_data)
    effective_level = _normalize_level(summary.get("severity") or normalized_level)
    if not bool(config.get("enabled")):
        return _result(False, False, True, False, effective_level, normalized_event, trace, "通知未启用。")
    if not force and not _level_allowed(effective_level, str(config.get("minLevel") or "warn")):
        return _result(False, False, True, False, effective_level, normalized_event, trace, "低于通知等级阈值。")
    events = _normalize_events(config.get("events"))
    if not force and events and normalized_event not in events:
        return _result(False, False, True, False, effective_level, normalized_event, trace, "事件未列入通知白名单。")
    key = _cooldown_key(normalized_event, effective_level, summary, notification_data)
    cooldown = _effective_cooldown_seconds(config, summary)
    text = _render_summary(summary, trace)
    if bool(config.get("dryRun")):
        return _result(True, False, False, True, effective_level, normalized_event, trace, "dry-run：已生成飞书通知文本但未发送。")
    webhook = str(config.get("webhookUrl") or "").strip()
    if not webhook:
        return _result(False, False, True, False, effective_level, normalized_event, trace, "飞书 webhook 未配置。")
    if _test_network_send_blocked(config):
        return _result(False, False, True, False, effective_level, normalized_event, trace, "测试环境禁止真实飞书发送，已跳过本次外发。")
    with _cooldown_lock_for(key):
        try:
            with _cooldown_file_lock_for(key):
                now = time.time()
                last_sent = _last_sent_at(key, refresh_from_disk=True)
                if not force and last_sent and now - last_sent < cooldown:
                    return _result(False, False, True, False, effective_level, normalized_event, trace, "命中飞书通知冷却窗口。")
                _record_sent_at(key, now)
                try:
                    status_code = _send_feishu_text(webhook, str(config.get("secret") or ""), text)
                except Exception as exc:  # noqa: BLE001 - notification failure must not mask original error.
                    _release_sent_at(key, now)
                    return _result(False, False, False, False, effective_level, normalized_event, trace, f"飞书发送失败：{_redact(str(exc))}")
        except TimeoutError:
            return _result(False, False, True, False, effective_level, normalized_event, trace, "飞书通知冷却锁繁忙，已跳过本次重复通知。")
    return _result(True, True, False, False, effective_level, normalized_event, trace, "飞书通知已发送。", status_code=status_code)


def send_test_notification(send: bool) -> NotificationSendResponse:
    return send_error_notification(
        event="backend.error",
        level="error",
        message="AIDP 做题生产平台飞书错误通知测试。",
        data={"source": "notifications/test", "send": send},
        force=send,
    ) if send else _result(True, False, True, False, "error", "backend.error", uuid4().hex, "未传 send=true，只检查配置。")


def _send_feishu_text(webhook: str, secret: str, text: str) -> int:
    payload = {"msg_type": "text", "content": {"text": text}}
    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = _feishu_sign(timestamp, secret)
    response = requests.post(webhook, json=payload, timeout=10)
    response.raise_for_status()
    business = _parse_feishu_response(response)
    if business:
        code = business.get("code")
        if code is None:
            code = business.get("StatusCode")
        if code is None:
            code = business.get("status_code")
        if code is not None and str(code) not in {"0", "0.0", ""}:
            msg = business.get("msg") or business.get("StatusMessage") or business.get("message") or "unknown"
            raise RuntimeError(f"飞书业务返回失败：code={code} msg={msg}")
    return response.status_code


def _parse_feishu_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _feishu_sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    return base64.b64encode(hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()).decode("utf-8")


def _render_message(event: str, level: str, message: str, trace_id: str, data: Optional[dict[str, Any]]) -> str:
    summary = _build_human_summary(event, level, message, data or {})
    return _render_summary(summary, trace_id)


def build_error_notification_text(event: str, level: str, message: str, data: Optional[dict[str, Any]] = None, trace_id: str = "") -> str:
    normalized_event = event or "backend.error"
    normalized_level = _normalize_level(level)
    summary = _build_human_summary(normalized_event, normalized_level, message, data or {})
    return _render_summary(summary, trace_id or uuid4().hex)


def _render_summary(summary: dict[str, str], trace_id: str) -> str:
    settings = get_settings()
    return render_human_readable_alert_text(
        title=summary["title"],
        severity=summary["severity"],
        problem=summary["problem"],
        impact=summary["impact"],
        action=summary["action"],
        occurred_at=utc_now().isoformat(),
        trace_id=trace_id,
        panel_url=f"{settings.public_base_url.rstrip('/')}/alerts",
        technical_event=summary["technical_event"],
    )


def _build_human_summary(event: str, level: str, message: str, data: dict[str, Any]) -> dict[str, str]:
    normalized_level = _normalize_level(level)
    structured = _extract_structured_message(message)
    code = _first_text(data, "error_code") or _first_text(structured, "error_code")
    if not code:
        code = _find_known_error_code(message)
    if code and code in KNOWN_ERROR_CODES:
        item = KNOWN_ERROR_CODES[code]
        if code == "WEB_LOGIN_RATE_LIMIT":
            account = _first_text(data, "phone_masked", "account_user_id", "account_id", "user_id") or "平台登录账号"
        else:
            account = _first_text(data, "account_user_id", "account_id", "user_id") or "某个账号"
        detail = _first_text(data, "error_detail") or _first_text(structured, "error_detail")
        stage = _first_text(data, "stage") or _first_text(structured, "stage")
        step = _first_text(data, "step") or _first_text(structured, "step")
        return {
            "title": item["title"],
            "severity": _max_level(normalized_level, item["severity"]),
            "problem": _known_error_problem(code, item["problem"].format(account=account), detail, stage, step),
            "impact": item["impact"],
            "action": item["action"],
            "technical_event": f"{code} / {event}",
        }

    incidents = data.get("incidents")
    if isinstance(incidents, list) and incidents:
        first = incidents[0] if isinstance(incidents[0], dict) else {}
        title = _first_text(first, "title") or "告警评估发现待处理事件"
        reason = _first_text(first, "reason") or _clean_message(message)
        subject = _first_text(first, "subject") or title
        action = _first_text(first, "recommended_action") or "打开告警中心查看待处理事件，并按提示处理。"
        severity = _incident_effective_severity(level, incidents)
        technical_key = _first_text(first, "key") or event
        return {
            "title": f"{len(incidents)} 条告警待处理：{title}",
            "severity": severity,
            "problem": f"{subject}：{reason}",
            "impact": "平台存在未关闭告警，相关采集、做题或发布流程需要人工确认后再继续。",
            "action": action,
            "technical_event": f"{technical_key} / {event}",
        }

    if event == "worker.error":
        return {
            "title": "Worker 执行异常",
            "severity": level,
            "problem": "Worker 在执行任务时上报异常，具体原因已记录在技术日志中。",
            "impact": "相关账号或任务可能暂停，系统会避免在状态不明时继续提交。",
            "action": "打开 Worker 管理页查看最近错误；如果同一账号反复出现，先暂停该账号。",
            "technical_event": event,
        }
    if event == "audit.error":
        return {
            "title": "严重审计事件存在",
            "severity": level,
            "problem": "权限审计记录到严重事件，具体原因已记录在技术日志中。",
            "impact": "可能涉及高风险操作或异常状态，需要人工确认是否安全。",
            "action": "打开权限审计页按 trace_id 排查，并记录处理结果。",
            "technical_event": event,
        }
    if event == "backend.error" and "测试" in message:
        return {
            "title": "飞书通知测试",
            "severity": level,
            "problem": "你正在测试飞书通知链路是否可用。",
            "impact": "这不是生产故障，不需要处理业务任务。",
            "action": "确认收到这条消息即可；如未收到，检查 webhook、dry-run 和最低级别配置。",
            "technical_event": event,
        }
    return {
        "title": "平台内部服务异常",
        "severity": level,
        "problem": "平台后端处理请求时出错，具体原因已记录在技术日志中。",
        "impact": "本次操作可能失败；是否影响自动做题，需要打开告警中心确认。",
        "action": "把排查编号发给我，我按 trace_id 查日志定位。",
        "technical_event": event,
    }


def _extract_structured_message(message: str) -> dict[str, Any]:
    text = str(message or "").strip()
    if not (text.startswith("{") and text.endswith("}")):
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _find_known_error_code(message: str) -> str:
    text = str(message or "")
    for code in KNOWN_ERROR_CODES:
        if code in text:
            return code
    return ""


def _incident_effective_severity(level: str, incidents: list[Any]) -> str:
    normalized_level = _normalize_level(level)
    incident_levels = []
    for item in incidents:
        if isinstance(item, dict):
            incident_levels.append(_normalize_level(_first_text(item, "severity")))
    if normalized_level in {"critical", "failed"} or any(item in {"critical", "failed"} for item in incident_levels):
        return "critical"
    if normalized_level in {"debug", "info", "warn"}:
        return normalized_level
    return normalized_level


def _cooldown_key(event: str, level: str, summary: dict[str, str], data: dict[str, Any]) -> str:
    if _is_task_ai_provider_outage(summary):
        return f"{event}|{level}|{summary['title']}|{summary['technical_event']}|provider=task_ai"
    identity_parts = []
    for key in ("worker_id", "account_user_id", "account_id", "user_id", "task_id", "target_id", "event_type", "method", "path"):
        value = _first_text(data, key)
        if value:
            identity_parts.append(f"{key}={value}")
    incidents = data.get("incidents")
    if isinstance(incidents, list):
        for item in incidents[:5]:
            if not isinstance(item, dict):
                continue
            incident_key = _first_text(item, "key")
            subject = _first_text(item, "subject")
            if incident_key or subject:
                identity_parts.append(f"incident={incident_key}:{subject}")
    identity = "|".join(identity_parts) or "global"
    return f"{event}|{level}|{summary['title']}|{summary['technical_event']}|{identity}"


def _is_task_ai_provider_outage(summary: dict[str, str]) -> bool:
    technical_event = str(summary.get("technical_event") or "")
    return technical_event.startswith("AI_PROVIDER_")


def _effective_cooldown_seconds(config: dict[str, Any], summary: dict[str, str]) -> int:
    base = int(config.get("cooldownSec") or 300)
    if _is_task_ai_provider_outage(summary):
        return max(base, 3600)
    return base


def _test_network_send_blocked(config: dict[str, Any]) -> bool:
    if bool(config.get("dryRun")):
        return False
    allow = str(os.environ.get("AIDP_ALLOW_TEST_NOTIFICATION_SEND") or "").strip().lower()
    if allow in {"1", "true", "yes", "on"}:
        return False
    if "PYTEST_CURRENT_TEST" not in os.environ:
        return False
    return bool(str(config.get("webhookUrl") or "").strip())


def _known_error_problem(code: str, fallback: str, detail: str, stage: str, step: str) -> str:
    if code == "AI_PROVIDER_502":
        return _provider_problem("做题 AI 上游返回 502/Bad Gateway", detail, stage, step)
    if code == "AI_PROVIDER_TIMEOUT":
        return _provider_problem("做题 AI 上游请求超时", detail, stage, step)
    return fallback


def _provider_problem(prefix: str, detail: str, stage: str, step: str) -> str:
    parts = [prefix]
    if detail:
        parts.append(f"详情：{detail[:180]}")
    stage_step = "/".join(part for part in [stage, step] if part)
    if stage_step:
        parts.append(f"发生阶段：{stage_step}")
    parts.append("当前题拿不到可靠答案")
    return "；".join(parts) + "。"


def _last_sent_at(key: str, refresh_from_disk: bool = False) -> float:
    memory_value = LAST_SENT.get(key, 0.0)
    if memory_value and not refresh_from_disk:
        return memory_value
    state = _load_cooldown_state()
    value = state.get(key)
    try:
        file_value = float(value)
    except (TypeError, ValueError):
        file_value = 0.0
    timestamp = max(float(memory_value or 0.0), file_value)
    LAST_SENT[key] = timestamp
    return timestamp


def _record_sent_at(key: str, timestamp: float) -> None:
    LAST_SENT[key] = timestamp
    state = _load_cooldown_state()
    state[key] = timestamp
    _save_cooldown_state(state, timestamp)


def _release_sent_at(key: str, timestamp: float) -> None:
    if LAST_SENT.get(key) == timestamp:
        LAST_SENT.pop(key, None)
    state = _load_cooldown_state()
    if state.get(key) == timestamp:
        state.pop(key, None)
        _save_cooldown_state(state, time.time())


def _cooldown_lock_for(key: str) -> threading.Lock:
    with COOLDOWN_LOCKS_GUARD:
        lock = COOLDOWN_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            COOLDOWN_LOCKS[key] = lock
        return lock


@contextmanager
def _cooldown_file_lock_for(key: str):
    path = _resolve_path(get_settings().notification_cooldown_path)
    lock_path = path.with_name(f".{path.name}.{hashlib.sha256(key.encode('utf-8')).hexdigest()}.lock")
    fd = _acquire_cooldown_file_lock(lock_path)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def _acquire_cooldown_file_lock(lock_path: Path, timeout_seconds: float = 12.0, stale_seconds: float = 60.0) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout_seconds
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, f"{os.getpid()} {time.time()}".encode("utf-8"))
            return fd
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > stale_seconds:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() >= deadline:
                raise TimeoutError(f"notification cooldown lock busy: {lock_path}")
            time.sleep(0.05)


def _load_cooldown_state() -> dict[str, float]:
    path = _resolve_path(get_settings().notification_cooldown_path)
    data = _load_json(path)
    if not isinstance(data, dict):
        return {}
    result: dict[str, float] = {}
    for key, value in data.items():
        try:
            result[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return result


def _save_cooldown_state(state: dict[str, float], now: float) -> None:
    path = _resolve_path(get_settings().notification_cooldown_path)
    cutoff = now - 7 * 24 * 60 * 60
    trimmed = {key: value for key, value in state.items() if value >= cutoff}
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        # Notification cooldown must never break the original alert path.
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return


def _max_level(left: str, right: str) -> str:
    normalized_left = _normalize_level(left)
    normalized_right = _normalize_level(right)
    return normalized_left if LEVEL_RANK.get(normalized_left, 3) >= LEVEL_RANK.get(normalized_right, 3) else normalized_right


def _clean_message(message: str) -> str:
    structured = _extract_structured_message(message)
    text = _first_text(structured, "error_detail", "message") if structured else str(message or "")
    text = _redact(text).strip()
    return text[:220] if text else "未提供具体原因"


def _first_text(source: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return _redact(str(value))
    return ""


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
    redacted = AUTH_BEARER_PATTERN.sub(lambda match: f"{match.group('prefix')}<redacted>", value)
    redacted = SECRET_PATTERN.sub(lambda match: f"{match.group('prefix')}<redacted>", redacted)
    redacted = FEISHU_HOOK_PATTERN.sub(lambda match: f"{match.group('prefix')}<redacted>", redacted)
    redacted = SIGNED_QUERY_PATTERN.sub(lambda match: f"{match.group('prefix')}<redacted>", redacted)
    redacted = PROVIDER_KEY_PATTERN.sub("<redacted>", redacted)
    return AWS_ACCESS_KEY_PATTERN.sub("<redacted>", redacted)


def _mask_webhook_url(webhook: str) -> str:
    return _redact(webhook) if webhook else ""


def _preserve_blank_update(value: Optional[str], existing: str) -> str:
    if value is None:
        return existing
    stripped = value.strip()
    if "<redacted>" in stripped:
        return existing
    return stripped or existing
