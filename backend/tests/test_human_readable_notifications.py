import os
import json
import base64
import hashlib
import hmac
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Event, Process
from pathlib import Path

from app.core.settings import get_settings
from app.schemas.alerting import AlertEvaluationRequest, AlertIncident, SloSummaryResponse
from app.schemas.notification import NotificationConfigUpdate
from app.services import alerting_service
from app.services.alerting_service import _build_notification_preview
from app.services import notification_service
from app.services.alert_service import build_alert_message
from app.services.task_rules import utc_now


def _send_provider_outage_notification_in_process(tmpdir: str, sent_log: str, ready_event: Event, start_event: Event) -> None:
    os.environ["AIDP_PUBLIC_BASE_URL"] = "http://127.0.0.1:8789"
    os.environ["AIDP_NOTIFICATION_CONFIG_PATH"] = str(Path(tmpdir) / "notifications.json")
    os.environ["AIDP_NOTIFICATION_COOLDOWN_PATH"] = str(Path(tmpdir) / "notification-cooldown.json")
    os.environ.pop("AIDP_FEISHU_WEBHOOK_URL", None)
    os.environ.pop("AIDP_FEISHU_SECRET", None)
    os.environ.pop("AIDP_NOTIFY_ENABLED", None)
    os.environ.pop("AIDP_NOTIFY_DRY_RUN", None)
    get_settings.cache_clear()
    notification_service.LAST_SENT.clear()
    notification_service.COOLDOWN_LOCKS.clear()

    def _fake_send(webhook: str, secret: str, text: str) -> int:
        time.sleep(0.5)
        with open(sent_log, "a", encoding="utf-8") as file:
            file.write(text.splitlines()[0] + "\n")
        return 200

    notification_service._send_feishu_text = _fake_send
    ready_event.set()
    start_event.wait(timeout=5)
    notification_service.send_error_notification(
        event="worker.error",
        level="critical",
        message='{"error_code":"AI_PROVIDER_502","error_detail":"bad gateway"}',
        data={"worker_id": f"worker-{os.getpid()}", "account_user_id": f"account-{os.getpid()}", "task_id": "same-task"},
        trace_id=f"trace-process-{os.getpid()}",
    )


class HumanReadableNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_env = {
            "AIDP_PUBLIC_BASE_URL": os.environ.get("AIDP_PUBLIC_BASE_URL"),
            "AIDP_NOTIFICATION_CONFIG_PATH": os.environ.get("AIDP_NOTIFICATION_CONFIG_PATH"),
            "AIDP_NOTIFICATION_COOLDOWN_PATH": os.environ.get("AIDP_NOTIFICATION_COOLDOWN_PATH"),
            "AIDP_FEISHU_WEBHOOK_URL": os.environ.get("AIDP_FEISHU_WEBHOOK_URL"),
            "AIDP_FEISHU_SECRET": os.environ.get("AIDP_FEISHU_SECRET"),
            "AIDP_NOTIFY_ENABLED": os.environ.get("AIDP_NOTIFY_ENABLED"),
            "AIDP_NOTIFY_DRY_RUN": os.environ.get("AIDP_NOTIFY_DRY_RUN"),
            "AIDP_ALLOW_TEST_NOTIFICATION_SEND": os.environ.get("AIDP_ALLOW_TEST_NOTIFICATION_SEND"),
        }
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["AIDP_PUBLIC_BASE_URL"] = "http://127.0.0.1:8789"
        os.environ["AIDP_NOTIFICATION_CONFIG_PATH"] = str(Path(self._tmpdir.name) / "notifications.json")
        os.environ["AIDP_NOTIFICATION_COOLDOWN_PATH"] = str(Path(self._tmpdir.name) / "notification-cooldown.json")
        for key in ("AIDP_FEISHU_WEBHOOK_URL", "AIDP_FEISHU_SECRET", "AIDP_NOTIFY_ENABLED", "AIDP_NOTIFY_DRY_RUN"):
            os.environ.pop(key, None)
        os.environ["AIDP_ALLOW_TEST_NOTIFICATION_SEND"] = "true"
        get_settings.cache_clear()

    def tearDown(self) -> None:
        for key, value in self._previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        self._tmpdir.cleanup()

    def test_error_notification_explains_task_auth_failure_without_raw_json(self) -> None:
        text = notification_service._render_message(
            "worker.error",
            "critical",
            '{"error_code":"TASK_PAGE_AUTH_EXPIRED","error_detail":"cookie expired"}',
            "trace-auth-expired",
            {
                "worker_id": "worker-a",
                "account_user_id": "7630000000000000001",
                "task_id": "7650000000000000001",
                "token": "secret-token",
            },
        )

        self.assertIn("【紧急】账号登录失效", text)
        self.assertIn("处理级别：必须立刻处理", text)
        self.assertIn("问题出在：账号 7630000000000000001 的登录状态失效", text)
        self.assertIn("影响：这个账号不会继续自动做题或提交", text)
        self.assertIn("现在要做：去账号管理重新登录该账号", text)
        self.assertIn("排查编号：trace_id=trace-auth-expired", text)
        self.assertIn("技术事件：TASK_PAGE_AUTH_EXPIRED / worker.error", text)
        self.assertNotIn('"account_user_id"', text)
        self.assertNotIn("secret-token", text)

    def test_ai_provider_502_notification_explains_upstream_detail(self) -> None:
        text = notification_service._render_message(
            "worker.error",
            "error",
            '{"error_code":"AI_PROVIDER_502","error_detail":"HTTP 502 Bad Gateway from dashscope"}',
            "trace-ai-502",
            {
                "worker_id": "worker-a",
                "task_id": "task-a",
                "stage": "ai_draft",
                "step": "call_provider",
            },
        )

        self.assertIn("【一般】做题 AI 服务请求失败", text)
        self.assertIn("问题出在：做题 AI 上游返回 502/Bad Gateway", text)
        self.assertIn("HTTP 502 Bad Gateway from dashscope", text)
        self.assertIn("影响：本次做题会暂停或等待重试", text)
        self.assertIn("技术事件：AI_PROVIDER_502 / worker.error", text)

    def test_ai_provider_timeout_notification_explains_upstream_timeout(self) -> None:
        text = notification_service._render_message(
            "worker.error",
            "critical",
            '{"error_code":"AI_PROVIDER_TIMEOUT","error_detail":"HTTPSConnectionPool(host=\'dashscope.aliyuncs.com\'): Read timed out. (read timeout=25)"}',
            "trace-ai-timeout",
            {"worker_id": "worker-a", "task_id": "task-a", "stage": "answer", "step": "call_provider"},
        )

        self.assertIn("【紧急】做题 AI 响应超时", text)
        self.assertIn("问题出在：做题 AI 上游请求超时", text)
        self.assertIn("Read timed out", text)
        self.assertIn("现在要做：先观察是否自动恢复", text)

    def test_web_login_rate_limit_notification_explains_login_failures(self) -> None:
        text = notification_service._render_message(
            "backend.error",
            "warn",
            "WEB_LOGIN_RATE_LIMIT",
            "trace-login-rate-limit",
            {
                "error_code": "WEB_LOGIN_RATE_LIMIT",
                "path": "/api/v1/auth/login",
                "client_source": "203.0.113.10",
                "phone_masked": "176****1914",
            },
        )

        self.assertIn("【提醒】平台登录连续失败", text)
        self.assertIn("问题出在：平台登录连续失败，系统已临时限流", text)
        self.assertIn("176****1914", text)
        self.assertIn("影响：只是登录入口被临时保护", text)
        self.assertIn("现在要做：确认是否有人输错密码", text)
        self.assertIn("技术事件：WEB_LOGIN_RATE_LIMIT / backend.error", text)
        self.assertNotIn("平台内部服务异常", text)

    def test_alert_preview_uses_plain_language_sections(self) -> None:
        alert = build_alert_message(
            "采集连续失败",
            "warning",
            "主账号未配置",
            "任务页采集连续失败 3 次",
            "http://127.0.0.1:8789/alerts",
        )

        text = alert.render_feishu_text()

        self.assertIn("【提醒】采集连续失败", text)
        self.assertIn("处理级别：不一定要立刻处理", text)
        self.assertIn("问题出在：主账号未配置：任务页采集连续失败 3 次", text)
        self.assertIn("影响：相关任务可能无法继续采集或完成告警闭环", text)
        self.assertIn("现在要做：打开告警中心查看主账号未配置", text)
        self.assertIn(f"排查编号：trace_id={alert.trace_id}", text)
        self.assertIn("面板：http://127.0.0.1:8789/alerts", text)

    def test_unknown_error_does_not_echo_json_style_secrets(self) -> None:
        text = notification_service._render_message(
            "backend.unhandled_exception",
            "error",
            '{"error_detail":"request failed: {\\"token\\":\\"secret-token\\", \\"cookie\\":\\"sessionid=abc\\"}"}',
            "trace-secret",
            {},
        )

        self.assertIn("【一般】平台内部服务异常", text)
        self.assertIn("问题出在：平台后端处理请求时出错，具体原因已记录在技术日志中", text)
        self.assertNotIn("secret-token", text)
        self.assertNotIn("sessionid=abc", text)
        self.assertNotIn('{"error_detail"', text)

    def test_ai_provider_detail_redacts_standalone_provider_secrets(self) -> None:
        text = notification_service._render_message(
            "worker.error",
            "error",
            '{"error_code":"AI_PROVIDER_502","error_detail":"upstream rejected API key sk-test-secret-1234567890 and access AKIAABCDEFGHIJKLMNOP"}',
            "trace-provider-secret",
            {},
        )

        self.assertIn("问题出在：做题 AI 上游返回 502/Bad Gateway", text)
        self.assertNotIn("sk-test-secret-1234567890", text)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", text)
        self.assertIn("<redacted>", text)

    def test_error_code_effective_severity_controls_min_level(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "",
                    "secret": "",
                    "minLevel": "critical",
                    "events": ["worker.error"],
                    "dryRun": True,
                    "cooldownSec": 30,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        notification_service.LAST_SENT.clear()

        result = notification_service.send_error_notification(
            event="worker.error",
            level="error",
            message='{"error_code":"TASK_PAGE_AUTH_EXPIRED","error_detail":"cookie expired"}',
            data={"account_user_id": "7630000000000000001"},
            trace_id="trace-effective-severity",
        )

        self.assertTrue(result.ok)
        self.assertFalse(result.skipped)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.level, "critical")

    def test_env_webhook_does_not_force_notification_enabled_when_switch_is_false(self) -> None:
        os.environ["AIDP_FEISHU_WEBHOOK_URL"] = "https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN"
        os.environ["AIDP_FEISHU_SECRET"] = "sign-secret"
        os.environ["AIDP_NOTIFY_ENABLED"] = "false"
        get_settings.cache_clear()
        notification_service.LAST_SENT.clear()

        status = notification_service.get_notification_config_status()
        result = notification_service.send_error_notification(
            event="worker.error",
            level="error",
            message='{"error_code":"AI_PROVIDER_502","error_detail":"bad gateway"}',
            data={"worker_id": "worker-a", "account_user_id": "7630000000000000001", "task_id": "7650000000000000001"},
            trace_id="trace-disabled-env-webhook",
        )

        self.assertFalse(status.enabled)
        self.assertTrue(status.webhook_configured)
        self.assertFalse(status.sends_network)
        self.assertTrue(result.skipped)
        self.assertEqual(result.reason, "通知未启用。")

    def test_saved_config_can_enable_notification_when_default_env_switch_is_false(self) -> None:
        os.environ["AIDP_NOTIFY_ENABLED"] = "false"
        get_settings.cache_clear()

        status = notification_service.update_notification_config(
            NotificationConfigUpdate(
                enabled=True,
                webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN",
                secret="sign-secret",
                min_level="warn",
                dry_run=False,
                cooldown_seconds=300,
            )
        )

        self.assertTrue(status.enabled)
        self.assertTrue(status.webhook_configured)
        self.assertTrue(status.sends_network)

    def test_failed_status_is_urgent_not_general(self) -> None:
        alert = build_alert_message(
            "发布门禁未通过",
            "failed",
            "发布门禁",
            "必需门禁失败",
            "http://127.0.0.1:8789/alerts",
        )

        text = alert.render_feishu_text()

        self.assertIn("【紧急】发布门禁未通过", text)
        self.assertIn("处理级别：必须立刻处理", text)

    def test_warning_incident_keeps_reminder_severity_and_respects_min_level(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "",
                    "secret": "",
                    "minLevel": "error",
                    "events": ["alert.evaluation.warning"],
                    "dryRun": True,
                    "cooldownSec": 30,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        notification_service.LAST_SENT.clear()
        incident = {
            "key": "worker_offline",
            "title": "Worker 全部离线",
            "severity": "warning",
            "subject": "Worker",
            "reason": "没有在线 Worker",
            "recommended_action": "打开 Worker 管理页检查心跳。",
        }

        text = notification_service._render_message(
            "alert.evaluation.warning",
            "warn",
            "告警评估发现 1 条待处理：Worker 全部离线 / 没有在线 Worker",
            "trace-warning",
            {"incidents": [incident], "status": "warning"},
        )
        result = notification_service.send_error_notification(
            event="alert.evaluation.warning",
            level="warn",
            message="告警评估发现 1 条待处理：Worker 全部离线 / 没有在线 Worker",
            data={"incidents": [incident], "status": "warning"},
            trace_id="trace-warning-send",
        )

        self.assertIn("【提醒】1 条告警待处理：Worker 全部离线", text)
        self.assertTrue(result.skipped)
        self.assertEqual(result.level, "warn")
        self.assertEqual(result.reason, "低于通知等级阈值。")

    def test_cooldown_key_includes_affected_account(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "https://open.feishu.cn/webhook/test",
                    "secret": "",
                    "minLevel": "critical",
                    "events": ["worker.error"],
                    "dryRun": False,
                    "cooldownSec": 300,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sent_texts: list[str] = []
        original_send = notification_service._send_feishu_text
        notification_service.LAST_SENT.clear()
        notification_service._send_feishu_text = lambda webhook, secret, text: sent_texts.append(text) or 200
        try:
            first = notification_service.send_error_notification(
                event="worker.error",
                level="error",
                message='{"error_code":"TASK_PAGE_AUTH_EXPIRED","error_detail":"cookie expired"}',
                data={"account_user_id": "7630000000000000001"},
                trace_id="trace-account-a",
            )
            second = notification_service.send_error_notification(
                event="worker.error",
                level="error",
                message='{"error_code":"TASK_PAGE_AUTH_EXPIRED","error_detail":"cookie expired"}',
                data={"account_user_id": "7630000000000000002"},
                trace_id="trace-account-b",
            )
            duplicate = notification_service.send_error_notification(
                event="worker.error",
                level="error",
                message='{"error_code":"TASK_PAGE_AUTH_EXPIRED","error_detail":"cookie expired"}',
                data={"account_user_id": "7630000000000000001"},
                trace_id="trace-account-a-duplicate",
            )
        finally:
            notification_service._send_feishu_text = original_send

        self.assertTrue(first.sent)
        self.assertTrue(second.sent)
        self.assertTrue(duplicate.skipped)
        self.assertEqual(len(sent_texts), 2)
        self.assertIn("账号 7630000000000000001", sent_texts[0])
        self.assertIn("账号 7630000000000000002", sent_texts[1])

    def test_task_ai_provider_outage_cooldown_is_not_split_by_account_or_task(self) -> None:
        message = '{"error_code":"AI_PROVIDER_502","error_detail":"bad gateway"}'
        summary_a = notification_service._build_human_summary(
            "worker.error",
            "error",
            message,
            {"worker_id": "worker-a", "account_user_id": "7630000000000000001", "task_id": "7650000000000000001"},
        )
        summary_b = notification_service._build_human_summary(
            "worker.error",
            "error",
            message,
            {"worker_id": "worker-b", "account_user_id": "7630000000000000002", "task_id": "7650000000000000002"},
        )

        key_a = notification_service._cooldown_key(
            "worker.error",
            "error",
            summary_a,
            {"worker_id": "worker-a", "account_user_id": "7630000000000000001", "task_id": "7650000000000000001"},
        )
        key_b = notification_service._cooldown_key(
            "worker.error",
            "error",
            summary_b,
            {"worker_id": "worker-b", "account_user_id": "7630000000000000002", "task_id": "7650000000000000002"},
        )

        self.assertEqual(key_a, key_b)
        self.assertIn("provider=task_ai", key_a)

    def test_real_send_cooldown_survives_process_memory_reset(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "https://open.feishu.cn/webhook/test",
                    "secret": "",
                    "minLevel": "critical",
                    "events": ["worker.error"],
                    "dryRun": False,
                    "cooldownSec": 300,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sent_texts: list[str] = []
        original_send = notification_service._send_feishu_text
        notification_service.LAST_SENT.clear()
        notification_service._send_feishu_text = lambda webhook, secret, text: sent_texts.append(text) or 200
        try:
            first = notification_service.send_error_notification(
                event="worker.error",
                level="critical",
                message='{"error_code":"AI_PROVIDER_502","error_detail":"bad gateway"}',
                data={"worker_id": "worker-a", "account_user_id": "7630000000000000001", "task_id": "7650000000000000001"},
                trace_id="trace-persist-cooldown-a",
            )
            notification_service.LAST_SENT.clear()
            duplicate = notification_service.send_error_notification(
                event="worker.error",
                level="critical",
                message='{"error_code":"AI_PROVIDER_502","error_detail":"bad gateway"}',
                data={"worker_id": "worker-b", "account_user_id": "7630000000000000002", "task_id": "7650000000000000002"},
                trace_id="trace-persist-cooldown-b",
            )
        finally:
            notification_service._send_feishu_text = original_send

        self.assertTrue(first.sent)
        self.assertTrue(duplicate.skipped)
        self.assertEqual(duplicate.reason, "命中飞书通知冷却窗口。")
        self.assertEqual(len(sent_texts), 1)

    def test_task_ai_provider_outage_uses_at_least_hour_cooldown(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "https://open.feishu.cn/webhook/test",
                    "secret": "",
                    "minLevel": "critical",
                    "events": ["worker.error"],
                    "dryRun": False,
                    "cooldownSec": 300,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        message = '{"error_code":"AI_PROVIDER_502","error_detail":"HTTP 502 Bad Gateway"}'
        data = {"worker_id": "worker-a", "account_user_id": "account-a", "task_id": "task-a"}
        summary = notification_service._build_human_summary("worker.error", "critical", message, data)
        key = notification_service._cooldown_key("worker.error", "critical", summary, data)
        cooldown_path = Path(self._tmpdir.name) / "notification-cooldown.json"
        cooldown_path.write_text(json.dumps({key: time.time() - 600}, ensure_ascii=False), encoding="utf-8")
        notification_service.LAST_SENT.clear()
        sent_texts: list[str] = []
        original_send = notification_service._send_feishu_text
        notification_service._send_feishu_text = lambda webhook, secret, text: sent_texts.append(text) or 200
        try:
            result = notification_service.send_error_notification(
                event="worker.error",
                level="critical",
                message=message,
                data=data,
                trace_id="trace-provider-hour-cooldown",
            )
        finally:
            notification_service._send_feishu_text = original_send

        self.assertTrue(result.skipped)
        self.assertEqual(result.reason, "命中飞书通知冷却窗口。")
        self.assertEqual(sent_texts, [])

    def test_concurrent_task_ai_provider_outage_uses_single_real_send_cooldown_slot(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "https://open.feishu.cn/webhook/test",
                    "secret": "",
                    "minLevel": "critical",
                    "events": ["worker.error"],
                    "dryRun": False,
                    "cooldownSec": 300,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        thread_count = 5
        release_send = threading.Event()
        first_send_entered = threading.Event()
        send_lock = threading.Lock()
        send_enter_count = 0
        sent_texts: list[str] = []
        original_send = notification_service._send_feishu_text
        notification_service.LAST_SENT.clear()

        def _fake_send(webhook: str, secret: str, text: str) -> int:
            nonlocal send_enter_count
            with send_lock:
                send_enter_count += 1
                first_send_entered.set()
            release_send.wait(timeout=5)
            sent_texts.append(text)
            return 200

        notification_service._send_feishu_text = _fake_send
        try:
            with ThreadPoolExecutor(max_workers=thread_count) as executor:
                futures = [
                    executor.submit(
                        notification_service.send_error_notification,
                        event="worker.error",
                        level="critical",
                        message='{"error_code":"AI_PROVIDER_502","error_detail":"bad gateway"}',
                        data={"worker_id": f"worker-{index}", "account_user_id": f"account-{index}", "task_id": f"task-{index}"},
                        trace_id=f"trace-concurrent-provider-{index}",
                    )
                    for index in range(thread_count)
                ]
                self.assertTrue(first_send_entered.wait(timeout=5))
                release_send.set()
                results = [future.result(timeout=5) for future in futures]
        finally:
            notification_service._send_feishu_text = original_send

        self.assertEqual(sum(1 for item in results if item.sent), 1)
        self.assertEqual(sum(1 for item in results if item.skipped), thread_count - 1)
        self.assertEqual(len(sent_texts), 1)

    def test_multiprocess_task_ai_provider_outage_uses_single_real_send_cooldown_slot(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "https://open.feishu.cn/webhook/test",
                    "secret": "",
                    "minLevel": "critical",
                    "events": ["worker.error"],
                    "dryRun": False,
                    "cooldownSec": 300,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sent_log = str(Path(self._tmpdir.name) / "sent.log")
        ready_events = [Event(), Event()]
        start_event = Event()
        processes = [
            Process(
                target=_send_provider_outage_notification_in_process,
                args=(self._tmpdir.name, sent_log, ready_events[index], start_event),
            )
            for index in range(2)
        ]
        for process in processes:
            process.start()
        try:
            for ready_event in ready_events:
                self.assertTrue(ready_event.wait(timeout=10))
            start_event.set()
            for process in processes:
                process.join(timeout=15)
                self.assertEqual(process.exitcode, 0)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)

        sent_lines = Path(sent_log).read_text(encoding="utf-8").splitlines() if Path(sent_log).exists() else []
        self.assertEqual(len(sent_lines), 1)

    def test_cooldown_file_timestamp_wins_over_stale_process_memory_cache(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "https://open.feishu.cn/webhook/test",
                    "secret": "",
                    "minLevel": "critical",
                    "events": ["worker.error"],
                    "dryRun": False,
                    "cooldownSec": 300,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        message = '{"error_code":"AI_PROVIDER_502","error_detail":"bad gateway"}'
        data = {"worker_id": "worker-a", "account_user_id": "account-a", "task_id": "task-a"}
        summary = notification_service._build_human_summary("worker.error", "critical", message, data)
        key = notification_service._cooldown_key("worker.error", "critical", summary, data)
        recent_timestamp = time.time()
        stale_timestamp = recent_timestamp - 1000
        cooldown_path = Path(self._tmpdir.name) / "notification-cooldown.json"
        cooldown_path.write_text(json.dumps({key: recent_timestamp}, ensure_ascii=False), encoding="utf-8")
        notification_service.LAST_SENT.clear()
        notification_service.LAST_SENT[key] = stale_timestamp
        send_calls: list[str] = []
        original_send = notification_service._send_feishu_text
        notification_service._send_feishu_text = lambda webhook, secret, text: send_calls.append(text) or 200
        try:
            result = notification_service.send_error_notification(
                event="worker.error",
                level="critical",
                message=message,
                data=data,
                trace_id="trace-stale-memory-cache",
            )
        finally:
            notification_service._send_feishu_text = original_send

        self.assertTrue(result.skipped)
        self.assertEqual(result.reason, "命中飞书通知冷却窗口。")
        self.assertEqual(send_calls, [])

    def test_dry_run_does_not_consume_real_send_cooldown(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        dry_run_config = {
            "enabled": True,
            "provider": "feishu-webhook",
            "webhookUrl": "https://open.feishu.cn/webhook/test",
            "secret": "",
            "minLevel": "critical",
            "events": ["worker.error"],
            "dryRun": True,
            "cooldownSec": 300,
        }
        config_path.write_text(json.dumps(dry_run_config, ensure_ascii=False), encoding="utf-8")
        notification_service.LAST_SENT.clear()

        dry_run = notification_service.send_error_notification(
            event="worker.error",
            level="error",
            message='{"error_code":"TASK_PAGE_AUTH_EXPIRED","error_detail":"cookie expired"}',
            data={"account_user_id": "7630000000000000001"},
            trace_id="trace-dry-run",
        )

        real_config = {**dry_run_config, "dryRun": False}
        config_path.write_text(json.dumps(real_config, ensure_ascii=False), encoding="utf-8")
        original_send = notification_service._send_feishu_text
        notification_service._send_feishu_text = lambda webhook, secret, text: 200
        try:
            real = notification_service.send_error_notification(
                event="worker.error",
                level="error",
                message='{"error_code":"TASK_PAGE_AUTH_EXPIRED","error_detail":"cookie expired"}',
                data={"account_user_id": "7630000000000000001"},
                trace_id="trace-real-send",
            )
        finally:
            notification_service._send_feishu_text = original_send

        self.assertTrue(dry_run.dry_run)
        self.assertTrue(real.sent)

    def test_pytest_blocks_real_feishu_network_send_unless_explicitly_allowed(self) -> None:
        os.environ["AIDP_ALLOW_TEST_NOTIFICATION_SEND"] = "false"
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
                    "secret": "",
                    "minLevel": "warn",
                    "events": ["worker.error"],
                    "dryRun": False,
                    "cooldownSec": 30,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        original_send = notification_service._send_feishu_text
        notification_service._send_feishu_text = lambda webhook, secret, text: (_ for _ in ()).throw(AssertionError("pytest must not call real feishu sender"))
        try:
            result = notification_service.send_error_notification(
                event="worker.error",
                level="error",
                message='{"error_code":"AI_PROVIDER_502","error_detail":"bad gateway"}',
                data={"worker_id": "worker-test", "task_id": "task-test"},
                trace_id="trace-pytest-block",
            )
        finally:
            notification_service._send_feishu_text = original_send

        self.assertFalse(result.sent)
        self.assertTrue(result.skipped)
        self.assertIn("测试环境", result.reason)

    def test_alert_evaluation_preview_uses_real_notification_renderer(self) -> None:
        incident = AlertIncident(
            key="worker_offline",
            title="Worker 全部离线",
            severity="warning",
            status="open",
            subject="Worker",
            reason="没有在线 Worker",
            recommended_action="打开 Worker 管理页检查心跳。",
            evidence={"current": "0 个在线"},
        )

        preview = _build_notification_preview("warning", [incident], trace_id="trace-preview")
        real = notification_service.build_error_notification_text(
            event="alert.evaluation.warning",
            level="warn",
            message="告警评估发现 1 条待处理：Worker 全部离线 / 没有在线 Worker",
            data={"incidents": [incident.model_dump()], "status": "warning"},
            trace_id="trace-preview",
        )

        dynamic_prefixes = ("状态：", "发送：", "时间：")
        preview_core = "\n".join(line for line in preview.splitlines() if not line.startswith(dynamic_prefixes))
        real_core = "\n".join(line for line in real.splitlines() if not line.startswith(dynamic_prefixes))
        self.assertEqual(preview_core, real_core)

    def test_known_error_code_never_downgrades_caller_critical_level(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "",
                    "secret": "",
                    "minLevel": "critical",
                    "events": ["worker.error"],
                    "dryRun": True,
                    "cooldownSec": 30,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        notification_service.LAST_SENT.clear()

        result = notification_service.send_error_notification(
            event="worker.error",
            level="critical",
            message='{"error_code":"AI_PROVIDER_TIMEOUT","error_detail":"upstream timeout"}',
            data={"worker_id": "worker-a", "task_id": "task-a"},
            trace_id="trace-critical-timeout",
        )
        text = notification_service._render_message(
            "worker.error",
            "critical",
            '{"error_code":"AI_PROVIDER_TIMEOUT","error_detail":"upstream timeout"}',
            "trace-critical-timeout",
            {"worker_id": "worker-a", "task_id": "task-a"},
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.level, "critical")
        self.assertIn("【紧急】做题 AI 响应超时", text)

    def test_backend_exception_cooldown_key_includes_request_path(self) -> None:
        summary = notification_service._build_human_summary(
            "backend.unhandled_exception",
            "error",
            "database failed",
            {"method": "GET", "path": "/api/v1/accounts"},
        )

        key_a = notification_service._cooldown_key(
            "backend.unhandled_exception",
            "error",
            summary,
            {"method": "GET", "path": "/api/v1/accounts"},
        )
        key_b = notification_service._cooldown_key(
            "backend.unhandled_exception",
            "error",
            summary,
            {"method": "GET", "path": "/api/v1/tasks/catalog"},
        )

        self.assertIn("path=/api/v1/accounts", key_a)
        self.assertIn("path=/api/v1/tasks/catalog", key_b)
        self.assertNotEqual(key_a, key_b)

    def test_redact_removes_feishu_webhook_and_signed_query(self) -> None:
        redacted = notification_service._redact(
            "send failed: https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN?timestamp=123&sign=abc123"
        )

        self.assertNotIn("WEBHOOK_SECRET_TOKEN", redacted)
        self.assertNotIn("sign=abc123", redacted)
        self.assertIn("/hook/<redacted>", redacted)
        self.assertIn("sign=<redacted>", redacted)

    def test_signed_feishu_request_sends_signature_in_json_body(self) -> None:
        calls: list[dict[str, object]] = []
        original_post = notification_service.requests.post
        original_time = notification_service.time.time

        class _Response:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

        def _fake_post(url, json, timeout):
            calls.append({"url": url, "json": json, "timeout": timeout})
            return _Response()

        notification_service.requests.post = _fake_post
        notification_service.time.time = lambda: 1234567890
        try:
            status_code = notification_service._send_feishu_text(
                "https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN",
                "secret-for-sign",
                "hello",
            )
        finally:
            notification_service.requests.post = original_post
            notification_service.time.time = original_time

        expected_string_to_sign = "1234567890\nsecret-for-sign"
        expected_sign = base64.b64encode(hmac.new(expected_string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()).decode("utf-8")

        self.assertEqual(status_code, 200)
        self.assertEqual(calls[0]["url"], "https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN")
        self.assertEqual(calls[0]["json"]["timestamp"], "1234567890")
        self.assertEqual(calls[0]["json"]["sign"], expected_sign)
        self.assertEqual(calls[0]["json"]["msg_type"], "text")
        self.assertEqual(calls[0]["json"]["content"]["text"], "hello")

    def test_feishu_business_error_is_not_treated_as_success(self) -> None:
        original_post = notification_service.requests.post

        class _Response:
            status_code = 200
            text = '{"code":99991663,"msg":"invalid webhook"}'

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"code": 99991663, "msg": "invalid webhook"}

        notification_service.requests.post = lambda url, json, timeout: _Response()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                notification_service._send_feishu_text("https://open.feishu.cn/open-apis/bot/v2/hook/test", "", "hello")
        finally:
            notification_service.requests.post = original_post

        self.assertIn("飞书业务返回失败", str(ctx.exception))
        self.assertIn("99991663", str(ctx.exception))

    def test_feishu_business_error_does_not_consume_cooldown(self) -> None:
        config_path = Path(self._tmpdir.name) / "notifications.json"
        config_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "provider": "feishu-webhook",
                    "webhookUrl": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
                    "secret": "",
                    "minLevel": "critical",
                    "events": ["worker.error"],
                    "dryRun": False,
                    "cooldownSec": 300,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        original_send = notification_service._send_feishu_text
        notification_service.LAST_SENT.clear()
        notification_service._send_feishu_text = lambda webhook, secret, text: (_ for _ in ()).throw(RuntimeError("飞书业务返回失败：code=99991663 msg=invalid webhook"))
        try:
            failed = notification_service.send_error_notification(
                event="worker.error",
                level="critical",
                message='{"error_code":"TASK_PAGE_AUTH_EXPIRED"}',
                data={"account_user_id": "7630000000000000001"},
                trace_id="trace-feishu-business-failed",
            )
        finally:
            notification_service._send_feishu_text = original_send

        self.assertFalse(failed.ok)
        self.assertFalse(failed.sent)
        self.assertFalse(notification_service.LAST_SENT)

    def test_notification_status_masks_webhook_and_omitted_update_preserves_it(self) -> None:
        notification_service.update_notification_config(
            NotificationConfigUpdate(
                enabled=True,
                webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN",
                secret="sign-secret",
                min_level="error",
                dry_run=False,
                cooldown_seconds=90,
            )
        )

        status = notification_service.get_notification_config_status()
        preserved = notification_service.update_notification_config(
            NotificationConfigUpdate(
                enabled=True,
                webhook_url=None,
                secret=None,
                min_level="critical",
                dry_run=True,
                cooldown_seconds=120,
            )
        )
        raw_config = notification_service.read_notification_config()

        self.assertTrue(status.webhook_configured)
        self.assertNotIn("WEBHOOK_SECRET_TOKEN", status.webhook_url)
        self.assertIn("/hook/<redacted>", status.webhook_url)
        self.assertTrue(preserved.webhook_configured)
        self.assertEqual(raw_config["webhookUrl"], "https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN")

    def test_blank_notification_update_preserves_saved_webhook_and_secret(self) -> None:
        notification_service.update_notification_config(
            NotificationConfigUpdate(
                enabled=True,
                webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN",
                secret="sign-secret",
                min_level="error",
                dry_run=False,
                cooldown_seconds=90,
            )
        )

        result = notification_service.update_notification_config(
            NotificationConfigUpdate(
                enabled=True,
                webhook_url="",
                secret="",
                min_level="critical",
                dry_run=True,
                cooldown_seconds=120,
            )
        )
        raw_config = notification_service.read_notification_config()

        self.assertTrue(result.webhook_configured)
        self.assertTrue(result.secret_configured)
        self.assertEqual(raw_config["webhookUrl"], "https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN")
        self.assertEqual(raw_config["secret"], "sign-secret")

    def test_masked_webhook_update_preserves_saved_webhook(self) -> None:
        notification_service.update_notification_config(
            NotificationConfigUpdate(
                enabled=True,
                webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN",
                secret="sign-secret",
                min_level="error",
                dry_run=False,
                cooldown_seconds=90,
            )
        )
        masked_status = notification_service.get_notification_config_status()

        result = notification_service.update_notification_config(
            NotificationConfigUpdate(
                enabled=True,
                webhook_url=masked_status.webhook_url,
                secret=None,
                min_level="critical",
                dry_run=True,
                cooldown_seconds=120,
            )
        )
        raw_config = notification_service.read_notification_config()

        self.assertTrue(result.webhook_configured)
        self.assertEqual(raw_config["webhookUrl"], "https://open.feishu.cn/open-apis/bot/v2/hook/WEBHOOK_SECRET_TOKEN")

    def test_alert_evaluation_dry_run_does_not_send_external_notification(self) -> None:
        incident = AlertIncident(
            key="worker_offline",
            title="Worker 全部离线",
            severity="warning",
            status="open",
            subject="Worker",
            reason="没有在线 Worker",
            recommended_action="打开 Worker 管理页检查心跳。",
            evidence={"current": "0 个在线"},
        )
        slo = SloSummaryResponse(generated_at=utc_now(), overall_status="warning", indicators=[])
        original_build_slo = alerting_service.build_slo_summary
        original_build_incidents = alerting_service._build_incidents
        original_send = alerting_service.send_error_notification
        send_calls: list[dict[str, object]] = []

        class _DummyDb:
            def commit(self) -> None:
                return None

        def _fake_send(*args, **kwargs):
            send_calls.append({"args": args, "kwargs": kwargs})
            raise AssertionError("dry_run=true must not call send_error_notification")

        alerting_service.build_slo_summary = lambda db: slo
        alerting_service._build_incidents = lambda db, current_slo: [incident]
        alerting_service.send_error_notification = _fake_send
        try:
            response = alerting_service.evaluate_alerts(
                _DummyDb(),
                AlertEvaluationRequest(dry_run=True, write_audit=False, send_external=True),
            )
        finally:
            alerting_service.build_slo_summary = original_build_slo
            alerting_service._build_incidents = original_build_incidents
            alerting_service.send_error_notification = original_send

        self.assertEqual(send_calls, [])
        self.assertTrue(response.dry_run)
        self.assertIn("本次为 dry-run，未发送飞书。", response.notification_preview)


if __name__ == "__main__":
    unittest.main()
