import json
from datetime import datetime
from uuid import uuid4
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rule import RuleHitStat, RulePublishEvent, RuleVersion, RuleVersionStatus
from app.schemas.rule import RuleDiffItem, RuleVersionCreateRequest
from app.services.task_rules import utc_now


DEFAULT_RULES = {
    "task_name_shortener": {
        "prefixes": ["RFT人标_", "RFT_", "人标_", "标注_"],
        "keep_task_id_suffix": True,
    },
    "pending_permission_node": {
        "preferred_fields": ["OperatorStat.ToDo", "NodeStat.ToDo"],
        "node_filter": "Permission非空的当前可处理节点",
        "fallback": "空字符串",
    },
    "status_color_map": {
        "green": ["可做", "进行", "正常", "领取", "处理中"],
        "blue": ["等待", "排队", "待开始", "待领取"],
        "gray": ["结束", "不可见", "隐藏", "已完成", "关闭"],
        "red": ["异常", "失败", "错误", "失效"],
    },
}

CANARY_RULES = {
    **DEFAULT_RULES,
    "worker_claim_guard": {
        "require_account_binding": True,
        "emit_audit_event": True,
        "readonly_first": True,
    },
}


def dumps_rule(rule: dict) -> str:
    return json.dumps(rule, ensure_ascii=False, sort_keys=True, indent=2)


