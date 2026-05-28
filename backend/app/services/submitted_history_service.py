import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from typing import Optional
from uuid import uuid4

from app.core.settings import get_settings
from app.schemas.submitted_history import (
    SubmittedHistoryListResponse,
    SubmittedHistorySampleRead,
    SubmittedHistoryStatsResponse,
    SubmittedHistorySyncResponse,
    TestsetGenerateResponse,
    TestsetRead,
)
from app.services.runtime_account_service import load_production_state
from app.services.submitted_item_read_service import load_account_with_cookie, read_all_submitted_task_payloads


def sync_submitted_history(task_id: str, *, account_id: str = "", node_id: int = 1, force: bool = False) -> SubmittedHistorySyncResponse:
    selected_account_id = account_id or get_settings().task_source_account_user_id
    account = load_account_with_cookie(selected_account_id)
    payload = read_all_submitted_task_payloads(account, task_id, node_id=node_id)
    task_name = _task_name(task_id)
    root = _submitted_history_root(task_id)
    samples_dir = root / "samples"
    raw_dir = root / "raw"
    samples_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    new_count = 0
    updated_count = 0
    skipped_count = 0
    items: list[dict[str, Any]] = []
    synced_at = _now()
    answer_list = payload["answers"]["answer_list"]
    submitted_items = payload["submitted"]["items"]
    item_by_id = {_item_id(item): item for item in submitted_items if _item_id(item)}
    for index, item_id in enumerate(payload["submitted"]["item_ids"], start=1):
        sample = _build_sample(
            task_id=task_id,
            task_name=task_name,
            account_id=selected_account_id,
            item_id=item_id,
            item=item_by_id.get(item_id, {}),
            submitted_nodes=answer_list.get(item_id) or [],
            synced_at=synced_at,
            fallback_uid=f"sample_{index:03d}",
        )
        sample_path = samples_dir / f"{sample['uid']}.json"
        raw_path = raw_dir / f"{sample['uid']}.json"
        exists = sample_path.exists()
        if exists and not force:
            skipped_count += 1
        else:
            if exists:
                updated_count += 1
            else:
                new_count += 1
            sample_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raw_payload = {
                "task_id": task_id,
                "item_id": item_id,
                "item": item_by_id.get(item_id, {}),
                "answer_list": answer_list.get(item_id) or [],
            }
            raw_path.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        items.append({"uid": sample["uid"], "item_id": item_id, "path": str(sample_path)})
    manifest = {
        "task_id": str(task_id),
        "task_name": task_name,
        "account_id": selected_account_id,
        "sample_count": len(items),
        "sample_pool_count": int(payload["submitted"]["submitted_total"] or len(items)),
        "sample_ids": [item["uid"] for item in items],
        "items": items,
        "last_synced_at": synced_at.isoformat(),
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return SubmittedHistorySyncResponse(
        task_id=str(task_id),
        task_name=task_name,
        account_id=selected_account_id,
        sample_count=len(items),
        sample_pool_count=int(manifest["sample_pool_count"]),
        new_count=new_count,
        updated_count=updated_count,
        skipped_count=skipped_count,
        manifest_path=str(manifest_path),
        last_synced_at=synced_at,
    )


def read_submitted_history_stats(task_id: str) -> SubmittedHistoryStatsResponse:
    manifest = _load_manifest(task_id)
    return SubmittedHistoryStatsResponse(
        task_id=str(task_id),
        task_name=str(manifest.get("task_name") or ""),
        sample_count=int(manifest.get("sample_count") or 0),
        sample_pool_count=int(manifest.get("sample_pool_count") or 0),
        last_synced_at=_parse_datetime(manifest.get("last_synced_at")),
        manifest_path=str(_submitted_history_root(task_id) / "manifest.json"),
    )


def list_submitted_history(task_id: str) -> SubmittedHistoryListResponse:
    manifest = _load_manifest(task_id)
    samples_dir = _submitted_history_root(task_id) / "samples"
    items: list[SubmittedHistorySampleRead] = []
    for uid in manifest.get("sample_ids") or []:
        sample_path = samples_dir / f"{uid}.json"
        if sample_path.exists():
            items.append(SubmittedHistorySampleRead(**json.loads(sample_path.read_text(encoding="utf-8-sig"))))
    return SubmittedHistoryListResponse(
        task_id=str(task_id),
        task_name=str(manifest.get("task_name") or ""),
        sample_count=len(items),
        items=items,
    )


def get_submitted_history_sample(task_id: str, uid: str) -> SubmittedHistorySampleRead:
    sample_path = _submitted_history_root(task_id) / "samples" / f"{_safe_uid(uid)}.json"
    if not sample_path.exists():
        raise FileNotFoundError(f"submitted history sample not found: {uid}")
    return SubmittedHistorySampleRead(**json.loads(sample_path.read_text(encoding="utf-8-sig")))


def generate_testset(task_id: str, *, sample_count: int = 10) -> TestsetGenerateResponse:
    manifest = _load_manifest(task_id)
    sample_ids = list((manifest.get("sample_ids") or [])[: max(1, int(sample_count))])
    return TestsetGenerateResponse(
        task_id=str(task_id),
        task_name=str(manifest.get("task_name") or ""),
        sample_count=len(sample_ids),
        sample_pool_count=int(manifest.get("sample_pool_count") or 0),
        sample_ids=sample_ids,
    )


def save_testset(task_id: str, sample_ids: list[str]) -> TestsetRead:
    manifest = _load_manifest(task_id)
    selected = [str(item) for item in sample_ids if str(item)]
    testset = {
        "task_id": str(task_id),
        "task_name": str(manifest.get("task_name") or ""),
        "sample_pool_count": int(manifest.get("sample_pool_count") or 0),
        "testset_id": f"testset-{uuid4().hex[:10]}",
        "sample_count": len(selected),
        "sample_ids": selected,
        "source": "submitted_history",
        "created_at": _now().isoformat(),
        "path": str(_testset_path(task_id)),
    }
    path = _testset_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(testset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return TestsetRead(**testset)


def read_testset(task_id: str) -> TestsetRead:
    path = _testset_path(task_id)
    if not path.exists():
        raise FileNotFoundError(f"testset not found for task {task_id}")
    return TestsetRead(**json.loads(path.read_text(encoding="utf-8-sig")))


def _submitted_history_root(task_id: str) -> Path:
    base = Path(get_settings().production_state_path)
    root = base.parent if base.parent != Path("") else Path("data")
    return root / "task-abilities" / str(task_id) / "submitted-history"


def _testset_path(task_id: str) -> Path:
    base = Path(get_settings().production_state_path)
    root = base.parent if base.parent != Path("") else Path("data")
    return root / "task-abilities" / str(task_id) / "testsets" / "current.json"


def _load_manifest(task_id: str) -> dict[str, Any]:
    path = _submitted_history_root(task_id) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"submitted history manifest not found for task {task_id}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _task_name(task_id: str) -> str:
    state = load_production_state()
    for account in state.get("accounts", []):
        if not isinstance(account, dict):
            continue
        for task in account.get("tasks", []):
            if isinstance(task, dict) and str(task.get("id") or "") == str(task_id):
                return str(task.get("name") or "")
    return str(task_id)


def _item_id(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    source = item.get("Item") if isinstance(item.get("Item"), dict) else item
    return str(source.get("ItemID") or "") if isinstance(source, dict) else ""


def _build_sample(
    *,
    task_id: str,
    task_name: str,
    account_id: str,
    item_id: str,
    item: dict[str, Any],
    submitted_nodes: list[dict[str, Any]],
    synced_at: datetime,
    fallback_uid: str,
) -> dict[str, Any]:
    primary_output = _primary_output(submitted_nodes)
    uid = _safe_uid(str(primary_output.get("item", {}).get("uid") or fallback_uid))
    return {
        "uid": uid,
        "item_id": str(item_id),
        "task_id": str(task_id),
        "task_name": task_name,
        "account_id": str(account_id),
        "source": "submitted_history_http",
        "submitted_nodes": submitted_nodes,
        "primary_output": primary_output,
        "raw": {"list_item": item, "answer_list": submitted_nodes},
        "synced_at": synced_at.isoformat(),
    }


def _primary_output(submitted_nodes: list[dict[str, Any]]) -> dict[str, Any]:
    for node in submitted_nodes:
        if not isinstance(node, dict):
            continue
        text = node.get("NodeAnswer")
        if not text:
            continue
        try:
            parsed = json.loads(str(text))
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _safe_uid(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\\\/:*?\"<>|#]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    return text[:180] or "sample"


def _now() -> datetime:
    return datetime.now(timezone.utc)
