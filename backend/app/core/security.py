import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from dataclasses import dataclass
from threading import Lock
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from app.core.settings import Settings, get_settings


READ_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_API_SUFFIXES = {
    "/health",
    "/auth/login",
}
WEB_LOGIN_FAILURE_LIMIT = 5
WEB_LOGIN_WINDOW_SECONDS = 15 * 60
WEB_LOGIN_COOLDOWN_SECONDS = 15 * 60
ROLE_WRITE_PATH_PREFIXES = {
    "browser-extension": ("/api/client-session", "/api/browser-open-session"),
}
ROLE_READ_PATH_PREFIXES = {
    "browser-extension": ("/api/browser-open-session",),
}


@dataclass(frozen=True)
class Principal:
    role: str
    source: str = "api-token"


@dataclass
class _LoginFailureState:
    failures: int = 0
    first_failed_at: float = 0.0
    blocked_until: float = 0.0
    alert_sent: bool = False


@dataclass(frozen=True)
class LoginRateLimitResult:
    blocked: bool
    retry_after_seconds: int = 0


_LOGIN_FAILURES: dict[str, _LoginFailureState] = {}
_LOGIN_FAILURES_LOCK = Lock()


def auth_required(settings: Optional[Settings] = None) -> bool:
    current = settings or get_settings()
    if current.auth_enabled:
        return True
    if current.monitor_env.strip().lower() in {"prod", "production"}:
        return True
    return _is_public_url(current.public_base_url)


def legacy_production_routes_blocked(settings: Optional[Settings] = None) -> bool:
    current = settings or get_settings()
    return auth_required(current) and not current.legacy_production_routes_enabled


def client_source_from_request(request: Request, settings: Optional[Settings] = None) -> str:
    current = settings or get_settings()
    peer = request.client.host if request.client else "unknown"
    if _is_trusted_proxy(peer, current):
        proxy_client = _valid_client_ip(request.headers.get("x-aidp-client-ip", ""))
        if proxy_client:
            return proxy_client
    return peer


def require_api_auth(request: Request) -> Principal:
    settings = get_settings()
    if _is_public_api_path(request.url.path, settings):
        principal = Principal(role="anonymous", source="public")
        request.state.aidp_principal = principal
        return principal
    if not auth_required(settings):
        principal = Principal(role="admin", source="local-dev")
        request.state.aidp_principal = principal
        return principal

    if not str(settings.admin_api_token or "").strip() and not _web_login_configured(settings):
        raise HTTPException(
            status_code=503,
            detail="公开或生产环境必须配置管理员 token 或网页登录账号；当前已 fail closed。",
        )
    tokens = _configured_tokens(settings)

    supplied = _extract_token(request)
    if not supplied:
        raise HTTPException(status_code=401, detail="请先登录平台。")
    role = _match_token(supplied, tokens)
    source = "api-token"
    if role is None:
        role = _match_web_session_token(supplied, settings)
        source = "web-session" if role else source
    if role is None:
        raise HTTPException(status_code=403, detail="登录已失效或访问凭证无效，请重新登录平台。")
    if request.method.upper() in READ_METHODS and role == "browser-extension" and not _role_can_read_path(role, request.url.path, settings):
        raise HTTPException(status_code=403, detail="本机助手 token 只能读取助手必需接口。")
    if request.method.upper() not in READ_METHODS and role != "admin" and not _role_can_write_path(role, request.url.path, settings):
        raise HTTPException(status_code=403, detail="写入、高危和生产操作必须使用 admin API token。")

    principal = Principal(role=role, source=source)
    request.state.aidp_principal = principal
    return principal


def _configured_tokens(settings: Settings) -> dict[str, str]:
    result: dict[str, str] = {}
    for role, token in {
        "admin": settings.admin_api_token,
        "operator": settings.operator_api_token,
        "readonly": settings.readonly_api_token,
        "browser-extension": settings.browser_extension_api_token,
    }.items():
        value = str(token or "").strip()
        if value:
            result[role] = value
    return result


