import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


RESEARCH_CHART_TASK_ID = "7638992213846740763"
RESEARCH_CHART_NODE_ID = "1"
RESEARCH_CHART_TASK_NAME = "RFT科研图表还原-正式(随机5000题)"
SUBMIT_TEMP_ENDPOINT = "/api/dispatch/SubmitTempItemAnswer"


def extract_research_chart_samples(recording_root: Path, task_id: str = RESEARCH_CHART_TASK_ID) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    latest_payload: dict[str, Any] = {}
    latest_content: dict[str, Any] = {}
    success_response_count = 0
    for path in sorted(recording_root.glob("opr-*.json"), key=lambda item: item.stat().st_mtime):
        text = path.read_text(encoding="utf-8-sig")
        if task_id not in text or "SubmitTempItemAnswer" not in text:
            continue
        wrapper = json.loads(text)
        recording = wrapper.get("recording") if isinstance(wrapper.get("recording"), dict) else wrapper
        for index, entry in enumerate(recording.get("network", [])):
            if not isinstance(entry, dict) or "SubmitTempItemAnswer" not in str(entry.get("url") or ""):
                continue
            body = str(entry.get("request_body") or entry.get("post_data") or "")
            if not body:
                continue
            try:
                payload = json.loads(body)
                answer = payload.get("AuditAnswers", [{}])[0]
                content = json.loads(answer.get("Content") or "{}")
            except (json.JSONDecodeError, TypeError, IndexError):
                continue
            if str(payload.get("TaskID") or recording.get("task_id") or "") != str(task_id):
                continue
            data = content.get("data") if isinstance(content.get("data"), dict) else {}
            item = content.get("item") if isinstance(content.get("item"), dict) else {}
            score_map = data.get("label_sorce") if isinstance(data.get("label_sorce"), dict) else {}
            reason_map = data.get("label_remark") if isinstance(data.get("label_remark"), dict) else {}
            score = str(score_map.get("model_image") or "").strip()
            reason = str(reason_map.get("model_image") or "").strip()
            response_body = str(entry.get("response_body") or "")
            if response_body and _base_resp_status_code(response_body) == 0:
                success_response_count += 1
            row = {
                "file": path.name,
                "network_index": index,
                "task_id": str(payload.get("TaskID") or task_id),
                "node_id": str(payload.get("NodeID") or RESEARCH_CHART_NODE_ID),
                "item_id": str(answer.get("ItemID") or content.get("itemID") or ""),
                "uid": str(item.get("uid") or ""),
                "has_image_gt": bool(item.get("image_gt")),
                "has_model_image": bool(item.get("model_image")),
                "score": score,
                "reason": reason,
                "discard": data.get("discard"),
            }
            rows.append(row)
            latest_payload = payload
            latest_content = content
    by_item: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["item_id"] or f"{row['file']}#{row['network_index']}"
        by_item[key] = row
    items = list(by_item.values())
    score_counts: dict[str, int] = {}
    examples_by_score: dict[str, list[dict[str, Any]]] = {"0": [], "1": [], "2": [], "blank": []}
    for row in items:
        score = row["score"] if row["score"] in {"0", "1", "2"} else "blank"
        score_counts[score] = score_counts.get(score, 0) + 1
        if len(examples_by_score.setdefault(score, [])) < 6:
            examples_by_score[score].append(_public_example(row))
    node_id = str(latest_payload.get("NodeID") or RESEARCH_CHART_NODE_ID) if latest_payload else RESEARCH_CHART_NODE_ID
    return {
        "task_name": RESEARCH_CHART_TASK_NAME,
        "task_id": task_id,
        "node_id": node_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recording_count": len({row["file"] for row in rows}),
        "submit_temp_request_count": len(rows),
        "success_response_count": success_response_count,
        "unique_items": len(items),
        "score_counts": score_counts,
        "field_mapping": {
            "image_gt": "item.image_gt",
            "model_image": "item.model_image",
            "uid": "item.uid",
            "score": "data.label_sorce.model_image",
            "reason": "data.label_remark.model_image",
            "discard": "data.discard",
        },
        "examples_by_score": examples_by_score,
        "latest_payload": latest_payload,
        "latest_content_preview": _content_preview(latest_content),
    }


