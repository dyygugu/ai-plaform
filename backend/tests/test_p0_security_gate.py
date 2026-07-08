import importlib
import base64
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

MANAGED_ENV_KEYS = {
    "AIDP_DATABASE_URL",
    "AIDP_AUTO_CREATE_TABLES",
    "AIDP_API_PREFIX",
    "AIDP_PUBLIC_BASE_URL",
    "AIDP_MONITOR_ENV",
    "AIDP_AUTH_ENABLED",
    "AIDP_ADMIN_API_TOKEN",
    "AIDP_OPERATOR_API_TOKEN",
    "AIDP_READONLY_API_TOKEN",
    "AIDP_BROWSER_EXTENSION_API_TOKEN",
    "AIDP_WEB_LOGIN_PHONE",
    "AIDP_WEB_LOGIN_PASSWORD_HASH",
    "AIDP_WEB_SESSION_SECRET",
    "AIDP_WEB_SESSION_TTL_SECONDS",
    "AIDP_TRUSTED_PROXY_CIDRS",
    "AIDP_OPERATION_RECORDING_ROOT",
    "AIDP_PRODUCTION_STATE_PATH",
    "AIDP_SESSION_ACCOUNTS_PATH",
}


@pytest.fixture(autouse=True)
def _restore_auth_test_env():
    previous = {key: os.environ.get(key) for key in MANAGED_ENV_KEYS}
    _clear_login_failures()
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    _clear_login_failures()
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    for module_name in ["app.main", "app.db.init_db", "app.db.session"]:
        sys.modules.pop(module_name, None)


def _create_app(tmpdir: str, **env: str):
    for key in MANAGED_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["AIDP_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
    os.environ["AIDP_AUTO_CREATE_TABLES"] = "false"
    os.environ.update(env)
    for module_name in ["app.main", "app.db.init_db", "app.db.session"]:
        sys.modules.pop(module_name, None)
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    main_module = importlib.import_module("app.main")
    return main_module.create_app()


def test_public_platform_requires_api_token_but_keeps_health_public() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
        )
        with TestClient(app) as client:
            health = client.get("/api/v1/health")
            unauthenticated = client.get("/api/v1/settings/permissions")
            invalid = client.get("/api/v1/settings/permissions", headers={"Authorization": "Bearer wrong"})
            authenticated = client.get("/api/v1/settings/permissions", headers={"Authorization": "Bearer admin-secret"})

    assert health.status_code == 200, health.text
    assert unauthenticated.status_code == 401, unauthenticated.text
    assert "请先登录平台" in unauthenticated.text
    assert "API token" not in unauthenticated.text
    assert invalid.status_code == 403, invalid.text
    assert authenticated.status_code == 200, authenticated.text


def test_public_platform_allows_phone_password_login_and_session_token() -> None:
    password = "correct-password"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash(password),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app) as client:
            login = client.post("/api/v1/auth/login", json={"phone": "17600000001", "password": password})
            assert login.status_code == 200, login.text
            token = login.json().get("access_token")
            authenticated = client.get("/api/v1/settings/permissions", headers={"X-AIDP-API-Token": token})

    assert login.json()["token_type"] == "Bearer"
    assert token and token.startswith("web.")
    assert authenticated.status_code == 200, authenticated.text


def test_public_platform_web_login_works_without_admin_api_token() -> None:
    password = "correct-password"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash(password),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app) as client:
            unauthenticated = client.get("/api/v1/settings/permissions")
            login = client.post("/api/v1/auth/login", json={"phone": "17600000001", "password": password})
            assert login.status_code == 200, login.text
            token = login.json().get("access_token")
            authenticated = client.get("/api/v1/settings/permissions", headers={"X-AIDP-API-Token": token})

    assert unauthenticated.status_code == 401, unauthenticated.text
    assert token and token.startswith("web.")
    assert authenticated.status_code == 200, authenticated.text


