import re
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.models.task import TaskStatusColor

TASK_ID_PATTERN = re.compile(r"((?:\d{12,})|(?:\d{4}\*{3}\d{4}))$")
DEFAULT_PREFIXES = ("RFT人标_", "RFT_", "人标_", "标注_")
GREEN_KEYWORDS = ("可做", "进行", "正常", "领取", "处理中")
BLUE_KEYWORDS = ("等待", "排队", "待开始", "待领取")
GRAY_KEYWORDS = ("结束", "不可见", "隐藏", "已完成", "关闭")
RED_KEYWORDS = ("异常", "失败", "错误", "失效")


def extract_task_id(raw_task_name: str) -> str:
    match = TASK_ID_PATTERN.search(raw_task_name.strip())
    return match.group(1) if match else ""


def build_task_short_name(
    raw_task_name: str,
    task_id: Optional[str] = None,
    prefixes: Iterable[str] = DEFAULT_PREFIXES,
    manual_short_name: Optional[str] = None,
) -> str:
    if manual_short_name and manual_short_name.strip():
        return manual_short_name.strip()
    name = raw_task_name.strip()
    resolved_task_id = task_id or extract_task_id(name)
    if resolved_task_id and name.endswith(resolved_task_id):
        name = name[: -len(resolved_task_id)].strip()
    for prefix in prefixes:
        if name.startswith(prefix):
            name = name[len(prefix) :].strip()
            break
    return name or raw_task_name.strip()


def build_task_name_id(
    raw_task_name: str,
    task_id: Optional[str] = None,
    prefixes: Iterable[str] = DEFAULT_PREFIXES,
    manual_short_name: Optional[str] = None,
) -> str:
    resolved_task_id = task_id or extract_task_id(raw_task_name)
    short_name = build_task_short_name(raw_task_name, resolved_task_id, prefixes, manual_short_name)
    return f"{short_name}{resolved_task_id}" if resolved_task_id else short_name


def map_status_color(status_raw: str) -> TaskStatusColor:
    status = status_raw.strip()
    if any(keyword in status for keyword in GREEN_KEYWORDS):
        return TaskStatusColor.GREEN
    if any(keyword in status for keyword in BLUE_KEYWORDS):
        return TaskStatusColor.BLUE
    if any(keyword in status for keyword in GRAY_KEYWORDS):
        return TaskStatusColor.GRAY
    if any(keyword in status for keyword in RED_KEYWORDS):
        return TaskStatusColor.RED
    return TaskStatusColor.YELLOW


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
