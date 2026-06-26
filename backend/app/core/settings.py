from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_prefix="AIDP_", extra="ignore")
    monitor_env: str = Field(default="dev", alias="AIDP_MONITOR_ENV")
    monitor_version: str = Field(default="0.1.0-p0", alias="AIDP_MONITOR_VERSION")
    api_prefix: str = Field(default="/api/v1", alias="AIDP_API_PREFIX")
    database_url: str = Field(default="postgresql+psycopg://aidp:aidp_dev_password@localhost:5432/aidp_monitor", alias="AIDP_DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="AIDP_REDIS_URL")
    cors_origins: str = Field(default="http://localhost:5173,http://127.0.0.1:5173", alias="AIDP_CORS_ORIGINS")
    cors_origin_regex: str = Field(
        default=r"^(https:\/\/([^/]+\.)?aidp\.juejin\.cn|chrome-extension:\/\/[a-p0-9]+|http:\/\/localhost(?::\d+)?|http:\/\/127\.0\.0\.1(?::\d+)?)$",
        alias="AIDP_CORS_ORIGIN_REGEX",
    )
    task_source_account_user_id: str = Field(default="", alias="AIDP_TASK_SOURCE_ACCOUNT_USER_ID")
    public_base_url: str = Field(default="http://localhost:8789", alias="AIDP_PUBLIC_BASE_URL")
    backup_local_retention_days: int = Field(default=7, alias="AIDP_BACKUP_LOCAL_RETENTION_DAYS")
    backup_external_retention_days: int = Field(default=30, alias="AIDP_BACKUP_EXTERNAL_RETENTION_DAYS")
    backup_cleanup_time: str = Field(default="03:30", alias="AIDP_BACKUP_CLEANUP_TIME")
    backup_local_root: str = Field(default="./data/backups/local", alias="AIDP_BACKUP_LOCAL_ROOT")
    backup_external_root: str = Field(default="/home/admin/aidp监控平台备份", alias="AIDP_BACKUP_EXTERNAL_ROOT")
    task_sample_root: str = Field(default="./data/redacted-samples", alias="AIDP_TASK_SAMPLE_ROOT")
    operation_recording_root: str = Field(default="./data/operation-recordings", alias="AIDP_OPERATION_RECORDING_ROOT")
    local_agent_release_root: str = Field(default="./data/local-agent/releases/packages", alias="AIDP_LOCAL_AGENT_RELEASE_ROOT")
    production_state_path: str = Field(default="./data/production-state.json", alias="AIDP_PRODUCTION_STATE_PATH")
    production_auto_refresh_enabled: bool = Field(default=False, alias="AIDP_PRODUCTION_AUTO_REFRESH_ENABLED")
    production_auto_refresh_interval_minutes: int = Field(default=15, alias="AIDP_PRODUCTION_AUTO_REFRESH_INTERVAL_MINUTES")
    production_auto_refresh_initial_delay_seconds: int = Field(default=30, alias="AIDP_PRODUCTION_AUTO_REFRESH_INITIAL_DELAY_SECONDS")
    production_dashboard_poll_seconds: int = Field(default=30, alias="AIDP_PRODUCTION_DASHBOARD_POLL_SECONDS")
    auto_create_tables: bool = Field(default=True, alias="AIDP_AUTO_CREATE_TABLES")
    session_accounts_path: str = Field(default="./data/session-accounts.json", alias="AIDP_SESSION_ACCOUNTS_PATH")
    account_metadata_path: str = Field(default="./data/account-metadata.json", alias="AIDP_ACCOUNT_METADATA_PATH")
    host_launcher_url: str = Field(default="http://127.0.0.1:8790", alias="AIDP_HOST_LAUNCHER_URL")
    host_launcher_internal_url: str = Field(default="", alias="AIDP_HOST_LAUNCHER_INTERNAL_URL")
    host_launcher_script_path: str = Field(default=r"D:\数据标注插件\Projects\aidp-monitor\host-launcher.ps1", alias="AIDP_HOST_LAUNCHER_SCRIPT_PATH")
    incident_ai_base_url: str = Field(default="", alias="AIDP_INCIDENT_AI_BASE_URL")
    incident_ai_api_key: str = Field(default="", alias="AIDP_INCIDENT_AI_API_KEY")
    incident_ai_model: str = Field(default="gpt-4.1-mini", alias="AIDP_INCIDENT_AI_MODEL")
    incident_ai_timeout_seconds: int = Field(default=30, alias="AIDP_INCIDENT_AI_TIMEOUT_SECONDS")
    ai_runtime_config_path: str = Field(default="./data/ai-runtime-config.json", alias="AIDP_AI_RUNTIME_CONFIG_PATH")
    earnings_config_path: str = Field(default="./data/earnings-config.json", alias="AIDP_EARNINGS_CONFIG_PATH")
    earnings_ledger_path: str = Field(default="./data/earnings-ledger.json", alias="AIDP_EARNINGS_LEDGER_PATH")
    notification_config_path: str = Field(default="./config/notifications.json", alias="AIDP_NOTIFICATION_CONFIG_PATH")
    feishu_webhook_url: str = Field(default="", alias="AIDP_FEISHU_WEBHOOK_URL")
    feishu_secret: str = Field(default="", alias="AIDP_FEISHU_SECRET")
    notify_enabled: bool = Field(default=False, alias="AIDP_NOTIFY_ENABLED")
    notify_min_level: str = Field(default="warn", alias="AIDP_NOTIFY_MIN_LEVEL")
    notify_events: str = Field(default="backend.error,backend.unhandled_exception,audit.error,worker.error,alert.evaluation.failed,alert.evaluation.warning", alias="AIDP_NOTIFY_EVENTS")
    notify_dry_run: bool = Field(default=False, alias="AIDP_NOTIFY_DRY_RUN")
    notify_cooldown_seconds: int = Field(default=300, alias="AIDP_NOTIFY_COOLDOWN_SECONDS")

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