def loads_rule(rule_json: str) -> dict:
    try:
        value = json.loads(rule_json or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def flatten_rule(value: object, prefix: str = "") -> dict[str, str]:
    if isinstance(value, dict):
        result: dict[str, str] = {}
        for key, item in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.update(flatten_rule(item, next_prefix))
        return result
    return {prefix: json.dumps(value, ensure_ascii=False, sort_keys=True)}


def ensure_seed_rule_versions(db: Session) -> list[RuleVersion]:
    existing = list(db.scalars(select(RuleVersion).order_by(RuleVersion.id.desc())))
    if existing:
        return existing
    published = RuleVersion(
        version="rules-20260505-001",
        title="默认任务字段抽取规则",
        status=RuleVersionStatus.PUBLISHED,
        rule_json=dumps_rule(DEFAULT_RULES),
        changelog="建立任务名称、状态颜色、待处理数字的稳定抽取规则。",
        canary_percent=100,
        created_by="system",
        published_at=utc_now(),
    )
    canary = RuleVersion(
        version="rules-20260505-002",
        title="Worker 领取保护灰度规则",
        status=RuleVersionStatus.CANARY,
        rule_json=dumps_rule(CANARY_RULES),
        changelog="新增 Worker 领取前账号绑定和审计事件约束，先灰度 20%。",
        canary_percent=20,
        created_by="system",
        published_at=utc_now(),
    )
    db.add_all([published, canary])
    db.flush()
    db.add_all([
        RulePublishEvent(
            rule_version_id=published.id,
            action="publish",
            from_status="draft",
            to_status="published",
            canary_percent=100,
            message="初始化默认规则并设为当前发布版本。",
            actor="system",
            trace_id=uuid4().hex,
        ),
        RulePublishEvent(
            rule_version_id=canary.id,
            action="canary",
            from_status="draft",
            to_status="canary",
            canary_percent=20,
            message="初始化 Worker 领取保护规则灰度。",
            actor="system",
            trace_id=uuid4().hex,
        ),
    ])
    db.add_all([
        RuleHitStat(rule_version_id=published.id, rule_key="task_name_shortener", hits=36, misses=1, sample_task_name_id="RFT人标支持 GSB 评估7631***9721"),
        RuleHitStat(rule_version_id=published.id, rule_key="pending_permission_node", hits=24, misses=0, sample_task_name_id="video_bo7_正式队列（高优队列）7635***0761"),
        RuleHitStat(rule_version_id=canary.id, rule_key="worker_claim_guard", hits=5, misses=0, sample_task_name_id="灰度 Worker 领取事件"),
    ])
    db.flush()
    return list(db.scalars(select(RuleVersion).order_by(RuleVersion.id.desc())))


def list_rule_versions(db: Session) -> list[RuleVersion]:
    ensure_seed_rule_versions(db)
    return list(db.scalars(select(RuleVersion).order_by(RuleVersion.id.desc())))


def get_active_rule_version(db: Session) -> Optional[RuleVersion]:
    ensure_seed_rule_versions(db)
    return db.scalar(select(RuleVersion).where(RuleVersion.status == RuleVersionStatus.PUBLISHED).order_by(RuleVersion.published_at.desc().nullslast(), RuleVersion.id.desc()))


def list_publish_events(db: Session) -> list[RulePublishEvent]:
    ensure_seed_rule_versions(db)
    return list(db.scalars(select(RulePublishEvent).order_by(RulePublishEvent.created_at.desc(), RulePublishEvent.id.desc()).limit(20)))


def list_hit_stats(db: Session) -> list[RuleHitStat]:
    ensure_seed_rule_versions(db)
    return list(db.scalars(select(RuleHitStat).order_by(RuleHitStat.updated_at.desc(), RuleHitStat.id.desc()).limit(50)))


def create_rule_version(db: Session, payload: RuleVersionCreateRequest, actor: str = "operator") -> RuleVersion:
    if payload.version:
        version = payload.version
    else:
        next_id = (db.scalar(select(RuleVersion.id).order_by(RuleVersion.id.desc()).limit(1)) or 0) + 1
        version = f"rules-{datetime.now().strftime('%Y%m%d')}-{next_id:03d}"
    rule = payload.rule_json or DEFAULT_RULES
    item = RuleVersion(
        version=version,
        title=payload.title,
        status=RuleVersionStatus.DRAFT,
        rule_json=dumps_rule(rule),
        changelog=payload.changelog,
        canary_percent=0,
        created_by=actor,
    )
    db.add(item)
    db.flush()
    return item


def get_rule_version(db: Session, version_id: int) -> Optional[RuleVersion]:
    ensure_seed_rule_versions(db)
    return db.get(RuleVersion, version_id)


def set_rule_canary(db: Session, item: RuleVersion, percent: int, message: str = "", actor: str = "operator") -> RulePublishEvent:
    old_status = item.status.value
    item.status = RuleVersionStatus.CANARY
    item.canary_percent = percent
    item.published_at = utc_now()
    event = RulePublishEvent(
        rule_version_id=item.id,
        action="canary",
        from_status=old_status,
        to_status=item.status.value,
        canary_percent=percent,
        message=message or f"灰度发布 {percent}%",
        actor=actor,
        trace_id=uuid4().hex,
    )
    db.add(event)
    db.flush()
    return event


def publish_rule_version(db: Session, item: RuleVersion, message: str = "", actor: str = "operator") -> RulePublishEvent:
    old_status = item.status.value
    for current in db.scalars(select(RuleVersion).where(RuleVersion.status == RuleVersionStatus.PUBLISHED, RuleVersion.id != item.id)):
        current.status = RuleVersionStatus.ROLLED_BACK
        current.canary_percent = 0
    item.status = RuleVersionStatus.PUBLISHED
    item.canary_percent = 100
    item.published_at = utc_now()
    event = RulePublishEvent(
        rule_version_id=item.id,
        action="publish",
        from_status=old_status,
        to_status=item.status.value,
        canary_percent=100,
        message=message or "发布为当前正式规则版本。",
        actor=actor,
        trace_id=uuid4().hex,
    )
    db.add(event)
    db.flush()
    return event


def rollback_to_rule_version(db: Session, item: RuleVersion, message: str = "", actor: str = "operator") -> RulePublishEvent:
    old_status = item.status.value
    for current in db.scalars(select(RuleVersion).where(RuleVersion.status.in_([RuleVersionStatus.PUBLISHED, RuleVersionStatus.CANARY]), RuleVersion.id != item.id)):
        current.status = RuleVersionStatus.ROLLED_BACK
        current.canary_percent = 0
    item.status = RuleVersionStatus.PUBLISHED
    item.canary_percent = 100
    item.published_at = utc_now()
    event = RulePublishEvent(
        rule_version_id=item.id,
        action="rollback",
        from_status=old_status,
        to_status=item.status.value,
        canary_percent=100,
        message=message or f"回滚到 {item.version}。",
        actor=actor,
        trace_id=uuid4().hex,
    )
    db.add(event)
    db.flush()
    return event


def compare_rule_versions(db: Session, target: RuleVersion, base_id: Optional[int] = None) -> tuple[Optional[RuleVersion], list[RuleDiffItem]]:
    versions = list_rule_versions(db)
    base = db.get(RuleVersion, base_id) if base_id else next((item for item in versions if item.id != target.id), None)
    target_map = flatten_rule(loads_rule(target.rule_json))
    base_map = flatten_rule(loads_rule(base.rule_json)) if base else {}
    keys = sorted(set(base_map) | set(target_map))
    items: list[RuleDiffItem] = []
    for key in keys:
        base_value = base_map.get(key, "")
        target_value = target_map.get(key, "")
        if base_value == target_value:
            continue
        if not base_value:
            change_type = "added"
        elif not target_value:
            change_type = "removed"
        else:
            change_type = "changed"
        items.append(RuleDiffItem(key=key, change_type=change_type, base_value=base_value, target_value=target_value))
    return base, items


def hit_rate(stat: RuleHitStat) -> float:
    total = stat.hits + stat.misses
    return round((stat.hits / total) * 100, 2) if total else 0.0


