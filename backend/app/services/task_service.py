import json
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.task import RuntimeConfig, TaskCatalogEvent, TaskCatalogItem, TaskRuleConfig, TaskVisibility
from app.schemas.task import TaskCatalogSeedRequest, TaskRuleConfigUpdateRequest
from app.services.task_rules import (
    DEFAULT_PREFIXES,
    build_task_name_id,
    build_task_short_name,
    extract_task_id,
    map_status_color,
    utc_now,
)

TASK_SOURCE_ACCOUNT_KEY = "task_source_account_user_id"


def get_task_source_account_user_id(db: Session) -> str:
    settings = get_settings()
    config = db.get(RuntimeConfig, TASK_SOURCE_ACCOUNT_KEY)
    return config.value if config and config.value else settings.task_source_account_user_id


def set_task_source_account_user_id(db: Session, source_account_user_id: str, updated_by: str = "system") -> RuntimeConfig:
    config = db.get(RuntimeConfig, TASK_SOURCE_ACCOUNT_KEY)
    if config:
        config.value = source_account_user_id
        config.updated_by = updated_by
    else:
        config = RuntimeConfig(key=TASK_SOURCE_ACCOUNT_KEY, value=source_account_user_id, updated_by=updated_by)
        db.add(config)
    db.flush()
    return config


def _load_json(value: str, fallback: Any) -> Any:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return fallback
    return parsed if parsed is not None else fallback


def get_task_rule_config(db: Session) -> TaskRuleConfig:
    config = db.scalar(select(TaskRuleConfig).order_by(TaskRuleConfig.id.asc()))
    if config:
        return config
    config = TaskRuleConfig(prefix_rules_json=json.dumps(list(DEFAULT_PREFIXES), ensure_ascii=False), manual_short_names_json="{}")
    db.add(config)
    db.flush()
    return config


def read_prefix_rules(db: Session) -> list[str]:
    config = get_task_rule_config(db)
    rules = _load_json(config.prefix_rules_json, list(DEFAULT_PREFIXES))
    return [str(item) for item in rules if str(item).strip()]


def read_manual_short_names(db: Session) -> dict[str, str]:
    config = get_task_rule_config(db)
    data = _load_json(config.manual_short_names_json, {})
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if str(key).strip() and str(value).strip()}


def update_task_rule_config(db: Session, payload: TaskRuleConfigUpdateRequest, updated_by: str = "system") -> TaskRuleConfig:
    config = get_task_rule_config(db)
    prefixes = payload.prefix_rules if payload.prefix_rules is not None else read_prefix_rules(db)
    manual = payload.manual_short_names if payload.manual_short_names is not None else read_manual_short_names(db)
    config.prefix_rules_json = json.dumps([item for item in prefixes if item.strip()], ensure_ascii=False)
    config.manual_short_names_json = json.dumps({key: value for key, value in manual.items() if key.strip() and value.strip()}, ensure_ascii=False)
    config.updated_by = updated_by
    db.flush()
    return config


def list_task_catalog(db: Session, source_account_user_id: Optional[str] = None) -> list[TaskCatalogItem]:
    source = source_account_user_id or get_task_source_account_user_id(db)
    items = list(
        db.scalars(
            select(TaskCatalogItem)
            .where(TaskCatalogItem.source_account_user_id == source)
            .order_by(TaskCatalogItem.updated_at.desc())
        )
    )
    items = _drop_masked_duplicates(items)
    return sorted(items, key=lambda item: (_pending_sort_value(item.pending_raw), item.task_status_raw, item.updated_at), reverse=True)


def _drop_masked_duplicates(items: list[TaskCatalogItem]) -> list[TaskCatalogItem]:
    full_task_names = {item.task_short_name for item in items if "*" not in item.task_id and item.visibility != TaskVisibility.HIDDEN}
    return [
        item
        for item in items
        if item.visibility != TaskVisibility.HIDDEN
        and not ("*" in item.task_id and item.task_short_name in full_task_names)
    ]


