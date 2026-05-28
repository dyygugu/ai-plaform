import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from app.core.settings import get_settings
from app.schemas.learning_package import (
    LearningPackageCandidate,
    LearningPackageItem,
    LearningPackageSummary,
    SelectLearningPackageResponse,
    TaskLearningPackageListResponse,
)


def list_task_learning_packages(task_id: str, *, root_dir: Optional[Path] = None) -> TaskLearningPackageListResponse:
    task_id_text = str(task_id)
    index = _read_task_index(task_id_text, root_dir=root_dir)
    selected_learning_package_id = str(index.get("selected_learning_package_id") or "")
    items = []
    for raw in index.get("items", []):
        if not isinstance(raw, dict):
            continue
        package_id = str(raw.get("learning_package_id") or "")
        items.append(
            LearningPackageItem(
                learning_package_id=package_id,
                recording_id=str(raw.get("recording_id") or package_id),
                task_id=task_id_text,
                display_name=_normalize_display_name(raw, package_id),
                source=str(raw.get("source") or "browser_extension"),
                uploaded_at=_parse_datetime(raw.get("uploaded_at")),
                status=str(raw.get("status") or "parsed"),
                completeness=str(raw.get("completeness") or "partial"),
                selected=package_id == selected_learning_package_id,
            )
        )
    return TaskLearningPackageListResponse(
        task_id=task_id_text,
        selected_learning_package_id=selected_learning_package_id,
        items=items,
    )


def save_selected_learning_package(task_id: str, learning_package_id: str, *, root_dir: Optional[Path] = None) -> SelectLearningPackageResponse:
    task_id_text = str(task_id)
    package_id = str(learning_package_id).strip()
    index = _read_task_index(task_id_text, root_dir=root_dir)
    items = index.get("items", []) if isinstance(index.get("items"), list) else []
    known_ids = {
        str(item.get("learning_package_id") or "")
        for item in items
        if isinstance(item, dict)
    }
    if package_id and package_id not in known_ids:
        raise FileNotFoundError(f"learning package not found: {package_id}")
    index["task_id"] = task_id_text
    index["selected_learning_package_id"] = package_id
    _write_task_index(task_id_text, index, root_dir=root_dir)
    return SelectLearningPackageResponse(
        task_id=task_id_text,
        selected_learning_package_id=package_id,
        message="已切换当前学习包。",
    )


def register_learning_package(task_id: str, metadata: dict[str, Any], artifact_path: Path, *, root_dir: Optional[Path] = None) -> dict[str, Any]:
    task_id_text = str(task_id).strip()
    if not task_id_text:
        return {}
    index = _read_task_index(task_id_text, root_dir=root_dir)
    items = index.get("items", []) if isinstance(index.get("items"), list) else []
    package_id = str(metadata.get("learning_package_id") or metadata.get("recording_id") or artifact_path.stem)
    existing = next((item for item in items if isinstance(item, dict) and str(item.get("learning_package_id") or "") == package_id), None)
    record = {
        "learning_package_id": package_id,
        "recording_id": str(metadata.get("recording_id") or package_id),
        "task_id": task_id_text,
        "display_name": str(metadata.get("display_name") or _display_name_from_uploaded_at(str(metadata.get("uploaded_at") or ""))),
        "source": str(metadata.get("source") or "browser_extension"),
        "uploaded_at": str(metadata.get("uploaded_at") or _now().isoformat()),
        "status": str(metadata.get("status") or "parsed"),
        "completeness": str(metadata.get("completeness") or "partial"),
        "detected_actions": metadata.get("detected_actions") if isinstance(metadata.get("detected_actions"), list) else [],
        "page_url": str(metadata.get("page_url") or ""),
        "task_id_candidates": metadata.get("task_id_candidates") if isinstance(metadata.get("task_id_candidates"), list) else [],
        "artifact_path": str(artifact_path),
    }
    if existing is None:
        items.insert(0, record)
    else:
        existing.clear()
        existing.update(record)
    index["task_id"] = task_id_text
    index["items"] = items
    if not str(index.get("selected_learning_package_id") or ""):
        complete = next((item for item in items if str(item.get("completeness") or "") == "complete"), None)
        partial = next((item for item in items if str(item.get("completeness") or "") == "partial"), None)
        chosen = complete or partial
        index["selected_learning_package_id"] = str((chosen or {}).get("learning_package_id") or "")
    _write_task_index(task_id_text, index, root_dir=root_dir)
    return record


