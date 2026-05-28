import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Optional

from app.core.settings import get_settings
from app.schemas.ai_timer import (
    AiTimerEventCreate,
    AiTimerEventRead,
    AiTimerStageDuration,
    AiTimerStageSummary,
    AiTimerSummaryResponse,
)
from app.services.task_rules import utc_now

DEFAULT_EVENT_LOG = Path("data/ai-timer/events.jsonl")
DEFAULT_PRODUCTION_RUNS_ROOT = Path("data/production-runs")
STAGE_DISPLAY_NAMES = {
    "claim": "领题",
    "read": "读题",
    "render": "截图和渲染",
    "ai": "上游 AI 往返",
    "provider": "上游 AI 往返",
    "providerElapsed": "上游 AI 往返",
    "providerElapsedMs": "上游 AI 往返",
    "provider_elapsed_ms": "上游 AI 往返",
    "taskAi": "上游 AI 往返",
    "taskAI": "上游 AI 往返",
    "upstreamAi": "上游 AI 往返",
    "upstreamAiElapsed": "上游 AI 往返",
    "upstreamAiElapsedMs": "上游 AI 往返",
    "upstream_ai_elapsed_ms": "上游 AI 往返",
    "payload": "整理答案",
    "payloadBuild": "整理答案",
    "payload_build": "整理答案",
    "temp": "暂存答案",
    "verify": "提交前检查",
    "submit": "正式提交",
    "readback": "提交后回读",
    "categoryBefore": "读提交前状态",
    "submitTemp": "暂存答案",
    "verifySubmit": "提交前检查",
    "submitItem": "正式提交",
    "categoryAfter": "提交后回读",
}


def record_ai_timer_event(event: AiTimerEventCreate, event_log_path: Optional[Path] = None) -> AiTimerEventRead:
    data = event.model_dump()
    data["stages"] = _display_stages(event.stages)
    item = AiTimerEventRead(**data, recorded_at=utc_now())
    path = _resolve_path(event_log_path or DEFAULT_EVENT_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(_jsonable(item), ensure_ascii=False, separators=(",", ":")) + "\n")
    return item


def build_ai_timer_summary(
    *,
    event_log_path: Optional[Path] = None,
    production_runs_root: Optional[Path] = None,
    unit_price: float = 0,
    recent_limit: int = 10,
) -> AiTimerSummaryResponse:
    events = _load_timer_events(_resolve_path(event_log_path or DEFAULT_EVENT_LOG))
    events.extend(_load_bon8_http_results(_resolve_path(production_runs_root or DEFAULT_PRODUCTION_RUNS_ROOT)))
    events = [event for event in events if event.total_ms > 0]
    events = _dedupe_events(events)
    events.sort(key=lambda event: event.finished_at or event.recorded_at, reverse=True)
    totals = [event.total_ms for event in events]
    avg_total = round(sum(totals) / len(totals)) if totals else 0
    questions_per_hour = round(3600000 / avg_total, 2) if avg_total > 0 else 0
    stage_breakdown = _stage_breakdown(events)
    return AiTimerSummaryResponse(
        generated_at=utc_now(),
        total_items=len(events),
        submitted_items=sum(1 for event in events if event.status == "submitted"),
        avg_total_ms=avg_total,
        p50_total_ms=round(median(totals)) if totals else 0,
        p95_total_ms=_percentile(totals, 95),
        questions_per_hour=questions_per_hour,
        unit_price=round(float(unit_price or 0), 4),
        estimated_hourly_income=round(questions_per_hour * float(unit_price or 0), 2),
        slowest_stage=stage_breakdown[0] if stage_breakdown else AiTimerStageSummary(stage="无样本"),
        stage_breakdown=stage_breakdown,
        recent_items=events[:recent_limit],
        message=_summary_message(events, avg_total, questions_per_hour, unit_price),
    )


def _load_timer_events(path: Path) -> list[AiTimerEventRead]:
    if not path.exists():
        return []
    events: list[AiTimerEventRead] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            event = AiTimerEventRead(**json.loads(text))
            events.append(event.model_copy(update={"stages": _display_stages(event.stages)}))
        except Exception:
            continue
    return events


def _load_bon8_http_results(root: Path) -> list[AiTimerEventRead]:
    if not root.exists():
        return []
    events: list[AiTimerEventRead] = []
    for path in root.rglob("*http-submit-result.json"):
        data = _load_json(path)
        timings = data.get("timingsMs") if isinstance(data.get("timingsMs"), dict) else {}
        total = _int(timings.get("total"))
        if total <= 0:
            continue
        stages = [
            AiTimerStageDuration(stage=_stage_display_name(str(key)), duration_ms=_int(value))
            for key, value in timings.items()
            if key != "total" and _int(value) > 0
        ]
        has_upstream_ai_stage = any(stage.stage == "上游 AI 往返" for stage in stages)
        upstream_ai_ms = 0 if has_upstream_ai_stage else _upstream_ai_elapsed_ms(data)
        if upstream_ai_ms > 0:
            stages.insert(0, AiTimerStageDuration(stage=_stage_display_name("ai"), duration_ms=upstream_ai_ms))
            total = max(total + upstream_ai_ms, _total_elapsed_ms(data))
        events.append(
            AiTimerEventRead(
                account_user_id=str(data.get("userId") or ""),
                account_name=str(data.get("accountName") or ""),
                task_id=str(data.get("taskId") or ""),
                task_name="bon8",
                item_id=str(data.get("itemId") or path.stem),
                status="submitted" if data.get("ok") is not False else "failed",
                source="bon8_http_result",
                total_ms=total,
                stages=stages,
                finished_at=_parse_datetime(data.get("generatedAt")),
                recorded_at=_parse_datetime(data.get("generatedAt")) or utc_now(),
            )
        )
    return events