def build_research_chart_ability_draft(summary: dict[str, Any]) -> str:
    examples = summary.get("examples_by_score") if isinstance(summary.get("examples_by_score"), dict) else {}
    lines = [
        f"# {RESEARCH_CHART_TASK_NAME} 做题能力草稿",
        "",
        "## 适用任务",
        f"- TaskID：{summary.get('task_id') or RESEARCH_CHART_TASK_ID}",
        f"- NodeID：{summary.get('node_id') or RESEARCH_CHART_NODE_ID}",
        "- 题型：科研图表还原对比。左图是原图，右图是 AI 生成图。",
        "",
        "## 读题材料",
        "- 原图：`item.image_gt`。",
        "- AI 生成图：`item.model_image`。",
        "- 图表标识：`item.uid`。",
        "",
        "## 严格评分规则",
        "- 2 分：只有文字、图表类型、点位/曲线/柱状位置、网格线、坐标轴、刻度、图例、比例、布局和数据表达完全符合才给 2 分。",
        "- 1 分：只允许约 95% 相似且仅存在轻微瑕疵，例如很小的文字间距、局部线宽或非关键视觉细节偏差。",
        "- 0 分：数据、文字、图表结构、点位、曲线趋势、网格、坐标刻度、比例、布局等出现稍大偏差，或图表主体不一致，一律给 0 分。",
        "- 不确定时给 0 分，并在原因中写明最关键的不一致点。",
        "- 默认 `discard=No`，不要因为低分废弃题目。",
        "",
        "## 输出格式和字段映射",
        "- AI 只输出 JSON：`{\"score\":\"0|1|2\",\"reason\":\"中文原因\",\"confidence\":\"high|medium|low\"}`。",
        "- 分数写入：`data.label_sorce.model_image`。",
        "- 理由写入：`data.label_remark.model_image`。",
        "- 保持：`data.discard=No`、`discard_type=[]`、`discard_remark=null`、`checkRemark=null`。",
        "",
        "## 样例参考",
        f"- 已解析录制包：{summary.get('recording_count', 0)} 个；唯一题目：{summary.get('unique_items', 0)} 个；分数分布：{json.dumps(summary.get('score_counts', {}), ensure_ascii=False)}。",
    ]
    for score in ["0", "1", "2"]:
        sample = (examples.get(score) or [{}])[0]
        if sample:
            lines.append(f"- {score} 分样例：`{sample.get('uid', '')}`，理由：{sample.get('reason', '')}")
    lines.extend(
        [
            "",
            "## 护栏",
            "- 本阶段只做 dry-run：生成本地 payload 预览和能力草稿，不写远端、不暂存、不正式提交。",
            "- 人工审核草稿通过后，才能进入下一阶段的暂存/端到端提交。",
            "- provider 输出缺字段、分数不在 0/1/2、理由为空或置信度低时，必须转人工复核。",
        ]
    )
    return "\n".join(lines) + "\n"


def build_research_chart_dry_run_payload(recorded_payload: dict[str, Any], *, score: str, reason: str) -> dict[str, Any]:
    if score not in {"0", "1", "2"}:
        raise ValueError("科研图 dry-run 分数必须是 0/1/2。")
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("科研图 dry-run 必须包含中文理由。")
    payload = deepcopy(recorded_payload)
    answers = payload.get("AuditAnswers")
    if not isinstance(answers, list) or not answers:
        raise ValueError("录制 payload 缺少 AuditAnswers。")
    content = json.loads(str(answers[0].get("Content") or "{}"))
    data = content.setdefault("data", {})
    if not isinstance(data, dict):
        raise ValueError("录制 Content.data 不是对象。")
    label_score = data.setdefault("label_sorce", {})
    label_reason = data.setdefault("label_remark", {})
    if not isinstance(label_score, dict) or not isinstance(label_reason, dict):
        raise ValueError("录制 label_sorce/label_remark 不是对象。")
    label_score["model_image"] = score
    label_reason["model_image"] = clean_reason
    data["discard"] = "No"
    data["discard_type"] = []
    data["discard_remark"] = None
    data["checkRemark"] = None
    answers[0]["Content"] = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return {
        "mode": "research_chart_dry_run",
        "task_id": str(payload.get("TaskID") or RESEARCH_CHART_TASK_ID),
        "node_id": str(payload.get("NodeID") or RESEARCH_CHART_NODE_ID),
        "item_id": str(answers[0].get("ItemID") or content.get("itemID") or ""),
        "allowed_endpoint": SUBMIT_TEMP_ENDPOINT,
        "writes_remote": False,
        "submits_remote": False,
        "payload": payload,
        "field_diff": {
            "data.label_sorce.model_image": score,
            "data.label_remark.model_image": clean_reason,
            "data.discard": "No",
        },
    }