def test_public_platform_accepts_compose_safe_colon_password_hash() -> None:
    password = "correct-password"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash(password).replace("$", ":"),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app) as client:
            login = client.post("/api/v1/auth/login", json={"phone": "17600000001", "password": password})

    assert login.status_code == 200, login.text
    assert login.json()["access_token"].startswith("web.")


def test_verify_password_returns_false_for_malformed_hash_parameters() -> None:
    security_module = importlib.import_module("app.core.security")

    assert security_module.verify_password("correct-password", "pbkdf2_sha256:-1:c2FsdA:ZGlnZXN0") is False


def test_public_paths_follow_custom_api_prefix() -> None:
    password = "correct-password"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_API_PREFIX="/custom-api",
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash(password),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app) as client:
            health = client.get("/custom-api/health")
            login = client.post("/custom-api/auth/login", json={"phone": "17600000001", "password": password})

    assert health.status_code == 200, health.text
    assert login.status_code == 200, login.text


@pytest.mark.parametrize(
    ("raw_prefix", "normalized_prefix"),
    [
        ("/custom-api/", "/custom-api"),
        ("custom-api/v2/", "/custom-api/v2"),
        ("/custom-api///", "/custom-api"),
    ],
)
def test_api_prefix_is_normalized_consistently_for_backend_routes(raw_prefix: str, normalized_prefix: str) -> None:
    password = "correct-password"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_API_PREFIX=raw_prefix,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash(password),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app) as client:
            health = client.get(f"{normalized_prefix}/health")
            login = client.post(f"{normalized_prefix}/auth/login", json={"phone": "17600000001", "password": password})
            runtime_config = client.get("/aidp-runtime-config.js")

    assert health.status_code == 200, health.text
    assert login.status_code == 200, login.text
    assert runtime_config.status_code == 200, runtime_config.text
    assert f'window.__AIDP_API_PREFIX__ = "{normalized_prefix}";' in runtime_config.text


def test_frontend_runtime_config_exposes_custom_api_prefix_without_auth() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_API_PREFIX="/custom-api",
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
        )
        with TestClient(app) as client:
            response = client.get("/aidp-runtime-config.js")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/javascript")
    assert 'window.__AIDP_API_PREFIX__ = "/custom-api";' in response.text


def test_unknown_custom_api_prefix_path_does_not_fall_back_to_spa() -> None:
    static_dir = BACKEND_ROOT / "frontend_dist"
    shutil.rmtree(static_dir, ignore_errors=True)
    try:
        (static_dir / "assets").mkdir(parents=True, exist_ok=True)
        (static_dir / "index.html").write_text("<!doctype html><title>AIDP</title>", encoding="utf-8")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            app = _create_app(
                tmpdir,
                AIDP_API_PREFIX="/custom-api",
                AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            )
            with TestClient(app) as client:
                response = client.get("/custom-api/not-a-real-route")
    finally:
        shutil.rmtree(static_dir, ignore_errors=True)

    assert response.status_code == 404, response.text
    assert "text/html" not in response.headers.get("content-type", "")


def test_public_platform_rejects_wrong_phone_password_login() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash("correct-password"),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app) as client:
            wrong_password = client.post("/api/v1/auth/login", json={"phone": "17600000001", "password": "wrong"})
            wrong_phone = client.post("/api/v1/auth/login", json={"phone": "17600000000", "password": "correct-password"})

    assert wrong_password.status_code == 401, wrong_password.text
    assert wrong_phone.status_code == 401, wrong_phone.text


def test_login_validation_error_does_not_echo_plaintext_password() -> None:
    leaked_password = "PlaintextShouldNotEcho"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash("correct-password"),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app) as client:
            response = client.post("/api/v1/auth/login", json={"password": leaked_password})

    assert response.status_code == 422, response.text
    assert leaked_password not in response.text
    assert "password" not in response.text.lower()