def get_selected_learning_package_summary(task_id: str, selected_learning_package_id: str = "", *, root_dir: Optional[Path] = None) -> LearningPackageSummary:
    task_id_text = str(task_id)
    index = _read_task_index(task_id_text, root_dir=root_dir)
    package_id = str(selected_learning_package_id or index.get("selected_learning_package_id") or "")
    if not package_id:
        return LearningPackageSummary()
    items = index.get("items", []) if isinstance(index.get("items"), list) else []
    item = next((entry for entry in items if isinstance(entry, dict) and str(entry.get("learning_package_id") or "") == package_id), None)
    if not item:
        return LearningPackageSummary()
    summary_text = _build_summary_text(item)
    return LearningPackageSummary(
        learning_package_id=package_id,
        source=str(item.get("source") or ""),
        status=str(item.get("status") or ""),
        completeness=str(item.get("completeness") or ""),
        uploaded_at=str(item.get("uploaded_at") or ""),
        detected_actions=[str(value) for value in item.get("detected_actions", []) if str(value)],
        page_url=str(item.get("page_url") or ""),
        task_id_candidates=[
            LearningPackageCandidate(
                value=str(candidate.get("value") or ""),
                source=str(candidate.get("source") or ""),
                confidence=str(candidate.get("confidence") or ""),
            )
            for candidate in item.get("task_id_candidates", [])
            if isinstance(candidate, dict)
        ],
        summary_text=summary_text,
    )


def resolve_learning_package_id(task_id: str, selected_learning_package_id: str = "", *, root_dir: Optional[Path] = None) -> str:
    task_id_text = str(task_id)
    package_id = str(selected_learning_package_id or "").strip()
    index = _read_task_index(task_id_text, root_dir=root_dir)
    if not package_id:
        return str(index.get("selected_learning_package_id") or "").strip()
    items = index.get("items", []) if isinstance(index.get("items"), list) else []
    known_ids = {
        str(item.get("learning_package_id") or "").strip()
        for item in items
        if isinstance(item, dict)
    }
    if package_id not in known_ids:
        raise FileNotFoundError(f"学习包 {package_id} 不属于当前任务 {task_id_text}。")
    return package_id


def _build_summary_text(item: dict[str, Any]) -> str:
    actions = [str(value) for value in item.get("detected_actions", []) if str(value)]
    candidate_values = [str(candidate.get("value") or "") for candidate in item.get("task_id_candidates", []) if isinstance(candidate, dict) and str(candidate.get("value") or "")]
    parse_failure_reason = str(item.get("parse_failure_reason") or item.get("parse_error") or "").strip()
    parse_warnings = [str(value) for value in item.get("parse_warnings", []) if str(value).strip()]
    parts = [
        f"任务ID：{str(item.get('task_id') or '')}",
        f"任务名称：{str(item.get('task_name') or '')}",
        f"学习包ID：{str(item.get('learning_package_id') or '')}",
        f"录制ID：{str(item.get('recording_id') or item.get('learning_package_id') or '')}",
        f"来源：{str(item.get('source') or '')}",
        f"解析状态：{str(item.get('status') or '')}",
        f"完整度：{str(item.get('completeness') or '')}",
    ]
    if actions:
        parts.append("检测动作：" + "、".join(actions))
        parts.append("页面填写动作摘要：" + "、".join(actions))
    if candidate_values:
        parts.append("TaskID候选：" + "、".join(candidate_values))
    if str(item.get("page_url") or ""):
        parts.append("页面：" + str(item.get("page_url") or ""))
    if parse_failure_reason:
        parts.append("解析失败原因：" + parse_failure_reason)
    if parse_warnings:
        parts.append("解析警告：" + "、".join(parse_warnings[:8]))
    context_lines = _build_artifact_context_lines(item)
    if context_lines:
        parts.append("学习包上下文：")
        parts.extend(context_lines)
    return "\n".join(parts)


def _build_artifact_context_lines(item: dict[str, Any]) -> list[str]:
    artifact = _load_learning_package_artifact(item)
    if not artifact:
        return []
    recording = artifact.get("recording") if isinstance(artifact.get("recording"), dict) else {}
    if not recording:
        return []
    lines: list[str] = []
    mode = str(recording.get("mode") or "").strip()
    if mode:
        lines.append(f"录制模式：{mode}")
    api_paths = _extract_api_paths(recording)
    if api_paths:
        lines.append("关键接口：" + "、".join(api_paths[:8]))
        lines.append("关键 HTTP 摘要：" + "、".join(api_paths[:8]))
    temp_save = _extract_temp_save_context(recording)
    if temp_save.get("material_keys"):
        lines.append("题面材料字段：" + "、".join(temp_save["material_keys"][:10]))
        lines.append("题面字段摘要：" + "、".join(temp_save["material_keys"][:10]))
        original_keys = [key for key in temp_save["material_keys"] if key in {"image_gt", "original_image", "reference_image"} or "原图" in key]
        ai_keys = [key for key in temp_save["material_keys"] if key.startswith("model_image") or key in {"ai_image", "candidate_image"} or "AI" in key.upper()]
        if original_keys:
            lines.append("原图字段摘要：" + "、".join(original_keys[:8]))
        if ai_keys:
            lines.append("AI 图字段摘要：" + "、".join(ai_keys[:8]))
    if temp_save.get("answer_fields"):
        lines.append("暂存答案字段：" + "、".join(temp_save["answer_fields"][:12]))
        score_fields = [key for key in temp_save["answer_fields"] if "label_sorce" in key or "score" in key.lower()]
        reason_fields = [key for key in temp_save["answer_fields"] if "label_remark" in key or "reason" in key.lower() or "remark" in key.lower()]
        if score_fields:
            lines.append("评分字段摘要：" + "、".join(score_fields[:8]))
        if reason_fields:
            lines.append("理由字段摘要：" + "、".join(reason_fields[:8]))
        lines.append("暂存动作摘要：SubmitTempItemAnswer / 暂存接口字段已脱敏摘要")
    dom_titles = _extract_dom_titles(recording)
    if dom_titles:
        lines.append("页面快照：" + "、".join(dom_titles[:4]))
    claim_paths = _extract_claim_candidate_paths(artifact)
    if claim_paths:
        lines.append("领题候选：" + "、".join(claim_paths[:4]))
    return lines


