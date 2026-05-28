import unittest

from app.services.submitted_item_read_service import (
    read_all_submitted_task_payloads,
    read_submitted_item_answers,
    read_submitted_items,
)


def _item(item_id: str, status: int = 3) -> dict:
    return {"ItemID": item_id, "Status": status, "Content": "{\"itemID\":\"%s\"}" % item_id}


class SubmittedItemReadServiceTests(unittest.TestCase):
    def test_read_submitted_items_uses_category_type_one_and_paginates(self) -> None:
        calls = []

        def transport(account, kind, path, body):
            calls.append((account, kind, path, body))
            self.assertEqual(kind, "agw")
            self.assertEqual(path, "/dispatcher/search_item/category")
            self.assertEqual(body["ItemCategoryType"], 1)
            self.assertEqual(body["TaskID"], "task-1")
            page_no = int(body["PageRequest"]["PageNo"])
            if page_no == 0:
                return {
                    "BaseResp": {"StatusCode": 0},
                    "TabItemCategoryTotal": "3",
                    "TotalMap": {"0": 0, "1": 3},
                    "Data": [_item("item-1"), _item("item-2")],
                }
            return {
                "BaseResp": {"StatusCode": 0},
                "TabItemCategoryTotal": "3",
                "TotalMap": {"0": 0, "1": 3},
                "Data": [_item("item-3")],
            }

        result = read_submitted_items({"cookie": "sessionid=test"}, "task-1", transport=transport, page_size=2)

        self.assertEqual(result["submitted_total"], 3)
        self.assertEqual(result["item_ids"], ["item-1", "item-2", "item-3"])
        self.assertEqual(result["status_counts"], {"3": 3})
        self.assertEqual(len(calls), 2)

    def test_read_submitted_item_answers_batches_mget_answer_list(self) -> None:
        calls = []

        def transport(account, kind, path, body):
            calls.append((account, kind, path, body))
            self.assertEqual(kind, "api")
            self.assertEqual(path, "/api/dispatch/MGetAnswerList")
            batch = tuple(body["ItemIDs"])
            if batch == ("item-1", "item-2"):
                return {
                    "BaseResp": {"StatusCode": 0},
                    "AnswerList": {
                        "item-1": [{"NodeName": "标注", "NodeAnswer": "{\"item\":{\"title\":\"A\"}}"}],
                        "item-2": [{"NodeName": "质检", "NodeAnswer": "{\"item\":{\"title\":\"B\"}}"}],
                    },
                }
            return {
                "BaseResp": {"StatusCode": 0},
                "AnswerList": {
                    "item-3": [{"NodeName": "检查", "NodeAnswer": "{\"item\":{\"title\":\"C\"}}"}],
                    "item-4": [],
                },
            }

        result = read_submitted_item_answers(
            {"cookie": "sessionid=test"},
            "task-1",
            ["item-1", "item-2", "item-3", "item-4"],
            transport=transport,
            batch_size=2,
        )

        self.assertEqual(result["answer_key_count"], 4)
        self.assertEqual(result["nonempty_answer_key_count"], 3)
        self.assertEqual(result["answer_list"]["item-1"][0]["NodeName"], "标注")
        self.assertEqual(result["answer_list"]["item-4"], [])
        self.assertEqual(len(calls), 2)

    def test_read_all_submitted_task_payloads_combines_list_and_answers(self) -> None:
        def transport(account, kind, path, body):
            if path == "/dispatcher/search_item/category":
                return {
                    "BaseResp": {"StatusCode": 0},
                    "TabItemCategoryTotal": "2",
                    "TotalMap": {"0": 0, "1": 2},
                    "Data": [_item("item-1"), _item("item-2", status=7)],
                }
            if path == "/api/dispatch/MGetAnswerList":
                return {
                    "BaseResp": {"StatusCode": 0},
                    "AnswerList": {
                        "item-1": [{"NodeName": "标注", "NodeAnswer": "{\"item\":{\"kind\":\"one\"}}"}],
                        "item-2": [{"NodeName": "质检", "NodeAnswer": "{\"item\":{\"kind\":\"two\"}}"}],
                    },
                }
            raise AssertionError(path)

        result = read_all_submitted_task_payloads({"cookie": "sessionid=test"}, "task-1", transport=transport, page_size=10, batch_size=10)

        self.assertEqual(result["submitted"]["submitted_total"], 2)
        self.assertEqual(result["answers"]["nonempty_answer_key_count"], 2)
        self.assertEqual(result["sample_item_ids"], ["item-1", "item-2"])


if __name__ == "__main__":
    unittest.main()