def _record_task_event(db: Session, item: TaskCatalogItem, event_type: str, message: str) -> TaskCatalogEvent:
    event = TaskCatalogEvent(
        task_catalog_item_id=item.id,
        source_account_user_id=item.source_account_user_id,
        task_id=item.task_id,
        event_type=event_type,
        status_raw=item.task_status_raw,
        pending_raw=item.pending_raw,
        message=message,
    )
    db.add(event)
    db.flush()
    return event


def seed_task_catalog_item(db: Session, payload: TaskCatalogSeedRequest) -> tuple[TaskCatalogItem, bool]:
    source = payload.source_account_user_id or get_task_source_account_user_id(db)
    task_id = extract_task_id(payload.raw_task_name)
    if not task_id:
        task_id = payload.raw_task_name.strip()
    existing = db.scalar(
        select(TaskCatalogItem).where(
            TaskCatalogItem.source_account_user_id == source,
            TaskCatalogItem.task_id == task_id,
        )
    )
    prefixes = read_prefix_rules(db)
    manual_short_name = read_manual_short_names(db).get(task_id)
    short_name = build_task_short_name(payload.raw_task_name, task_id, prefixes, manual_short_name)
    task_name_id = build_task_name_id(payload.raw_task_name, task_id, prefixes, manual_short_name)
    color = map_status_color(payload.task_status_raw)
    if existing:
        changed = existing.raw_task_name != payload.raw_task_name or existing.task_status_raw != payload.task_status_raw or existing.pending_raw != payload.pending_raw
        existing.raw_task_name = payload.raw_task_name
        existing.task_short_name = short_name
        existing.task_name_id = task_name_id
        existing.task_status_raw = payload.task_status_raw
        existing.task_status_color = color
        existing.pending_raw = payload.pending_raw
        existing.visibility = TaskVisibility.VISIBLE
        existing.last_task_page_seen_at = utc_now()
        existing.last_task_page_error = None
        db.flush()
        _record_task_event(db, existing, "task_catalog_update", "任务目录已刷新" if changed else "任务目录刷新，无字段变化")
        return existing, False
    item = TaskCatalogItem(
        source_account_user_id=source,
        raw_task_name=payload.raw_task_name,
        task_short_name=short_name,
        task_id=task_id,
        task_name_id=task_name_id,
        task_status_raw=payload.task_status_raw,
        task_status_color=color,
        pending_raw=payload.pending_raw,
        visibility=TaskVisibility.VISIBLE,
        last_task_page_seen_at=utc_now(),
    )
    db.add(item)
    db.flush()
    _record_task_event(db, item, "task_catalog_create", "任务目录首次写入")
    return item, True


def mark_task_catalog_pending_unverified(db: Session, source_account_user_id: str, reason: str) -> int:
    items = list(db.scalars(select(TaskCatalogItem).where(TaskCatalogItem.source_account_user_id == source_account_user_id)))
    for item in items:
        item.pending_raw = ""
        item.last_task_page_error = reason
        item.visibility = TaskVisibility.RESTORED
        _record_task_event(db, item, "task_pending_unverified", reason)
    db.flush()
    return len(items)


def get_task_catalog_item(db: Session, item_id: int) -> Optional[TaskCatalogItem]:
    return db.get(TaskCatalogItem, item_id)


def get_task_detail(db: Session, item_id: int) -> Optional[dict[str, Any]]:
    item = get_task_catalog_item(db, item_id)
    if not item:
        return None
    events = list(
        db.scalars(
            select(TaskCatalogEvent)
            .where(TaskCatalogEvent.task_catalog_item_id == item.id)
            .order_by(TaskCatalogEvent.created_at.desc(), TaskCatalogEvent.id.desc())
            .limit(50)
        )
    )
    source_count = db.scalar(
        select(func.count(func.distinct(TaskCatalogItem.source_account_user_id))).where(TaskCatalogItem.task_id == item.task_id)
    ) or 0
    latest_failure = next((event for event in events if "fail" in event.event_type or "error" in event.event_type or "失败" in event.message), None)
    return {
        "item": item,
        "status_history": [event for event in events if event.status_raw],
        "pending_history": [event for event in events if event.pending_raw],
        "timeline": events,
        "source_account_user_id": item.source_account_user_id,
        "covered_account_count": source_count,
        "latest_failure": latest_failure.message if latest_failure else item.last_task_page_error,
    }