def test_public_platform_throttles_repeated_login_failures_and_alerts_once() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash("correct-password"),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with patch("app.api.v1.routes.auth.send_error_notification") as notify:
            with TestClient(app) as client:
                responses = [
                    client.post(
                        "/api/v1/auth/login",
                        json={"phone": "17600000001", "password": f"wrong-{index}"},
                        headers={"X-Forwarded-For": "203.0.113.10"},
                    )
                    for index in range(6)
                ]
                still_blocked = client.post(
                    "/api/v1/auth/login",
                    json={"phone": "17600000001", "password": "correct-password"},
                    headers={"X-Forwarded-For": "203.0.113.10"},
                )

    assert [response.status_code for response in responses[:5]] == [401, 401, 401, 401, 401]
    assert responses[5].status_code == 429, responses[5].text
    assert "失败次数过多" in responses[5].text
    assert still_blocked.status_code == 429, still_blocked.text
    notify.assert_called_once()
    _, kwargs = notify.call_args
    assert kwargs["event"] == "backend.error"
    assert kwargs["level"] == "warn"
    assert kwargs["data"]["error_code"] == "WEB_LOGIN_RATE_LIMIT"
    assert kwargs["data"]["phone_masked"] == "176****0001"
    assert "correct-password" not in str(kwargs)


def test_public_platform_throttles_login_failures_even_when_forwarded_for_rotates() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash("correct-password"),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app) as client:
            responses = [
                client.post(
                    "/api/v1/auth/login",
                    json={"phone": "17600000001", "password": f"wrong-{index}"},
                    headers={"X-Forwarded-For": f"203.0.113.{index}"},
                )
                for index in range(6)
            ]

    assert [response.status_code for response in responses[:5]] == [401, 401, 401, 401, 401]
    assert responses[5].status_code == 429, responses[5].text


def test_public_platform_ignores_spoofed_forwarded_for_from_untrusted_clients() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash("correct-password"),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app, client=("203.0.113.200", 50000)) as client:
            responses = [
                client.post(
                    "/api/v1/auth/login",
                    json={"phone": f"1760000000{index}", "password": f"wrong-{index}"},
                    headers={"X-Forwarded-For": f"203.0.113.{index}"},
                )
                for index in range(6)
            ]

    assert [response.status_code for response in responses[:5]] == [401, 401, 401, 401, 401]
    assert responses[5].status_code == 429, responses[5].text


def test_public_platform_uses_forwarded_client_only_from_trusted_proxy() -> None:
    password = "correct-password"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash(password),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
            AIDP_TRUSTED_PROXY_CIDRS="127.0.0.1/32",
        )
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            wrong_from_a = [
                client.post(
                    "/api/v1/auth/login",
                    json={"phone": f"1760000000{index}", "password": f"wrong-{index}"},
                    headers={"X-AIDP-Client-IP": "198.51.100.10"},
                )
                for index in range(6)
            ]
            correct_from_b = client.post(
                "/api/v1/auth/login",
                json={"phone": "17600000001", "password": password},
                headers={"X-AIDP-Client-IP": "198.51.100.20"},
            )

    assert [response.status_code for response in wrong_from_a[:5]] == [401, 401, 401, 401, 401]
    assert wrong_from_a[5].status_code == 429, wrong_from_a[5].text
    assert correct_from_b.status_code == 200, correct_from_b.text


def test_public_platform_does_not_trust_internal_client_ip_header_by_default() -> None:
    password = "correct-password"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash(password),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            wrong_from_a = [
                client.post(
                    "/api/v1/auth/login",
                    json={"phone": f"1760000000{index}", "password": f"wrong-{index}"},
                    headers={"X-AIDP-Client-IP": "198.51.100.10"},
                )
                for index in range(6)
            ]
            correct_from_b = client.post(
                "/api/v1/auth/login",
                json={"phone": "17600000001", "password": password},
                headers={"X-AIDP-Client-IP": "198.51.100.20"},
            )

    assert [response.status_code for response in wrong_from_a[:5]] == [401, 401, 401, 401, 401]
    assert wrong_from_a[5].status_code == 429, wrong_from_a[5].text
    assert correct_from_b.status_code == 429, correct_from_b.text


