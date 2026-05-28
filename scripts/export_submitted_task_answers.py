import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.services.submitted_item_read_service import load_account_with_cookie, read_all_submitted_task_payloads  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Read all submitted item ids and answers for one task with a runtime account cookie.")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--node-id", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    account = load_account_with_cookie(args.user_id)
    payload = read_all_submitted_task_payloads(
        account,
        args.task_id,
        node_id=args.node_id,
        page_size=args.page_size,
        batch_size=args.batch_size,
    )
    result = {
        "user_id": args.user_id,
        "account_name": str(account.get("name") or ""),
        "task_id": args.task_id,
        "node_id": args.node_id,
        "submitted_total": payload["submitted"]["submitted_total"],
        "status_counts": payload["submitted"]["status_counts"],
        "item_ids": payload["submitted"]["item_ids"],
        "answer_list": payload["answers"]["answer_list"],
    }
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(output_path), "submitted_total": result["submitted_total"]}, ensure_ascii=False, indent=2))
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
