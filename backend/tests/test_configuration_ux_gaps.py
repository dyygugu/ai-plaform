import os
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.settings import get_settings
from app.db.base import Base
from app.models.account import AccountStatus, AidpAccount
from app.models.ai import AiActionConfirmation
from app.models.audit import AuditLog
from app.models.backup import BackupJob
from app.models.ops import EarningsSnapshot, RestoreDrill
from app.models.score_loop import ScoreLoopCase
from app.models.task import RuntimeConfig, TaskCatalogEvent, TaskCatalogItem, TaskRuleConfig
from app.models.worker import Worker
from app.schemas.account import AccountMetadataUpdate
from app.schemas.earnings import EarningsPriceConfigUpdate
from app.schemas.notification import NotificationConfigUpdate
from app.services.account_service import update_account_metadata
from app.services.earnings_service import build_earnings_summary, update_earnings_price_config
from app.services.notification_service import get_notification_config_status, update_notification_config
from app.services.production_dashboard_service import build_production_dashboard


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@contextmanager
def _isolated_runtime_paths(tmp: str):
    root = Path(tmp)
    keys = {
        "AIDP_ACCOUNT_METADATA_PATH": root / "data" / "account-metadata.json",
        "AIDP_PRODUCTION_STATE_PATH": root / "data" / "production-state.json",
        "AIDP_SESSION_ACCOUNTS_PATH": root / "data" / "session-accounts.json",
        "AIDP_EARNINGS_CONFIG_PATH": root / "data" / "earnings-config.json",
        "AIDP_EARNINGS_LEDGER_PATH": root / "data" / "earnings-ledger.json",
        "AIDP_NOTIFICATION_CONFIG_PATH": root / "config" / "notifications.json",
        "AIDP_TASK_SOURCE_ACCOUNT_USER_ID": "",
    }
    previous = {key: os.environ.get(key) for key in keys}
    for key, value in keys.items():
        os.environ[key] = str(value)
    get_settings.cache_clear()
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


