from pathlib import Path
import threading
import time

from app.services.production_account_refresh_service import refresh_production_account_by_user_id, refresh_production_accounts


def test_refresh_production_accounts_writes_native_state(tmp_path: Path) -> None:
    accounts = [
        {
            "userId": "account-sample-002",
            "name": "用户样例002",
            "enabled": True,
            "authMode": "client-cookie",
            "cookie": "sessionid=redacted",
            "tasks": [{"id": "old-task", "name": "旧任务", "nodeId": 4}],
        }
    ]

    def fake_transport(kind: str, path: str, body: dict, account: dict) -> dict:
        if path == "/api/crowdsourcingSettle/SummaryIncome":
            return {"CumulativeIncome": "12.5", "CurMonthIncome": "2", "PreMonthIncome": "1", "CashableIncome": "3", "AfterTaxCashableIncome": "2.5"}
        if path == "/api/dispatch/SearchTask":
            return {
                "Total": 1,
                "Tasks": [
                    {
                        "Task": {"TaskID": "task-1", "Title": "新任务"},
                        "Nodes": [{"Node": {"NodeID": 4, "Name": "后台节点"}}],
                    }
                ],
            }
        if path == "/dispatcher/search_item/category":
            return {"TabItemCategoryTotal": 5, "TotalMap": {"0": 7, "1": 11}, "Data": [], "ReceiveEnable": True}
        if path == "/llm/insights/get_progress_stat":
            return {"submitted_count": 13, "abandoned_count": 1, "in_progress_count": 2}
        raise AssertionError(f"unexpected {kind} {path}")

    response = refresh_production_accounts(accounts=accounts, state_path=tmp_path / "production-state.json", transport=fake_transport)

    assert response.ok is True
    assert response.refreshed_count == 1
    assert response.failed_count == 0
    assert response.items[0].task_count == 1
    state_text = (tmp_path / "production-state.json").read_text(encoding="utf-8")
    assert "task-1" in state_text
    assert "old-task" not in state_text
    assert "http-8789-native-category-progress" in state_text
    state = __import__("json").loads(state_text)
    assert state["nextRefreshAt"]


def test_refresh_production_accounts_preserves_task_page_pending_total(tmp_path: Path) -> None:
    accounts = [
        {
            "userId": "account-sample-002",
            "name": "用户样例002",
            "enabled": True,
            "authMode": "client-cookie",
            "cookie": "sessionid=redacted",
        }
    ]

    def fake_transport(kind: str, path: str, body: dict, account: dict) -> dict:
        if path == "/api/crowdsourcingSettle/SummaryIncome":
            return {}
        if path == "/api/dispatch/SearchTask":
            return {
                "Total": 1,
                "Tasks": [
                    {
                        "Task": {"TaskID": "task-1", "Title": "高优队列"},
                        "Nodes": [
                            {
                                "Node": {"NodeID": 4, "Name": "后台节点"},
                                "OperatorStat": {"ToDo": "13,489"},
                                "NodeStat": {"ToDo": 99},
                            }
                        ],
                    }
                ],
            }
        if path == "/dispatcher/search_item/category":
            return {"TabItemCategoryTotal": 0, "TotalMap": {"0": 0}, "Data": [], "ReceiveEnable": True}
        if path == "/llm/insights/get_progress_stat":
            return {}
        raise AssertionError(f"unexpected {kind} {path}")

    refresh_production_accounts(accounts=accounts, state_path=tmp_path / "production-state.json", transport=fake_transport)

    state = __import__("json").loads((tmp_path / "production-state.json").read_text(encoding="utf-8"))
    task = state["accounts"][0]["tasks"][0]
    assert task["poolPendingSubmit"] == 13489


