from app.models.task import TaskStatusColor
from app.services.task_rules import build_task_name_id, build_task_short_name, extract_task_id, map_status_color


def test_extract_task_id() -> None:
    assert extract_task_id("RFT人标_美观度（6.5万）7634515789236309806") == "7634515789236309806"
    assert extract_task_id("RFT人标_美观度（6.5万） 7634***9806") == "7634***9806"


def test_build_task_short_name() -> None:
    assert build_task_short_name("RFT人标_美观度（6.5万）7634515789236309806") == "美观度（6.5万）"


def test_build_task_name_id() -> None:
    assert build_task_name_id("RFT人标_美观度（6.5万）7634515789236309806") == "美观度（6.5万）7634515789236309806"
    assert build_task_name_id("RFT人标_美观度（6.5万） 7634***9806") == "美观度（6.5万）7634***9806"


def test_map_status_color() -> None:
    assert map_status_color("进行中") == TaskStatusColor.GREEN
    assert map_status_color("排队中") == TaskStatusColor.BLUE
    assert map_status_color("已结束") == TaskStatusColor.GRAY
    assert map_status_color("失败") == TaskStatusColor.RED
    assert map_status_color("新状态") == TaskStatusColor.YELLOW