class ConfigurationUxGapTests(unittest.TestCase):
    def test_account_custom_name_and_note_are_persisted_and_visible_on_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            os.chdir(tmp)
            db = _session()
            try:
                with _isolated_runtime_paths(tmp):
                    account = AidpAccount(user_id="7630778503730253600", display_name="用户3600", status=AccountStatus.ACTIVE, auth_mode="client-cookie")
                    db.add(account)
                    db.commit()

                    updated = update_account_metadata(db, "7630778503730253600", AccountMetadataUpdate(custom_name="一号做题号", note="主跑评分题"))
                    dashboard = build_production_dashboard(db)
                    card = next(item for item in dashboard.accounts if item.user_id == "7630778503730253600")

                    self.assertEqual(updated.custom_name, "一号做题号")
                    self.assertEqual(updated.note, "主跑评分题")
                    self.assertEqual(card.custom_name, "一号做题号")
                    self.assertEqual(card.note, "主跑评分题")
                    self.assertEqual(card.display_name, "用户3600")
            finally:
                db.close()
                os.chdir(previous_cwd)

    def test_dashboard_marks_pending_only_accounts_as_auto_claim_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            os.chdir(tmp)
            db = _session()
            try:
                with _isolated_runtime_paths(tmp):
                    Path("data").mkdir(parents=True, exist_ok=True)
                    Path("data/production-state.json").write_text(
                        json.dumps(
                            {
                                "accounts": [
                                    {
                                        "userId": "7630000000000000004",
                                        "name": "阻塞账号",
                                        "cookie": "sessionid=blocked",
                                        "operationUrl": "https://aidp.juejin.cn/operation/lite/setting/account/personal-center?org=AIDP%20Coding&tab=2",
                                        "tasks": [
                                            {
                                                "id": "7638992213846740763",
                                                "name": "科研图任务",
                                                "poolPendingSubmit": 5,
                                                "frontendSubmittedCategory": {"receiveEnable": False},
                                            }
                                        ],
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

                    dashboard = build_production_dashboard(db)
                    card = next(item for item in dashboard.accounts if item.user_id == "7630000000000000004")
                    task = card.task_stats[0]

                    self.assertFalse(task.receive_enabled)
                    self.assertFalse(task.operation_url_ok)
                    self.assertTrue(task.auto_receive_ready)
                    self.assertIn("启动时会先自动点击“处理”", task.auto_receive_block_reason)
            finally:
                db.close()
                os.chdir(previous_cwd)

    def test_dashboard_allows_processing_task_to_auto_run_even_if_receive_enable_is_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            os.chdir(tmp)
            db = _session()
            try:
                with _isolated_runtime_paths(tmp):
                    Path("data").mkdir(parents=True, exist_ok=True)
                    Path("data/production-state.json").write_text(
                        json.dumps(
                            {
                                "accounts": [
                                    {
                                        "userId": "7630000000000000002",
                                        "name": "处理中账号",
                                        "cookie": "sessionid=ok",
                                        "operationUrl": "https://aidp.juejin.cn/operation/task-v2?page=1",
                                        "tasks": [
                                            {
                                                "id": "7639402643386830630",
                                                "name": "科研图全量",
                                                "frontendNotSubmitted": 1,
                                                "frontendRepairCount": 0,
                                                "poolPendingSubmit": 40981,
                                                "frontendSubmittedCategory": {"receiveEnable": False},
                                                "frontendCategoryTotalMap": {"0": 1},
                                            }
                                        ],
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

                    dashboard = build_production_dashboard(db)
                    card = next(item for item in dashboard.accounts if item.user_id == "7630000000000000002")
                    task = card.task_stats[0]

                    self.assertFalse(task.receive_enabled)
                    self.assertTrue(task.operation_url_ok)
                    self.assertTrue(task.auto_receive_ready)
                    self.assertEqual(task.auto_receive_block_reason, "")
            finally:
                db.close()
                os.chdir(previous_cwd)

    def test_dashboard_open_target_urls_follow_custom_api_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            previous_env = {
                "AIDP_API_PREFIX": os.environ.get("AIDP_API_PREFIX"),
                "AIDP_PUBLIC_BASE_URL": os.environ.get("AIDP_PUBLIC_BASE_URL"),
            }
            os.chdir(tmp)
            db = _session()
            try:
                with _isolated_runtime_paths(tmp):
                    os.environ["AIDP_API_PREFIX"] = "custom//api/"
                    os.environ["AIDP_PUBLIC_BASE_URL"] = "https://platform.51gugu.uk/"
                    get_settings.cache_clear()
                    Path("data").mkdir(parents=True, exist_ok=True)
                    Path("data/production-state.json").write_text(
                        json.dumps(
                            {
                                "accounts": [
                                    {
                                        "userId": "7630000000000000005",
                                        "name": "用户0005",
                                        "cookie": "sessionid=ok",
                                        "operationUrl": "https://aidp.juejin.cn/operation/task-v2?page=1",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )

                    dashboard = build_production_dashboard(db)
                    card = next(item for item in dashboard.accounts if item.user_id == "7630000000000000005")

                    self.assertEqual(
                        card.task_open_url,
                        "https://platform.51gugu.uk/custom/api/accounts/7630000000000000005/open-target/task",
                    )
                    self.assertEqual(
                        card.personal_open_url,
                        "https://platform.51gugu.uk/custom/api/accounts/7630000000000000005/open-target/personal",
                    )
            finally:
                for key, value in previous_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                get_settings.cache_clear()
                db.close()
                os.chdir(previous_cwd)

    def test_notification_webhook_config_can_be_saved_from_runtime_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with _isolated_runtime_paths(tmp):
                    result = update_notification_config(NotificationConfigUpdate(enabled=True, webhook_url="https://open.feishu.cn/webhook/test", min_level="error", dry_run=False, cooldown_seconds=90))
                    status = get_notification_config_status()

                    self.assertTrue(result.webhook_configured)
                    self.assertTrue(status.webhook_configured)
                    self.assertTrue(status.sends_network)
                    self.assertEqual(status.min_level, "error")
                    self.assertEqual(status.cooldown_seconds, 90)
            finally:
                os.chdir(previous_cwd)

    def test_earnings_price_config_drives_task_income_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = os.getcwd()
            os.chdir(tmp)
            db = _session()
            try:
                with _isolated_runtime_paths(tmp):
                    db.add(AidpAccount(user_id="7630778503730253600", display_name="用户3600", status=AccountStatus.ACTIVE, auth_mode="client-cookie"))
                    db.add(AidpAccount(user_id="pending-20260505174327", display_name="新账号待登录", status=AccountStatus.NEEDS_LOGIN, auth_mode="local-profile-pending"))
                    db.add(TaskCatalogItem(source_account_user_id="7630778503730253600", raw_task_name="评分", task_short_name="评分", task_id="task-1", task_name_id="评分task-1", pending_raw="0", task_status_raw="已交付"))
                    db.commit()

                    update_earnings_price_config(EarningsPriceConfigUpdate(unit_price=1.5, currency="CNY", billable_unit="交付题"))
                    summary = build_earnings_summary(db)

                    self.assertEqual(summary.price_config.unit_price, 1.5)
                    self.assertEqual(summary.task_income_items[0].delivered_total, 1)
                    self.assertEqual(summary.task_income_items[0].estimated_income, 1.5)
                    self.assertEqual(len(summary.task_income_items), 1)
                    self.assertEqual(summary.estimated_task_income_total, 1.5)
            finally:
                db.close()
                os.chdir(previous_cwd)


if __name__ == "__main__":
    unittest.main()
