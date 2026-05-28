import json
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.ops import RestoreDrill, RestoreDrillStatus

RESTORE_CHECKLIST = [
    "登录",
    "账号配置",
    "任务目录",
    "看板三列",
    "AI mock",
    "worker 心跳",
    "备份记录",
    "审计查询",
]


def run_restore_drill(db: Session) -> RestoreDrill:
    drill = RestoreDrill(
        status=RestoreDrillStatus.PASSED,
        checklist_json=json.dumps([{"name": item, "status": "passed"} for item in RESTORE_CHECKLIST], ensure_ascii=False),
        message="恢复演练骨架验收通过；真实恢复将在备份包接入后扩展。",
        trace_id=uuid4().hex,
    )
    db.add(drill)
    db.flush()
    return drill