def _stage_breakdown(events: Iterable[AiTimerEventRead]) -> list[AiTimerStageSummary]:
    totals_by_stage: dict[str, int] = {}
    counts_by_stage: dict[str, int] = {}
    all_stage_total = 0
    for event in events:
        for stage in event.stages:
            if stage.duration_ms <= 0:
                continue
            totals_by_stage[stage.stage] = totals_by_stage.get(stage.stage, 0) + stage.duration_ms
            counts_by_stage[stage.stage] = counts_by_stage.get(stage.stage, 0) + 1
            all_stage_total += stage.duration_ms
    rows = [
        AiTimerStageSummary(
            stage=stage,
            avg_duration_ms=round(total / counts_by_stage[stage]),
            total_duration_ms=total,
            sample_count=counts_by_stage[stage],
            share_percent=round(total * 100 / all_stage_total, 1) if all_stage_total else 0,
        )
        for stage, total in totals_by_stage.items()
    ]
    return sorted(rows, key=lambda item: (-item.avg_duration_ms, item.stage))


def _dedupe_events(events: list[AiTimerEventRead]) -> list[AiTimerEventRead]:
    deduped: dict[tuple[Any, ...], AiTimerEventRead] = {}
    for event in events:
        key = (
            event.account_user_id,
            event.task_id,
            event.item_id,
            event.total_ms,
            tuple((stage.stage, stage.duration_ms) for stage in event.stages),
        )
        previous = deduped.get(key)
        if previous is None or _dedupe_rank(event) > _dedupe_rank(previous):
            deduped[key] = event
    return list(deduped.values())


def _dedupe_rank(event: AiTimerEventRead) -> tuple[int, datetime]:
    source_rank = 1 if event.source == "bon8_http_result" else 2
    timestamp = event.finished_at or event.recorded_at
    return (source_rank, timestamp)


def _percentile(values: list[int], percent: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percent / 100) - 1))
    return ordered[index]


def _summary_message(events: list[AiTimerEventRead], avg_total_ms: int, questions_per_hour: float, unit_price: float) -> str:
    if not events:
        return "暂无 AI 做题计时样本；真实做题或导入历史提交结果后会显示效率和收益。"
    return f"已汇总 {len(events)} 个计时样本，平均每题 {round(avg_total_ms / 1000, 2)} 秒，每小时大约能做 {questions_per_hour} 题，按单题价格 {unit_price} 元估算每小时收入。"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) >= 5 and (text[-5] in {"+", "-"}) and text[-3] != ":":
            text = text[:-2] + ":" + text[-2:]
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _int(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _upstream_ai_elapsed_ms(data: dict[str, Any]) -> int:
    for key in (
        "provider_elapsed_ms",
        "providerElapsedMs",
        "ai_provider_elapsed_ms",
        "aiProviderElapsedMs",
        "upstream_ai_elapsed_ms",
        "upstreamAiElapsedMs",
    ):
        value = _int(data.get(key))
        if value > 0:
            return value
    ai_result = data.get("aiResult")
    if isinstance(ai_result, dict):
        return _upstream_ai_elapsed_ms(ai_result)
    judgement = data.get("judgement")
    if isinstance(judgement, dict):
        return _upstream_ai_elapsed_ms(judgement)
    return 0


def _total_elapsed_ms(data: dict[str, Any]) -> int:
    for key in ("total_elapsed_ms", "totalElapsedMs", "elapsed_ms", "elapsedMs"):
        value = _int(data.get(key))
        if value > 0:
            return value
    return 0


def _jsonable(item: AiTimerEventRead) -> dict[str, Any]:
    return item.model_dump(mode="json")


def _display_stages(stages: list[AiTimerStageDuration]) -> list[AiTimerStageDuration]:
    return [AiTimerStageDuration(stage=_stage_display_name(stage.stage), duration_ms=stage.duration_ms) for stage in stages]


def _stage_display_name(stage: str) -> str:
    text = str(stage or "").strip()
    return STAGE_DISPLAY_NAMES.get(text, text or "未标注阶段")


def ai_timer_event_log_path() -> Path:
    root = Path(get_settings().production_state_path)
    base = root.parent if root.parent != Path("") else Path("data")
    return base / "ai-timer" / "events.jsonl"