def test_public_platform_ignores_spoofable_forwarded_headers_even_from_trusted_proxy() -> None:
    password = "correct-password"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash(password),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
            AIDP_TRUSTED_PROXY_CIDRS="127.0.0.1/32",
        )
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            responses = [
                client.post(
                    "/api/v1/auth/login",
                    json={"phone": f"1760000000{index}", "password": f"wrong-{index}"},
                    headers={
                        "X-Forwarded-For": f"198.51.100.{index}",
                        "CF-Connecting-IP": f"203.0.113.{index}",
                    },
                )
                for index in range(6)
            ]

    assert [response.status_code for response in responses[:5]] == [401, 401, 401, 401, 401]
    assert responses[5].status_code == 429, responses[5].text


def test_public_platform_throttles_login_failures_even_when_phone_rotates() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash("correct-password"),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with TestClient(app) as client:
            responses = [
                client.post(
                    "/api/v1/auth/login",
                    json={"phone": f"1760000000{index}", "password": f"wrong-{index}"},
                )
                for index in range(6)
            ]

    assert [response.status_code for response in responses[:5]] == [401, 401, 401, 401, 401]
    assert responses[5].status_code == 429, responses[5].text


def test_public_platform_verifies_password_even_when_phone_is_wrong() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_WEB_LOGIN_PHONE="17600000001",
            AIDP_WEB_LOGIN_PASSWORD_HASH=_test_password_hash("correct-password"),
            AIDP_WEB_SESSION_SECRET="session-secret-for-test",
        )
        with patch("app.api.v1.routes.auth.verify_password", return_value=False) as verify:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/auth/login",
                    json={"phone": "17600000000", "password": "wrong"},
                )

    assert response.status_code == 401, response.text
    verify.assert_called_once()


def test_web_session_token_rejects_tampered_and_expired_tokens() -> None:
    os.environ["AIDP_PUBLIC_BASE_URL"] = "https://platform.51gugu.uk"
    os.environ["AIDP_ADMIN_API_TOKEN"] = "admin-secret"
    os.environ["AIDP_WEB_LOGIN_PHONE"] = "17600000001"
    os.environ["AIDP_WEB_SESSION_SECRET"] = "session-secret-for-test"
    os.environ["AIDP_WEB_SESSION_TTL_SECONDS"] = "60"
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    security_module = importlib.import_module("app.core.security")
    settings = settings_module.get_settings()

    valid = security_module.create_web_session_token(settings, "17600000001")
    tampered = valid[:-1] + ("a" if valid[-1] != "a" else "b")
    future = time.time() + 120

    assert security_module.require_api_auth(_request("GET", "/api/v1/settings/permissions", valid)).role == "admin"
    with pytest.raises(HTTPException) as tampered_exc:
        security_module.require_api_auth(_request("GET", "/api/v1/settings/permissions", tampered))
    with patch("app.core.security.time.time", return_value=future):
        with pytest.raises(HTTPException) as expired_exc:
            security_module.require_api_auth(_request("GET", "/api/v1/settings/permissions", valid))
    assert tampered_exc.value.status_code == 403
    assert expired_exc.value.status_code == 403


def test_successful_web_login_clears_phone_failures_but_preserves_source_cooldown() -> None:
    security_module = importlib.import_module("app.core.security")
    phone = "17600000001"

    for index in range(5):
        security_module.record_web_login_failure("old-source", phone, now=1000 + index)

    assert security_module.check_web_login_rate_limit("old-source", phone, now=1006).blocked is True

    security_module.record_web_login_success("new-source", phone)

    assert security_module.check_web_login_rate_limit("old-source", phone, now=1007).blocked is True
    assert security_module.check_web_login_rate_limit("new-source", phone, now=1007).blocked is False