def _load_learning_package_artifact(item: dict[str, Any]) -> dict[str, Any]:
    artifact_path = Path(str(item.get("artifact_path") or "").strip())
    if not artifact_path.exists():
        return {}
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_api_paths(recording: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for entry in recording.get("network", []):
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url:
            continue
        path = urlparse(url).path or url.split("?", 1)[0]
        if not path or not re.search(r"/api/|/dispatcher/", path, flags=re.IGNORECASE):
            continue
        if path not in result:
            result.append(path)
    return result


def _extract_temp_save_context(recording: dict[str, Any]) -> dict[str, list[str]]:
    for entry in reversed(recording.get("network", [])):
        if not isinstance(entry, dict):
            continue
        if "SubmitTempItemAnswer" not in str(entry.get("url") or ""):
            continue
        payload = _load_json_text(entry.get("request_body") or entry.get("post_data") or "")
        answers = payload.get("AuditAnswers") if isinstance(payload.get("AuditAnswers"), list) else []
        answer = answers[0] if answers and isinstance(answers[0], dict) else {}
        content = _load_json_text(answer.get("Content") or "")
        if not isinstance(content, dict):
            continue
        item = content.get("item") if isinstance(content.get("item"), dict) else {}
        data = content.get("data") if isinstance(content.get("data"), dict) else {}
        material_keys = [str(key) for key, value in item.items() if str(value).strip()]
        answer_fields: list[str] = []
        if isinstance(data.get("label_sorce"), dict):
            answer_fields.extend([f"data.label_sorce.{key}" for key in data["label_sorce"].keys()])
        if isinstance(data.get("label_remark"), dict):
            answer_fields.extend([f"data.label_remark.{key}" for key in data["label_remark"].keys()])
        for key in ("discard", "discard_type", "discard_remark", "checkRemark"):
            if key in data:
                answer_fields.append(f"data.{key}")
        return {
            "material_keys": material_keys,
            "answer_fields": answer_fields,
        }
    return {"material_keys": [], "answer_fields": []}


def _extract_dom_titles(recording: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for snapshot in recording.get("dom_snapshots", []):
        if not isinstance(snapshot, dict):
            continue
        title = str(snapshot.get("title") or "").strip()
        if title and title not in result:
            result.append(title)
    return result


def _extract_claim_candidate_paths(artifact: dict[str, Any]) -> list[str]:
    analysis = artifact.get("operation_claim_analysis") if isinstance(artifact.get("operation_claim_analysis"), dict) else {}
    result: list[str] = []
    for entry in analysis.get("candidates", []):
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path") or "").strip()
        if path and path not in result:
            result.append(path)
    return result


def _root_dir() -> Path:
    return Path(get_settings().operation_recording_root).parent / "task-abilities"


def _task_learning_package_dir(task_id: str, *, root_dir: Optional[Path] = None) -> Path:
    return (root_dir or _root_dir()) / str(task_id) / "learning-packages"


def _task_index_path(task_id: str, *, root_dir: Optional[Path] = None) -> Path:
    return _task_learning_package_dir(task_id, root_dir=root_dir) / "index.json"


def _read_task_index(task_id: str, *, root_dir: Optional[Path] = None) -> dict[str, Any]:
    path = _task_index_path(task_id, root_dir=root_dir)
    if not path.exists():
        return {"task_id": str(task_id), "selected_learning_package_id": "", "items": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"task_id": str(task_id), "selected_learning_package_id": "", "items": []}
    if not isinstance(payload, dict):
        return {"task_id": str(task_id), "selected_learning_package_id": "", "items": []}
    payload.setdefault("task_id", str(task_id))
    payload.setdefault("selected_learning_package_id", "")
    payload.setdefault("items", [])
    return payload


def _write_task_index(task_id: str, payload: dict[str, Any], *, root_dir: Optional[Path] = None) -> None:
    path = _task_index_path(task_id, root_dir=root_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return _now()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return _now()


def _display_name_from_uploaded_at(text: str) -> str:
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone(timedelta(hours=8)))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(text or "未命名学习包")


def _normalize_display_name(item: dict[str, Any], package_id: str) -> str:
    raw_name = str(item.get("display_name") or "").strip()
    uploaded_at = str(item.get("uploaded_at") or "").strip()
    if raw_name and not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", raw_name):
        return raw_name
    if uploaded_at:
        return _display_name_from_uploaded_at(uploaded_at)
    return raw_name or package_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_json_text(value: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except Exception:
        return {}