def write_research_chart_artifacts(recording_root: Path, output_root: Path) -> dict[str, Any]:
    summary = extract_research_chart_samples(recording_root)
    draft = build_research_chart_ability_draft(summary)
    dry_run = build_research_chart_dry_run_payload(
        summary["latest_payload"],
        score="0",
        reason="dry-run保守示例：如文字、点位、网格或图表结构存在明显偏差则给0分。",
    )
    output_root.mkdir(parents=True, exist_ok=True)
    summary_path = output_root / "research-chart-samples-summary.json"
    draft_path = output_root / "research-chart-ability-draft.md"
    dry_run_path = output_root / "research-chart-dry-run-payload.json"
    summary_path.write_text(json.dumps(_without_large_payload(summary), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    draft_path.write_text(draft, encoding="utf-8")
    dry_run_path.write_text(json.dumps(dry_run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    draft_record = upsert_research_chart_ability_draft(output_root.parent / "ability-drafts.json", summary, draft)
    return {
        "summary_path": str(summary_path),
        "draft_path": str(draft_path),
        "dry_run_path": str(dry_run_path),
        "draft_record_id": draft_record["id"],
        "summary": _without_large_payload(summary),
    }


def upsert_research_chart_ability_draft(store_path: Path, summary: dict[str, Any], draft_text: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    if store_path.exists():
        data = json.loads(store_path.read_text(encoding="utf-8-sig"))
        items = data.get("items", []) if isinstance(data, dict) else data if isinstance(data, list) else []
    else:
        items = []
    existing = next((item for item in items if isinstance(item, dict) and str(item.get("task_id")) == RESEARCH_CHART_TASK_ID), None)
    record = {
        "id": existing.get("id") if existing else uuid4().hex,
        "version": existing.get("version") if existing else f"ability-{datetime.now(timezone.utc).strftime('%Y%m%d')}-research-chart",
        "status": "草稿",
        "task_name": RESEARCH_CHART_TASK_NAME,
        "task_id": RESEARCH_CHART_TASK_ID,
        "specific_rules": "科研图表还原严格对比：完全符合才2分，约95%轻微瑕疵才1分，数据/文字/图表等明显偏差为0分。",
        "sample_data": f"录制包 {summary.get('recording_count', 0)} 个，唯一题目 {summary.get('unique_items', 0)} 个，分数分布 {summary.get('score_counts', {})}。",
        "related_content": "本阶段范围为草稿+dry-run；证据在 data/task-abilities/research-chart-7638992213846740763/。",
        "system_ai_draft": draft_text,
        "system_ai_trace_id": "",
        "provider_status": "local_research_chart_draft",
        "next_step": "人工审核草稿内容；确认无误后再升级暂存或端到端提交链路。",
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
    }
    next_items = [item for item in items if not (isinstance(item, dict) and str(item.get("task_id")) == RESEARCH_CHART_TASK_ID)]
    next_items.insert(0, record)
    store_path.write_text(json.dumps({"items": next_items, "updated_at": now}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def _base_resp_status_code(response_body: str) -> Optional[int]:
    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError:
        return None
    base_resp = parsed.get("BaseResp") if isinstance(parsed, dict) else None
    if not isinstance(base_resp, dict):
        return None
    try:
        return int(base_resp.get("StatusCode"))
    except (TypeError, ValueError):
        return None


def _public_example(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": row.get("item_id", ""),
        "uid": row.get("uid", ""),
        "score": row.get("score", ""),
        "reason": row.get("reason", ""),
        "has_image_gt": bool(row.get("has_image_gt")),
        "has_model_image": bool(row.get("has_model_image")),
    }


def _content_preview(content: dict[str, Any]) -> dict[str, Any]:
    item = content.get("item") if isinstance(content.get("item"), dict) else {}
    data = content.get("data") if isinstance(content.get("data"), dict) else {}
    return {
        "uid": str(item.get("uid") or ""),
        "has_image_gt": bool(item.get("image_gt")),
        "has_model_image": bool(item.get("model_image")),
        "data_keys": sorted(str(key) for key in data.keys()),
    }


def _without_large_payload(summary: dict[str, Any]) -> dict[str, Any]:
    result = dict(summary)
    result.pop("latest_payload", None)
    return result


def main(argv: Optional[list[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    recording_root = Path(args[0]) if args else Path("../data/operation-recordings")
    output_root = Path(args[1]) if len(args) > 1 else Path("../data/task-abilities/research-chart-7638992213846740763")
    result = write_research_chart_artifacts(recording_root, output_root)
    summary = result["summary"]
    print(result["draft_path"])
    print(result["dry_run_path"])
    print(f"unique_items={summary['unique_items']} score_counts={summary['score_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