def test_public_platform_fails_closed_when_no_token_is_configured() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(tmpdir, AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk")
        with TestClient(app) as client:
            response = client.get("/api/v1/settings/permissions")

    assert response.status_code == 503, response.text
    assert "管理员 token 或网页登录账号" in response.text


def test_public_platform_requires_admin_token_even_if_browser_token_exists() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_BROWSER_EXTENSION_API_TOKEN="browser-secret",
        )
        with TestClient(app) as client:
            response = client.get("/api/v1/settings/permissions", headers={"X-AIDP-API-Token": "browser-secret"})

    assert response.status_code == 503, response.text
    assert "管理员 token 或网页登录账号" in response.text


def test_public_platform_rejects_non_admin_mutations() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_OPERATOR_API_TOKEN="operator-secret",
        )
        with TestClient(app) as client:
            readonly = client.get("/api/v1/settings/permissions", headers={"X-AIDP-API-Token": "operator-secret"})
            mutation = client.post("/api/v1/accounts/7630778503730253620/delete", headers={"X-AIDP-API-Token": "operator-secret"})

    assert readonly.status_code == 200, readonly.text
    assert mutation.status_code == 403, mutation.text
    assert "admin" in mutation.text.lower()


def test_public_platform_allows_browser_extension_token_for_learning_package_upload() -> None:
    os.environ["AIDP_PUBLIC_BASE_URL"] = "https://platform.51gugu.uk"
    os.environ["AIDP_ADMIN_API_TOKEN"] = "admin-secret"
    os.environ["AIDP_BROWSER_EXTENSION_API_TOKEN"] = "browser-secret"
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    security_module = importlib.import_module("app.core.security")

    allowed = security_module.require_api_auth(_request("POST", "/api/v1/operation-recordings", "browser-secret"))
    assert allowed.role == "browser-extension"
    client_session = security_module.require_api_auth(_request("POST", "/api/client-session", "browser-secret"))
    assert client_session.role == "browser-extension"
    browser_open = security_module.require_api_auth(_request("GET", "/api/browser-open-session", "browser-secret"))
    assert browser_open.role == "browser-extension"
    release = security_module.require_api_auth(_request("GET", "/api/v1/local-agent/releases/latest/download-suite", "browser-secret"))
    assert release.role == "browser-extension"
    event = security_module.require_api_auth(_request("POST", "/api/v1/workers/events", "browser-secret"))
    assert event.role == "browser-extension"
    register = security_module.require_api_auth(_request("POST", "/api/v1/workers/register", "browser-secret"))
    assert register.role == "browser-extension"
    heartbeat = security_module.require_api_auth(_request("POST", "/api/v1/workers/heartbeat", "browser-secret"))
    assert heartbeat.role == "browser-extension"
    claim = security_module.require_api_auth(_request("POST", "/api/v1/workers/helper-1/commands/claim", "browser-secret"))
    assert claim.role == "browser-extension"
    renew = security_module.require_api_auth(_request("POST", "/api/v1/workers/commands/cmd-1/renew", "browser-secret"))
    assert renew.role == "browser-extension"
    execution_gate = security_module.require_api_auth(_request("POST", "/api/v1/workers/commands/cmd-1/execution-gate", "browser-secret"))
    assert execution_gate.role == "browser-extension"
    result = security_module.require_api_auth(_request("POST", "/api/v1/workers/commands/cmd-1/result", "browser-secret"))
    assert result.role == "browser-extension"
    preflight = security_module.require_api_auth(_request("POST", "/api/v1/task-auto-runs/preflight", "browser-secret"))
    assert preflight.role == "browser-extension"

    with pytest.raises(HTTPException) as exc_info:
        security_module.require_api_auth(_request("POST", "/api/v1/accounts/7630778503730253620/delete", "browser-secret"))
    assert exc_info.value.status_code == 403
    with pytest.raises(HTTPException) as read_exc:
        security_module.require_api_auth(_request("GET", "/api/v1/settings/permissions", "browser-secret"))
    assert read_exc.value.status_code == 403
    with pytest.raises(HTTPException) as worker_exc:
        security_module.require_api_auth(_request("POST", "/api/v1/workers/helper-1/approve", "browser-secret"))
    assert worker_exc.value.status_code == 403