def test_refresh_production_accounts_counts_repair_items_from_category_data(tmp_path: Path) -> None:
    accounts = [
        {
            "userId": "account-sample-002",
            "name": "用户样例002",
            "enabled": True,
            "authMode": "client-cookie",
            "cookie": "sessionid=redacted",
        }
    ]

    def fake_transport(kind: str, path: str, body: dict, account: dict) -> dict:
        if path == "/api/crowdsourcingSettle/SummaryIncome":
            return {}
        if path == "/api/dispatch/SearchTask":
            return {
                "Total": 1,
                "Tasks": [
                    {
                        "Task": {"TaskID": "task-repair", "Title": "返修队列"},
                        "Nodes": [{"Node": {"NodeID": 1, "Name": "标注"}, "Permission": ["process"], "OperatorStat": {"ToDo": 0}, "NodeStat": {"ToDo": 0}}],
                    }
                ],
            }
        if path == "/dispatcher/search_item/category":
            return {
                "TabItemCategoryTotal": 3,
                "TotalMap": {"0": 3, "1": 10, "3": 26},
                "Data": [
                    {"ItemID": "repair-1", "Status": 9},
                    {"ItemID": "normal-1", "Status": 0},
                    {"Item": {"ItemID": "repair-2", "Status": 9}},
                ],
                "ReceiveEnable": True,
            }
        if path == "/llm/insights/get_progress_stat":
            return {"submitted_count": 0, "abandoned_count": 0, "in_progress_count": 0}
        raise AssertionError(f"unexpected {kind} {path}")

    refresh_production_accounts(accounts=accounts, state_path=tmp_path / "production-state.json", transport=fake_transport)

    state = __import__("json").loads((tmp_path / "production-state.json").read_text(encoding="utf-8"))
    task = state["accounts"][0]["tasks"][0]
    assert task["frontendRepairCount"] == 2
    assert task["frontendNotSubmitted"] == 3
    assert task["poolPendingSubmit"] == 0


def test_refresh_production_accounts_paginates_category_data_for_repair_count(tmp_path: Path) -> None:
    accounts = [
        {
            "userId": "account-sample-002",
            "name": "用户样例002",
            "enabled": True,
            "authMode": "client-cookie",
            "cookie": "sessionid=redacted",
        }
    ]
    category_pages: list[int] = []

    def fake_transport(kind: str, path: str, body: dict, account: dict) -> dict:
        if path == "/api/crowdsourcingSettle/SummaryIncome":
            return {}
        if path == "/api/dispatch/SearchTask":
            return {
                "Total": 1,
                "Tasks": [
                    {
                        "Task": {"TaskID": "task-repair", "Title": "返修队列"},
                        "Nodes": [{"Node": {"NodeID": 1, "Name": "标注"}, "Permission": ["process"], "OperatorStat": {"ToDo": 0}, "NodeStat": {"ToDo": 0}}],
                    }
                ],
            }
        if path == "/dispatcher/search_item/category":
            page_no = int(body["PageRequest"]["PageNo"])
            category_pages.append(page_no)
            if page_no == 0:
                return {"TabItemCategoryTotal": 101, "TotalMap": {"0": 101}, "Data": [{"ItemID": f"repair-{index}", "Status": 9} for index in range(99)], "ReceiveEnable": True}
            if page_no == 1:
                return {"TabItemCategoryTotal": 101, "TotalMap": {"0": 101}, "Data": [{"ItemID": "repair-99", "Status": 9}, {"ItemID": "normal-100", "Status": 0}], "ReceiveEnable": True}
            raise AssertionError(f"unexpected category page {page_no}")
        if path == "/llm/insights/get_progress_stat":
            return {}
        raise AssertionError(f"unexpected {kind} {path}")

    refresh_production_accounts(accounts=accounts, state_path=tmp_path / "production-state.json", transport=fake_transport)

    state = __import__("json").loads((tmp_path / "production-state.json").read_text(encoding="utf-8"))
    assert state["accounts"][0]["tasks"][0]["frontendRepairCount"] == 100
    assert category_pages == [0, 1]


