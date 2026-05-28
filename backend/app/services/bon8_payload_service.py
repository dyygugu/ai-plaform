import json
from copy import deepcopy
from typing import Any, Optional, Union


MODEL_KEYS = tuple(f"model{index}" for index in range(1, 9))
SCORE_FIELDS = (
    "sceneConsistencyScore",
    "objectCompletenessScore",
    "realismScore",
    "overallScore",
)
AUDIT_REMARK_FIELDS = {
    "checkRemark",
    "discard_remark",
    "videoLowScoreReason",
    "richness_reason",
}


def build_bon8_submit_temp_payload(
    *,
    task_id: str,
    node_id: Union[int, str],
    item_id: str,
    item_content: dict[str, Any],
    scores: dict[str, Any],
    sort_models: Optional[list[str]] = None,
    score_reasons: Optional[dict[str, Any]] = None,
    staging_time: str = "604800",
) -> dict[str, Any]:
    normalized_scores = _normalize_scores(scores)
    model_keys = [key for key in MODEL_KEYS if key in normalized_scores]
    if not model_keys:
        raise ValueError("bon8 至少需要一个 model 分数。")
    if sum(1 for value in normalized_scores.values() if value == "2") > 1:
        raise ValueError("bon8 只能有一个 2 分最佳产物。")

    data = _build_answer_data(
        normalized_scores,
        sort_models=sort_models or _default_sort(normalized_scores),
        score_reasons=_normalize_reasons(score_reasons or {}, normalized_scores),
    )
    content = {
        "item": deepcopy(item_content),
        "templateID": "7630728951002550067",
        "type": "neeko",
        "data": data,
        "dataMap": deepcopy(data),
        "itemID": str(item_id),
        "isAbandoned": False,
    }
    return {
        "AuditAnswers": [
            {
                "ItemID": str(item_id),
                "Content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                "ControlData": json.dumps({"Discard": False, "extraAnswer": []}, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "NodeID": str(node_id),
        "StagingTime": staging_time,
        "TaskID": str(task_id),
    }


def _build_answer_data(
    normalized_scores: dict[str, str],
    *,
    sort_models: list[str],
    score_reasons: dict[str, str],
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "discard": "No",
        "discard_type": [],
        "sceneConsistencyIssues": {},
        "objectCompletenessIssues": {},
        "realismIssues": {},
        "rotationConsistencyIssues": {},
        "rotationConsistencyScore": {},
        "anomalyLabel": {},
        "models_gsb": {},
        "sortModels": [key for key in sort_models if key in normalized_scores],
        "videoSceneConsistencyScore": {},
        "videoSceneConsistencyRemarks": {},
        "mappedModel": {},
        "modelRemarks": dict(score_reasons),
        "sceneConsistencyRemarks": dict(score_reasons),
        "lowScoreReason": {},
    }
    for field in SCORE_FIELDS:
        data[field] = dict(normalized_scores)

    for model_key, score in normalized_scores.items():
        if score == "0":
            data["sceneConsistencyIssues"][model_key] = ["视觉不足"]
            data["objectCompletenessIssues"][model_key] = ["功能不足"]
            data["lowScoreReason"][model_key] = score_reasons[model_key]
        elif score == "1":
            data["objectCompletenessIssues"][model_key] = ["功能不足"]
            data["lowScoreReason"][model_key] = score_reasons[model_key]

    for field in AUDIT_REMARK_FIELDS:
        data.pop(field, None)
    return data


def _normalize_scores(scores: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in scores.items():
        model_key = str(key)
        if model_key not in MODEL_KEYS:
            continue
        score = str(value).strip()
        if score not in {"0", "1", "2"}:
            raise ValueError(f"bon8 分数必须是 0/1/2：{model_key}={value}")
        normalized[model_key] = score
    return normalized


def _normalize_reasons(reasons: dict[str, Any], scores: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for model_key, score in scores.items():
        reason = str(reasons.get(model_key) or _default_reason(score)).strip()
        if not reason:
            reason = _default_reason(score)
        normalized[model_key] = reason
    return normalized


def _default_reason(score: str) -> str:
    if score == "0":
        return "存在明显瑕疵或核心内容缺失，视觉还原和功能完整度不足。"
    if score == "1":
        return "结构和还原度接近，但功能完整度仍有不足。"
    return "整体结构、核心内容和可用性最完整。"


def _default_sort(scores: dict[str, str]) -> list[str]:
    return sorted(scores, key=lambda key: (-int(scores[key]), key))