def test_browser_extension_token_permissions_follow_custom_api_prefix() -> None:
    os.environ["AIDP_API_PREFIX"] = "/custom-api"
    os.environ["AIDP_PUBLIC_BASE_URL"] = "https://platform.51gugu.uk"
    os.environ["AIDP_ADMIN_API_TOKEN"] = "admin-secret"
    os.environ["AIDP_BROWSER_EXTENSION_API_TOKEN"] = "browser-secret"
    settings_module = importlib.import_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    security_module = importlib.import_module("app.core.security")

    allowed_paths = [
        ("POST", "/custom-api/operation-recordings"),
        ("POST", "/custom-api/accounts/client-session"),
        ("GET", "/custom-api/local-agent/releases/latest/download-suite"),
        ("POST", "/custom-api/workers/events"),
        ("POST", "/custom-api/workers/register"),
        ("POST", "/custom-api/workers/heartbeat"),
        ("POST", "/custom-api/workers/helper-1/commands/claim"),
        ("POST", "/custom-api/workers/commands/cmd-1/renew"),
        ("POST", "/custom-api/workers/commands/cmd-1/execution-gate"),
        ("POST", "/custom-api/workers/commands/cmd-1/result"),
        ("POST", "/custom-api/task-auto-runs/preflight"),
        ("POST", "/api/client-session"),
        ("GET", "/api/browser-open-session"),
    ]

    for method, path in allowed_paths:
        principal = security_module.require_api_auth(_request(method, path, "browser-secret"))
        assert principal.role == "browser-extension", (method, path)

    with pytest.raises(HTTPException) as read_exc:
        security_module.require_api_auth(_request("GET", "/custom-api/settings/permissions", "browser-secret"))
    assert read_exc.value.status_code == 403
    with pytest.raises(HTTPException) as write_exc:
        security_module.require_api_auth(_request("POST", "/custom-api/accounts/7630778503730253620/delete", "browser-secret"))
    assert write_exc.value.status_code == 403


def test_public_platform_hides_docs_and_openapi() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
        )
        with TestClient(app) as client:
            docs = client.get("/api/v1/docs", headers={"Authorization": "Bearer admin-secret"})
            openapi = client.get("/api/v1/openapi.json", headers={"Authorization": "Bearer admin-secret"})

    assert docs.status_code == 404, docs.text
    assert openapi.status_code == 404, openapi.text


def test_public_platform_blocks_legacy_production_start_routes() -> None:
    headers = {"Authorization": "Bearer admin-secret"}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
        )
        with TestClient(app) as client:
            direct_auto_run = client.post(
                "/api/v1/task-auto-runs/start",
                json={"task_id": "task-prod", "account_user_ids": ["account-1"]},
                headers=headers,
            )
            old_auto_production = client.post(
                "/api/v1/tasks/task-prod/auto-production/production/start",
                json={"account_scope": {"mode": "specified", "account_user_ids": ["account-1"]}},
                headers=headers,
            )

    assert direct_auto_run.status_code == 410, direct_auto_run.text
    assert "AI 标注能力工作台" in direct_auto_run.text
    assert old_auto_production.status_code == 410, old_auto_production.text
    assert "AI 标注能力工作台" in old_auto_production.text