def test_refresh_production_accounts_uses_task_list_pending_node_before_backend_node(tmp_path: Path) -> None:
    accounts = [
        {
            "userId": "account-sample-002",
            "name": "用户样例002",
            "enabled": True,
            "authMode": "client-cookie",
            "cookie": "sessionid=redacted",
        }
    ]

    def fake_transport(kind: str, path: str, body: dict, account: dict) -> dict:
        if path == "/api/crowdsourcingSettle/SummaryIncome":
            return {}
        if path == "/api/dispatch/SearchTask":
            return {
                "Total": 1,
                "Tasks": [
                    {
                        "Task": {"TaskID": "task-1", "Title": "video_bo7_正式队列（高优队列）"},
                        "Nodes": [
                            {
                                "Node": {"NodeID": 1, "Name": "任务列表展示节点"},
                                "Permission": ["process"],
                                "OperatorStat": {"ToDo": "12,466"},
                                "NodeStat": {"ToDo": 12466},
                            },
                            {
                                "Node": {"NodeID": 4, "Name": "后台节点"},
                                "OperatorStat": {"ToDo": 8324},
                                "NodeStat": {"ToDo": 8324},
                            },
                        ],
                    }
                ],
            }
        if path == "/dispatcher/search_item/category":
            return {"TabItemCategoryTotal": 0, "TotalMap": {"0": 0}, "Data": [], "ReceiveEnable": True}
        if path == "/llm/insights/get_progress_stat":
            return {}
        raise AssertionError(f"unexpected {kind} {path}")

    refresh_production_accounts(accounts=accounts, state_path=tmp_path / "production-state.json", transport=fake_transport)

    state = __import__("json").loads((tmp_path / "production-state.json").read_text(encoding="utf-8"))
    task = state["accounts"][0]["tasks"][0]
    assert task["nodeId"] == 1
    assert task["poolPendingSubmit"] == 12466


def test_refresh_production_accounts_ignores_hidden_positive_pending_when_permission_node_is_zero(tmp_path: Path) -> None:
    accounts = [
        {
            "userId": "account-sample-002",
            "name": "用户样例002",
            "enabled": True,
            "authMode": "client-cookie",
            "cookie": "sessionid=redacted",
        }
    ]

    def fake_transport(kind: str, path: str, body: dict, account: dict) -> dict:
        if path == "/api/crowdsourcingSettle/SummaryIncome":
            return {}
        if path == "/api/dispatch/SearchTask":
            return {
                "Total": 1,
                "Tasks": [
                    {
                        "Task": {"TaskID": "task-2", "Title": "RFT人标支持 GSB 评估"},
                        "Nodes": [
                            {
                                "Node": {"NodeID": 1, "Name": "任务列表展示节点"},
                                "Permission": ["process"],
                                "OperatorStat": {"ToDo": 0},
                                "NodeStat": {"ToDo": 0},
                            },
                            {
                                "Node": {"NodeID": 4, "Name": "后台节点"},
                                "OperatorStat": {"ToDo": 0},
                                "NodeStat": {"ToDo": 0},
                            },
                            {
                                "Node": {"NodeID": 7, "Name": "无权限后台剩余节点"},
                                "OperatorStat": {"ToDo": "13,211"},
                                "NodeStat": {"ToDo": 13211},
                            },
                        ],
                    }
                ],
            }
        if path == "/dispatcher/search_item/category":
            return {"TabItemCategoryTotal": 0, "TotalMap": {"0": 0}, "Data": [], "ReceiveEnable": True}
        if path == "/llm/insights/get_progress_stat":
            return {}
        raise AssertionError(f"unexpected {kind} {path}")

    refresh_production_accounts(accounts=accounts, state_path=tmp_path / "production-state.json", transport=fake_transport)

    state = __import__("json").loads((tmp_path / "production-state.json").read_text(encoding="utf-8"))
    task = state["accounts"][0]["tasks"][0]
    assert task["nodeId"] == 1
    assert task["poolPendingSubmit"] == 0


def test_refresh_production_accounts_sums_multiple_visible_permission_nodes(tmp_path: Path) -> None:
    accounts = [
        {
            "userId": "account-sample-002",
            "name": "用户样例002",
            "enabled": True,
            "authMode": "client-cookie",
            "cookie": "sessionid=redacted",
        }
    ]

    def fake_transport(kind: str, path: str, body: dict, account: dict) -> dict:
        if path == "/api/crowdsourcingSettle/SummaryIncome":
            return {}
        if path == "/api/dispatch/SearchTask":
            return {
                "Total": 1,
                "Tasks": [
                    {
                        "Task": {"TaskID": "task-3", "Title": "多阶段可见任务"},
                        "Nodes": [
                            {
                                "Node": {"NodeID": 1, "Name": "标注"},
                                "Permission": ["process"],
                                "OperatorStat": {"ToDo": "12"},
                                "NodeStat": {"ToDo": 12},
                            },
                            {
                                "Node": {"NodeID": 4, "Name": "检查"},
                                "Permission": ["process"],
                                "OperatorStat": {"ToDo": "3"},
                                "NodeStat": {"ToDo": 3},
                            },
                            {
                                "Node": {"NodeID": 7, "Name": "隐藏后台"},
                                "OperatorStat": {"ToDo": "999"},
                                "NodeStat": {"ToDo": 999},
                            },
                        ],
                    }
                ],
            }
        if path == "/dispatcher/search_item/category":
            return {"TabItemCategoryTotal": 0, "TotalMap": {"0": 0}, "Data": [], "ReceiveEnable": True}
        if path == "/llm/insights/get_progress_stat":
            return {}
        raise AssertionError(f"unexpected {kind} {path}")

    refresh_production_accounts(accounts=accounts, state_path=tmp_path / "production-state.json", transport=fake_transport)

    state = __import__("json").loads((tmp_path / "production-state.json").read_text(encoding="utf-8"))
    task = state["accounts"][0]["tasks"][0]
    assert task["nodeId"] == 1
    assert task["poolPendingSubmit"] == 15