def _web_login_configured(settings: Settings) -> bool:
    return all(
        str(value or "").strip()
        for value in (settings.web_login_phone, settings.web_login_password_hash, settings.web_session_secret)
    )


def _extract_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return (
        request.headers.get("x-aidp-api-token", "").strip()
        or request.headers.get("x-api-key", "").strip()
    )


def _match_token(supplied: str, tokens: dict[str, str]) -> Optional[str]:
    for role, token in tokens.items():
        if secrets.compare_digest(supplied, token):
            return role
    return None


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        encoded = str(encoded_hash or "").strip()
        separator = "$" if "$" in encoded else ":"
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split(separator, 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = _b64decode(salt_raw)
        expected = _b64decode(digest_raw)
    except (TypeError, ValueError, binascii.Error):
        return False
    try:
        actual = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, iterations)
    except (TypeError, ValueError, OverflowError):
        return False
    return secrets.compare_digest(actual, expected)


def check_web_login_rate_limit(source: str, phone: str, now: Optional[float] = None) -> LoginRateLimitResult:
    current = now if now is not None else time.time()
    with _LOGIN_FAILURES_LOCK:
        for key in _login_failure_keys(source, phone):
            state = _LOGIN_FAILURES.get(key)
            if not state:
                continue
            if state.blocked_until > current:
                return LoginRateLimitResult(blocked=True, retry_after_seconds=max(1, int(state.blocked_until - current)))
            if state.blocked_until and state.blocked_until <= current:
                _LOGIN_FAILURES.pop(key, None)
    return LoginRateLimitResult(blocked=False)


def record_web_login_failure(source: str, phone: str, now: Optional[float] = None) -> bool:
    current = now if now is not None else time.time()
    should_alert = False
    with _LOGIN_FAILURES_LOCK:
        for key in _login_failure_keys(source, phone):
            state = _LOGIN_FAILURES.get(key)
            if not state or current - state.first_failed_at > WEB_LOGIN_WINDOW_SECONDS:
                state = _LoginFailureState(failures=0, first_failed_at=current)
                _LOGIN_FAILURES[key] = state
            state.failures += 1
            if state.failures >= WEB_LOGIN_FAILURE_LIMIT:
                state.blocked_until = current + WEB_LOGIN_COOLDOWN_SECONDS
                if not state.alert_sent:
                    state.alert_sent = True
                    should_alert = True
    return should_alert


def record_web_login_success(source: str, phone: str) -> None:
    with _LOGIN_FAILURES_LOCK:
        for key in _login_success_clear_keys(source, phone):
            _LOGIN_FAILURES.pop(key, None)