def test_public_platform_blocks_existing_generic_worker_start_route() -> None:
    from app.schemas.task_auto_runs import TaskAutoRunAccountState, TaskAutoRunResponse
    from app.services.task_auto_run_service import _write_run_state
    from app.services.task_rules import utc_now

    headers = {"Authorization": "Bearer admin-secret"}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
        )
        with TestClient(app) as client:
            state_dir = Path(tmpdir) / "task-auto-runs"
            app.state.task_auto_run_state_dir = state_dir
            run = TaskAutoRunResponse(
                generated_at=utc_now(),
                run_id="task-auto-existing-generic",
                adapter_key="research_chart",
                adapter_run_id="research-chart-existing",
                task_id="7639402643386830630",
                status="running_auto",
                selected_account_count=1,
                healthy_account_count=1,
                accounts=[TaskAutoRunAccountState(account_user_id="account-1", status="running_auto")],
            )
            _write_run_state(run, state_dir=state_dir)
            response = client.post(
                "/api/v1/task-auto-runs/runs/task-auto-existing-generic/worker/start",
                json={"interval_seconds": 1},
                headers=headers,
            )

    assert response.status_code == 410, response.text
    assert "Step4" in response.text


def test_public_platform_blocks_existing_bon8_worker_start_route() -> None:
    from app.schemas.task_auto_runs import TaskAutoRunAccountState, TaskAutoRunResponse
    from app.services.task_auto_run_service import _write_run_state
    from app.services.task_rules import utc_now

    headers = {"Authorization": "Bearer admin-secret"}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
        )
        with TestClient(app) as client:
            state_dir = Path(tmpdir) / "task-auto-runs"
            app.state.task_auto_run_state_dir = state_dir
            run = TaskAutoRunResponse(
                generated_at=utc_now(),
                run_id="task-auto-existing-bon8",
                adapter_key="bon8",
                adapter_run_id="bon8-existing",
                task_id="7635735855801536539",
                status="running_auto",
                selected_account_count=1,
                healthy_account_count=1,
                accounts=[TaskAutoRunAccountState(account_user_id="account-1", status="running_auto")],
            )
            _write_run_state(run, state_dir=state_dir)
            response = client.post(
                "/api/v1/task-auto-runs/runs/task-auto-existing-bon8/worker/start",
                json={"interval_seconds": 1},
                headers=headers,
            )

    assert response.status_code == 410, response.text
    assert "Step4" in response.text


def test_public_platform_blocks_all_bon8_legacy_write_routes() -> None:
    headers = {"Authorization": "Bearer admin-secret"}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
        )
        with TestClient(app) as client:
            responses = [
                client.post("/api/v1/bon8-production/start", json={"account_user_ids": ["account-1"]}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/confirmations/confirm-1/approve", json={}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/confirmations/confirm-1/reject", json={"rejected_reason": "no"}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/stop", json={}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/submit-first-item", json={}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/prepare-first-review", json={}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/plan-account-ticks", json={}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/execute-tick", json={}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/worker/start", json={}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/worker/stop", json={}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/accounts/account-1/operation-needed", json={}, headers=headers),
                client.post("/api/v1/bon8-production/runs/run-1/accounts/account-1/execute-tick", json={}, headers=headers),
            ]

    assert all(response.status_code == 410 for response in responses), [response.text for response in responses]


def test_browser_open_session_token_is_one_time_use() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        session_accounts = Path(tmpdir) / "session-accounts.json"
        session_accounts.write_text(
            """
{
  "accounts": [
    {
      "userId": "7630778503730253620",
      "name": "用户07040602572",
      "cookie": "sessionid=test-cookie",
      "enabled": true
    }
  ]
}
""".strip(),
            encoding="utf-8",
        )
        os.environ["AIDP_SESSION_ACCOUNTS_PATH"] = str(session_accounts)
        os.environ["AIDP_PRODUCTION_STATE_PATH"] = str(Path(tmpdir) / "production-state.json")
        settings_module = importlib.import_module("app.core.settings")
        settings_module.get_settings.cache_clear()
        service = importlib.import_module("app.services.production_dashboard_service")

        created = service.create_browser_open_session("7630778503730253620", "task")
        first = service.get_browser_open_session(created["token"])
        second = service.get_browser_open_session(created["token"])

    assert first["ok"] is True
    assert first["cookie"] == "sessionid=test-cookie"
    assert second["ok"] is False
    assert "过期" in second["error"] or "不存在" in second["error"]