def test_refresh_production_account_by_user_id_filters_accounts(tmp_path: Path) -> None:
    accounts = [
        {"userId": "111111111111", "name": "用户111", "cookie": "sessionid=redacted"},
        {"userId": "222222222222", "name": "用户222", "cookie": "sessionid=redacted"},
    ]

    def fake_transport(kind: str, path: str, body: dict, account: dict) -> dict:
        if path == "/api/crowdsourcingSettle/SummaryIncome":
            return {}
        if path == "/api/dispatch/SearchTask":
            return {"Total": 0, "Tasks": []}
        raise AssertionError(f"unexpected {kind} {path}")

    response = refresh_production_account_by_user_id(
        "222222222222",
        accounts=accounts,
        state_path=tmp_path / "production-state.json",
        transport=fake_transport,
    )

    state_text = (tmp_path / "production-state.json").read_text(encoding="utf-8")
    assert response.refreshed_count == 1
    assert "用户222" in state_text
    assert "用户111" not in state_text


def test_refresh_production_accounts_prefers_real_display_names(tmp_path: Path) -> None:
    accounts = [{"userId": "333333333333", "name": "账号-333333", "cookie": "sessionid=redacted"}]

    def fake_transport(kind: str, path: str, body: dict, account: dict) -> dict:
        if path == "/api/crowdsourcingSettle/SummaryIncome":
            return {}
        if path == "/api/dispatch/SearchTask":
            return {"Total": 0, "Tasks": []}
        raise AssertionError(f"unexpected {kind} {path}")

    refresh_production_accounts(
        accounts=accounts,
        state_path=tmp_path / "production-state.json",
        transport=fake_transport,
        display_names={"333333333333": "用户33333333333"},
    )

    state_text = (tmp_path / "production-state.json").read_text(encoding="utf-8")
    assert "用户33333333333" in state_text
    assert "账号-333333" not in state_text


def test_refresh_production_accounts_refreshes_accounts_concurrently(tmp_path: Path) -> None:
    accounts = [
        {"userId": "111111111111", "name": "用户111", "cookie": "sessionid=redacted"},
        {"userId": "222222222222", "name": "用户222", "cookie": "sessionid=redacted"},
        {"userId": "333333333333", "name": "用户333", "cookie": "sessionid=redacted"},
    ]
    search_started = set()
    release_search = threading.Event()
    lock = threading.Lock()

    def fake_transport(kind: str, path: str, body: dict, account: dict) -> dict:
        if path == "/api/dispatch/SearchTask":
            with lock:
                search_started.add(account["userId"])
                if len(search_started) == len(accounts):
                    release_search.set()
            assert release_search.wait(1.5), "刷新仍是串行执行，后续账号未并发进入 SearchTask"
            return {"Total": 0, "Tasks": []}
        if path == "/api/crowdsourcingSettle/SummaryIncome":
            time.sleep(0.02)
            return {}
        raise AssertionError(f"unexpected {kind} {path}")

    response = refresh_production_accounts(
        accounts=accounts,
        state_path=tmp_path / "production-state.json",
        transport=fake_transport,
    )

    state = __import__("json").loads((tmp_path / "production-state.json").read_text(encoding="utf-8"))
    assert response.refreshed_count == 3
    assert response.failed_count == 0
    assert [item.user_id for item in response.items] == ["111111111111", "222222222222", "333333333333"]
    assert [account["userId"] for account in state["accounts"]] == ["111111111111", "222222222222", "333333333333"]