def create_web_session_token(settings: Settings, phone: str) -> str:
    secret = str(settings.web_session_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="平台登录未配置会话密钥。")
    now = int(time.time())
    payload = {
        "sub": str(phone or "").strip(),
        "role": "admin",
        "iat": now,
        "exp": now + max(60, int(settings.web_session_ttl_seconds or 0)),
        "nonce": secrets.token_urlsafe(16),
    }
    body = _b64encode(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _sign_web_session_body(secret, body)
    return f"web.{body}.{signature}"


def _match_web_session_token(supplied: str, settings: Settings) -> Optional[str]:
    secret = str(settings.web_session_secret or "").strip()
    configured_phone = str(settings.web_login_phone or "").strip()
    if not secret or not configured_phone or not str(supplied or "").startswith("web."):
        return None
    try:
        prefix, body, signature = supplied.split(".", 2)
        if prefix != "web":
            return None
        expected_signature = _sign_web_session_body(secret, body)
        if not secrets.compare_digest(signature, expected_signature):
            return None
        payload = json.loads(_b64decode(body).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if str(payload.get("sub") or "").strip() != configured_phone:
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    role = str(payload.get("role") or "").strip()
    return role if role in {"admin"} else None


def _is_public_api_path(path: str, settings: Settings) -> bool:
    prefix = str(settings.api_prefix or "").rstrip("/")
    return any(path == f"{prefix}{suffix}" for suffix in PUBLIC_API_SUFFIXES)


def _login_failure_key(source: str, phone: str) -> str:
    return f"{str(source or 'unknown').strip()}|{str(phone or '').strip()}"


def _login_failure_keys(source: str, phone: str) -> tuple[str, ...]:
    scoped_key = _login_failure_key(source, phone)
    phone_key = _login_failure_key("__phone__", phone)
    source_key = _login_failure_key(source, "__source__")
    keys = [scoped_key, phone_key, source_key]
    return tuple(dict.fromkeys(keys))


def _login_success_clear_keys(source: str, phone: str) -> tuple[str, ...]:
    phone_value = str(phone or "").strip()
    suffix = f"|{phone_value}"
    keys = [key for key in _LOGIN_FAILURES if key.endswith(suffix)]
    for key in (*_login_failure_keys(source, phone_value), _login_failure_key(source, "__source__")):
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def _sign_web_session_body(secret: str, body: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(digest)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(raw: str) -> bytes:
    padded = str(raw or "") + "=" * (-len(str(raw or "")) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _role_can_write_path(role: str, path: str, settings: Settings) -> bool:
    if role == "browser-extension" and _browser_extension_can_write_path(path, settings):
        return True
    prefixes = ROLE_WRITE_PATH_PREFIXES.get(role, ())
    if role == "browser-extension":
        prefixes = (
            *prefixes,
            _api_prefixed_path(settings, "/operation-recordings"),
            _api_prefixed_path(settings, "/accounts/client-session"),
            _api_prefixed_path(settings, "/task-auto-runs/preflight"),
        )
    return any(_path_matches_prefix(path, prefix) for prefix in prefixes)


def _role_can_read_path(role: str, path: str, settings: Settings) -> bool:
    prefixes = ROLE_READ_PATH_PREFIXES.get(role, ())
    if role == "browser-extension":
        prefixes = (*prefixes, _api_prefixed_path(settings, "/local-agent/releases/latest"))
    return any(_path_matches_prefix(path, prefix) for prefix in prefixes)


def _browser_extension_can_write_path(path: str, settings: Optional[Settings] = None) -> bool:
    current = settings or get_settings()
    workers_prefix = _api_prefixed_path(current, "/workers")
    if path in {
        f"{workers_prefix}/register",
        f"{workers_prefix}/heartbeat",
        f"{workers_prefix}/events",
    }:
        return True
    claim_pattern = re.compile(rf"^{re.escape(workers_prefix)}/[^/]+/commands/claim$")
    command_pattern = re.compile(rf"^{re.escape(workers_prefix)}/commands/[^/]+/(renew|result|execution-gate)$")
    return bool(claim_pattern.match(path) or command_pattern.match(path))


def _api_prefixed_path(settings: Settings, suffix: str) -> str:
    prefix = str(settings.api_prefix or "").rstrip("/") or "/api/v1"
    return f"{prefix}/{str(suffix or '').lstrip('/')}"


def _path_matches_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _is_public_url(raw_url: str) -> bool:
    parsed = urlparse(str(raw_url or ""))
    host = parsed.hostname
    if not host:
        return False
    lowered = host.lower()
    if lowered in {"localhost", "127.0.0.1", "::1"}:
        return False
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return True
    return not (address.is_loopback or address.is_private or address.is_link_local)


def _is_trusted_proxy(peer: str, settings: Settings) -> bool:
    try:
        address = ipaddress.ip_address(str(peer or "").strip())
    except ValueError:
        return False
    for raw_network in str(settings.trusted_proxy_cidrs or "").split(","):
        value = raw_network.strip()
        if not value:
            continue
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False


def _valid_client_ip(raw_value: str) -> str:
    candidate = str(raw_value or "").strip()
    if not candidate:
        return ""
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return ""
    return ""
