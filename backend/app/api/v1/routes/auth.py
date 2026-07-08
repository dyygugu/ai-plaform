from fastapi import APIRouter, HTTPException, Request

from app.core.security import check_web_login_rate_limit, client_source_from_request, create_web_session_token, record_web_login_failure, record_web_login_success, verify_password
from app.core.settings import get_settings
from app.schemas.auth import PlatformLoginRequest, PlatformLoginResponse
from app.services.notification_service import send_error_notification

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=PlatformLoginResponse)
def login_to_platform(payload: PlatformLoginRequest, request: Request) -> PlatformLoginResponse:
    settings = get_settings()
    configured_phone = str(settings.web_login_phone or "").strip()
    configured_hash = str(settings.web_login_password_hash or "").strip()
    configured_secret = str(settings.web_session_secret or "").strip()
    if not configured_phone or not configured_hash or not configured_secret:
        raise HTTPException(status_code=503, detail="平台账号密码登录未配置，请先配置网页登录环境变量。")

    supplied_phone = str(payload.phone or "").strip()
    source = _client_source(request)
    rate_limit = check_web_login_rate_limit(source, supplied_phone)
    if rate_limit.blocked:
        raise HTTPException(status_code=429, detail=f"登录失败次数过多，请 {rate_limit.retry_after_seconds} 秒后再试。")

    password_ok = verify_password(payload.password, configured_hash)
    if supplied_phone != configured_phone or not password_ok:
        if record_web_login_failure(source, supplied_phone):
            send_error_notification(
                event="backend.error",
                level="warn",
                message="WEB_LOGIN_RATE_LIMIT",
                data={
                    "error_code": "WEB_LOGIN_RATE_LIMIT",
                    "path": str(request.url.path),
                    "client_source": source,
                    "phone_masked": _mask_phone(supplied_phone),
                },
            )
        raise HTTPException(status_code=401, detail="手机号或密码错误。")

    record_web_login_success(source, configured_phone)
    access_token = create_web_session_token(settings, configured_phone)
    return PlatformLoginResponse(
        access_token=access_token,
        expires_in=max(60, int(settings.web_session_ttl_seconds or 0)),
        phone_masked=_mask_phone(configured_phone),
        message="登录成功。",
    )


def _mask_phone(phone: str) -> str:
    value = str(phone or "").strip()
    if len(value) < 7:
        return "***"
    return f"{value[:3]}****{value[-4:]}"


def _client_source(request: Request) -> str:
    return client_source_from_request(request, get_settings())
