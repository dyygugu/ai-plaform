import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.schemas.operation_recording import OperationRecordingRequest, OperationRecordingResponse
from app.schemas.worker import WorkerEventReportRequest
from app.services.learning_package_service import register_learning_package
from app.services.worker_service import report_worker_event


SENSITIVE_KEY_RE = re.compile(r"(cookie|authorization|token|secret|password|msToken|a_bogus|session)", re.IGNORECASE)
SENSITIVE_VALUE_RE = re.compile(
    r"(Bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"((?:token|secret|session|cookie|authorization|msToken|a_bogus)=)[^&\s\"']+",
    re.IGNORECASE,
)
CLAIM_PATH_RE = re.compile(r"(receive|claim|process|assign|fetch)", re.IGNORECASE)
NON_CLAIM_PATH_RE = re.compile(
    r"(agreement/check|certification|getCertificationStatus|SubmitTempItemAnswer|SubmitItem|MGetAnswerList|verify/submit|msg/unread|feature-flags)",
    re.IGNORECASE,
)


def save_operation_recording(db: Session, payload: OperationRecordingRequest) -> OperationRecordingResponse:
    received_at = datetime.now(timezone.utc)
    recording_id = str(payload.recording_id or f"opr-{received_at.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}")
    root = Path(get_settings().operation_recording_root)
    root.mkdir(parents=True, exist_ok=True)
    artifact = root / f"{recording_id}.json"
    sanitized = sanitize_recording(payload.model_dump())
    operation_claim_analysis = analyze_operation_claim_candidates(sanitized)
    document = {
        "recording_id": recording_id,
        "received_at": received_at.isoformat(),
        "purpose": "operation-learning-http-replay",
        "sanitized": True,
        "operation_claim_analysis": operation_claim_analysis,
        "recording": sanitized,
    }
    artifact.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    bound_task_id = _resolve_bound_task_id(payload)
    learning_package = {}
    if bound_task_id:
        learning_package = register_learning_package(
            bound_task_id,
            {
                "learning_package_id": recording_id,
                "recording_id": recording_id,
                "display_name": _display_name_for_package(payload, received_at),
                "source": str(payload.source or "browser_extension"),
                "uploaded_at": payload.recorded_at or payload.ended_at or payload.started_at or received_at.isoformat(),
                "status": "parsed",
                "completeness": _learning_package_completeness(payload),
                "detected_actions": payload.detected_actions or _detect_actions_from_recording(document),
                "page_url": str(payload.page_url or ""),
                "task_id_candidates": payload.task_id_candidates,
            },
            artifact,
        )
    try:
        report_worker_event(
            db,
            WorkerEventReportRequest(
                worker_id="operation-recorder-api",
                event_type="event_report",
                account_user_id=payload.account_user_id,
                task_id=payload.task_id,
                target_version="8789",
                severity="info",
                stage="worker_runtime",
                step="log_summary",
                message=f"操作录制已上传：{payload.mode}，事件 {len(payload.events)}，网络 {len(payload.network)}",
            ),
        )
    except Exception:
        pass
    return OperationRecordingResponse(
        ok=True,
        recording_id=recording_id,
        mode=payload.mode,
        artifact_path=str(artifact),
        event_count=len(payload.events),
        network_count=len(payload.network),
        screenshot_count=len(payload.screenshots),
        received_at=received_at,
        operation_claim_analysis=operation_claim_analysis,
        task_id=bound_task_id,
        learning_package=learning_package,
        message="操作录制已保存，后续可交给内置 AI 学习。",
    )


def analyze_operation_claim_candidates(recording: dict[str, Any]) -> dict[str, Any]:
    network = recording.get("network") if isinstance(recording, dict) else []
    candidates: list[dict[str, Any]] = []
    known_non_claim_count = 0
    if not isinstance(network, list):
        network = []
    for index, item in enumerate(network):
        if not isinstance(item, dict) or str(item.get("type") or "").lower() != "request":
            continue
        method = str(item.get("method") or "").upper()
        url = str(item.get("url") or "")
        path = _request_path(url)
        if method != "POST":
            continue
        if NON_CLAIM_PATH_RE.search(path):
            known_non_claim_count += 1
            continue
        if CLAIM_PATH_RE.search(path):
            candidates.append(
                {
                    "request_index": index,
                    "method": method,
                    "path": path,
                    "url": url,
                    "reason": "POST 路径包含 receive/claim/process/assign/fetch 等领题候选关键词。",
                }
            )
    status = "candidate_found" if candidates else "not_captured"
    return {
        "status": status,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "known_non_claim_count": known_non_claim_count,
        "message": (
            "发现可能的 operation 领题候选接口；必须人工复核响应、跳转和题目变化后才能接入。"
            if candidates
            else "未捕获到可证明的 operation 领题接口；不能猜 endpoint 或伪造领题成功。"
        ),
    }


def sanitize_recording(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if SENSITIVE_KEY_RE.search(str(key)):
                result[key] = "[REDACTED]"
            else:
                result[key] = sanitize_recording(item)
        return result
    if isinstance(value, list):
        return [sanitize_recording(item) for item in value]
    if isinstance(value, str):
        return SENSITIVE_VALUE_RE.sub(lambda match: (match.group(1) or match.group(2) or "") + "[REDACTED]", value)
    return value


def _resolve_bound_task_id(payload: OperationRecordingRequest) -> str:
    direct = str(payload.task_id or "").strip()
    if direct:
        return direct
    for candidate in payload.task_id_candidates:
        if not isinstance(candidate, dict):
            continue
        value = str(candidate.get("value") or "").strip()
        confidence = str(candidate.get("confidence") or "").strip().lower()
        if value and confidence == "high":
            return value
    return ""


def _display_name_for_package(payload: OperationRecordingRequest, received_at: datetime) -> str:
    raw_text = payload.recorded_at or payload.ended_at or payload.started_at or received_at.isoformat()
    try:
        dt = datetime.fromisoformat(str(raw_text).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(raw_text)


def _learning_package_completeness(payload: OperationRecordingRequest) -> str:
    actions = set(str(item) for item in (payload.detected_actions or []) if str(item))
    if {"fill_score", "fill_reason", "click_temp_save"}.issubset(actions):
        return "complete"
    if actions:
        return "partial"
    return "partial"


def _detect_actions_from_recording(document: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    text = json.dumps(document.get("recording") or {}, ensure_ascii=False)
    if "label_sorce" in text or "score" in text:
        actions.append("fill_score")
    if "label_remark" in text or "reason" in text:
        actions.append("fill_reason")
    if "SubmitTempItemAnswer" in text:
        actions.append("click_temp_save")
    return actions


def _request_path(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path:
        return parsed.path
    return url.split("?", 1)[0]