def seed_tasks_from_sample_summary(
    db: Session,
    sample_payload: dict[str, Any],
    source_account_user_id: Optional[str] = None,
    pending_verified: bool = True,
    unverified_reason: str = "",
) -> list[TaskCatalogItem]:
    tasks = []
    source = source_account_user_id or get_task_source_account_user_id(db)
    summary_tasks = sample_payload.get("tasks") if isinstance(sample_payload, dict) else None
    if not isinstance(summary_tasks, list):
        search_tasks = sample_payload.get("searchTask", {}).get("Tasks", []) if isinstance(sample_payload, dict) else []
        summary_tasks = []
        for entry in search_tasks:
            task = entry.get("Task", {}) if isinstance(entry, dict) else {}
            nodes = entry.get("Nodes", []) if isinstance(entry, dict) else []
            selected_node = _select_pending_node(nodes)
            node = selected_node.get("Node", {}) if selected_node else {}
            summary_tasks.append({
                "title": task.get("Title", "未命名任务"),
                "taskId": str(task.get("TaskID", "")),
                "nodeName": str(node.get("Name", "")),
                "pendingRaw": _extract_pending_raw(selected_node),
                "status": str(task.get("Status", "未知")),
            })
    for task in summary_tasks:
        if not isinstance(task, dict):
            continue
        title = str(task.get("title") or task.get("rawTaskName") or "未命名任务")
        task_id = str(task.get("taskId") or task.get("taskID") or "").strip()
        pending_raw = str(task.get("pendingRaw") or task.get("pending") or task.get("todo") or "") if pending_verified else ""
        status = str(task.get("status") or task.get("taskStatusRaw") or "可做")
        raw_name = f"{title} {task_id}".strip()
        payload = TaskCatalogSeedRequest(
            source_account_user_id=source,
            raw_task_name=raw_name,
            task_status_raw="可做" if status.isdigit() else status,
            pending_raw=pending_raw,
        )
        item, _ = seed_task_catalog_item(db, payload)
        if not pending_verified:
            item.last_task_page_error = unverified_reason or "旧摘要只保留任务名称，待处理数未经过真实刷新确认。"
            item.visibility = TaskVisibility.RESTORED
        tasks.append(item)
    db.flush()
    return tasks


def _select_pending_node(nodes: Any) -> Optional[dict[str, Any]]:
    if not isinstance(nodes, list) or not nodes:
        return None
    dict_nodes = [node for node in nodes if isinstance(node, dict)]
    for node in dict_nodes:
        permission = node.get("Permission")
        if isinstance(permission, list) and permission:
            return node
    return dict_nodes[0] if dict_nodes else None


def _extract_pending_raw(node: Optional[dict[str, Any]]) -> str:
    value = _node_pending_value(node)
    return str(value) if value is not None else ""


def _node_pending_value(node: Optional[dict[str, Any]]) -> Optional[int]:
    if not node:
        return None
    operator_stat = node.get("OperatorStat") if isinstance(node.get("OperatorStat"), dict) else {}
    node_stat = node.get("NodeStat") if isinstance(node.get("NodeStat"), dict) else {}
    value = operator_stat.get("ToDo") or node_stat.get("ToDo") or ""
    try:
        return int(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _pending_sort_value(pending_raw: str) -> int:
    try:
        return int(str(pending_raw).replace(",", "").strip())
    except ValueError:
        return -1


def get_redacted_sample_path() -> Path:
    return Path("data/redacted-samples/task-page-latest.json")