def test_browser_open_session_token_is_one_time_use_under_concurrent_consumers() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        session_accounts = Path(tmpdir) / "session-accounts.json"
        session_accounts.write_text(
            """
{
  "accounts": [
    {
      "userId": "7630778503730253620",
      "name": "用户07040602572",
      "cookie": "sessionid=test-cookie",
      "enabled": true
    }
  ]
}
""".strip(),
            encoding="utf-8",
        )
        os.environ["AIDP_SESSION_ACCOUNTS_PATH"] = str(session_accounts)
        os.environ["AIDP_PRODUCTION_STATE_PATH"] = str(Path(tmpdir) / "production-state.json")
        settings_module = importlib.import_module("app.core.settings")
        settings_module.get_settings.cache_clear()
        service = importlib.import_module("app.services.production_dashboard_service")
        created = service.create_browser_open_session("7630778503730253620", "task")
        original_read = service._read_open_session_store
        barrier = threading.Barrier(2)
        results: list[dict] = []

        def racing_read():
            data = original_read()
            try:
                barrier.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                pass
            return data

        def consume() -> None:
            results.append(service.get_browser_open_session(created["token"]))

        with patch("app.services.production_dashboard_service._read_open_session_store", side_effect=racing_read):
            threads = [threading.Thread(target=consume), threading.Thread(target=consume)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

    assert sum(1 for item in results if item.get("ok")) == 1
    assert sum(1 for item in results if not item.get("ok")) == 1


def test_browser_extension_token_can_post_consume_browser_open_session_once() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        session_accounts = Path(tmpdir) / "session-accounts.json"
        session_accounts.write_text(
            """
{
  "accounts": [
    {
      "userId": "7630778503730253620",
      "name": "用户07040602572",
      "cookie": "sessionid=test-cookie",
      "enabled": true
    }
  ]
}
""".strip(),
            encoding="utf-8",
        )
        app = _create_app(
            tmpdir,
            AIDP_PUBLIC_BASE_URL="https://platform.51gugu.uk",
            AIDP_ADMIN_API_TOKEN="admin-secret",
            AIDP_BROWSER_EXTENSION_API_TOKEN="browser-secret",
            AIDP_SESSION_ACCOUNTS_PATH=str(session_accounts),
            AIDP_PRODUCTION_STATE_PATH=str(Path(tmpdir) / "production-state.json"),
        )
        headers = {"X-AIDP-API-Token": "browser-secret"}
        admin_headers = {"X-AIDP-API-Token": "admin-secret"}
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/accounts/7630778503730253620/open-target/task",
                headers=admin_headers,
            )
            token = created.json()["open_url"].split("token=", 1)[1]
            first = client.post("/api/browser-open-session", json={"token": token}, headers=headers)
            replay = client.post("/api/browser-open-session", json={"token": token}, headers=headers)
            missing_auth = client.post("/api/browser-open-session", json={"token": token})

    assert created.status_code == 200, created.text
    assert first.status_code == 200, first.text
    assert first.json()["ok"] is True
    assert first.json()["cookie"] == "sessionid=test-cookie"
    assert replay.status_code == 200, replay.text
    assert replay.json()["ok"] is False
    assert missing_auth.status_code == 401, missing_auth.text


def _request(method: str, path: str, token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "scheme": "https",
            "server": ("platform.51gugu.uk", 443),
            "headers": [(b"x-aidp-api-token", token.encode("utf-8"))],
        }
    )


def _test_password_hash(password: str) -> str:
    salt = b"static-test-salt"
    iterations = 120_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    )


def _clear_login_failures() -> None:
    security_module = importlib.import_module("app.core.security")
    with security_module._LOGIN_FAILURES_LOCK:
        security_module._LOGIN_FAILURES.clear()
