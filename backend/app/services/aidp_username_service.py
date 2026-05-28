import json
import time
from typing import Any, Optional
from urllib.parse import quote, urlparse

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session
from websockets.sync.client import connect

from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.schemas.account import AccountUsernameRefreshItem, AccountUsernameRefreshResponse
from app.services.audit_service import write_audit
from app.services.production_dashboard_service import build_production_dashboard, create_browser_open_session

PERSONAL_CENTER_URL = "https://aidp.juejin.cn/operation/lite/setting/account/personal-center?org=AIDP%20Coding&tab=2"


def refresh_account_usernames(db: Session, only_missing: bool = True) -> AccountUsernameRefreshResponse:
    dashboard = build_production_dashboard(db)
    items: list[AccountUsernameRefreshItem] = []
    for account in dashboard.accounts:
        if only_missing and account.real_name_ok:
            continue
        item = _refresh_one_username(db, account.user_id)
        items.append(item)
    db.commit()
    updated_count = sum(1 for item in items if item.updated)
    return AccountUsernameRefreshResponse(
        ok=all(item.error is None for item in items),
        updated_count=updated_count,
        items=items,
        message=f"已通过个人中心 GetUserInfo 刷新真实用户名：更新 {updated_count} 个账号。",
    )


def _refresh_one_username(db: Session, user_id: str) -> AccountUsernameRefreshItem:
    try:
        session = create_browser_open_session(user_id, "personal")
        cdp_port = _open_cookie_browser(session["token"])
        user = _capture_get_user_info(cdp_port, user_id)
        username = str(user.get("Username") or user.get("UserName") or user.get("username") or user.get("name") or "").strip()
        resolved_user_id = str(user.get("UserID") or user.get("UserId") or user.get("userId") or user_id).strip()
        if resolved_user_id != user_id:
            return AccountUsernameRefreshItem(user_id=user_id, error=f"GetUserInfo 返回 userId 不匹配：{resolved_user_id}")
        if not _looks_like_real_username(username):
            return AccountUsernameRefreshItem(user_id=user_id, error="GetUserInfo 未返回真实“用户+数字”用户名")
        _save_username(db, user_id, username)
        write_audit(
            db,
            event_type="account_username_refresh",
            message=f"Refreshed real username for {user_id} from GetUserInfo",
            target_type="account",
            target_id=user_id,
        )
        return AccountUsernameRefreshItem(user_id=user_id, display_name=username, source="GetUserInfoNetwork", updated=True)
    except Exception as exc:  # noqa: BLE001 - endpoint must continue other accounts and report exact failures.
        return AccountUsernameRefreshItem(user_id=user_id, error=str(exc))


def _open_cookie_browser(token: str) -> int:
    settings = get_settings()
    launcher = _internal_launcher_url()
    monitor_url = settings.public_base_url.rstrip("/")
    url = f"{launcher}/api/open-with-cookie?monitorUrl={quote(monitor_url, safe='')}&token={quote(token)}"
    response = requests.get(url, timeout=80)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        payload = next((item for item in reversed(payload) if isinstance(item, dict) and item.get("ok")), payload[-1] if payload else {})
    cdp_port = int(payload.get("cdpPort") or 0)
    if cdp_port <= 0:
        raise RuntimeError("本机助手未返回可用 CDP 端口")
    return cdp_port


def _capture_get_user_info(cdp_port: int, user_id: str) -> dict[str, Any]:
    page = _find_aidp_page(cdp_port)
    websocket_url = _container_websocket_url(str(page["webSocketDebuggerUrl"]))
    with connect(websocket_url, origin=None, open_timeout=20) as websocket:
        message_id = 1
        pending: dict[int, str] = {}
        for method in ("Network.enable", "Page.enable"):
            websocket.send(json.dumps({"id": message_id, "method": method, "params": {}}))
            pending[message_id] = method
            message_id += 1
        websocket.send(json.dumps({"id": message_id, "method": "Page.navigate", "params": {"url": PERSONAL_CENTER_URL}}))
        pending[message_id] = "Page.navigate"
        message_id += 1
        deadline = time.time() + 65
        body_requests: dict[int, tuple[int, str]] = {}
        while time.time() < deadline:
            raw_message = websocket.recv(timeout=70)
            message = json.loads(raw_message)
            method = message.get("method")
            if method == "Network.responseReceived":
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                response = params.get("response") if isinstance(params.get("response"), dict) else {}
                response_url = str(response.get("url") or "")
                if "/api/crowdsourcing/GetUserInfo" not in response_url:
                    continue
                request_id = str(params.get("requestId") or "")
                websocket.send(json.dumps({"id": message_id, "method": "Network.getResponseBody", "params": {"requestId": request_id}}))
                body_requests[message_id] = (int(response.get("status") or 0), response_url)
                message_id += 1
            elif isinstance(message.get("id"), int) and int(message["id"]) in body_requests:
                status, _response_url = body_requests.pop(int(message["id"]))
                if status != 200:
                    continue
                result = message.get("result") if isinstance(message.get("result"), dict) else {}
                body = str(result.get("body") or "")
                data = json.loads(body)
                user = data.get("User") if isinstance(data, dict) else None
                if isinstance(user, dict) and str(user.get("UserID") or user.get("UserId") or "") == user_id:
                    return user
        raise RuntimeError("未捕获到个人中心 GetUserInfo 用户响应")


def _find_aidp_page(cdp_port: int) -> dict[str, Any]:
    response = requests.get(
        f"http://{_cdp_host()}:{cdp_port}/json",
        headers={"Host": f"127.0.0.1:{cdp_port}"},
        timeout=10,
    )
    response.raise_for_status()
    pages = response.json()
    page = next((item for item in pages if item.get("type") == "page" and "aidp.juejin.cn" in str(item.get("url") or "")), None)
    if page is None:
        page = next((item for item in pages if item.get("type") == "page"), None)
    if not isinstance(page, dict) or not page.get("webSocketDebuggerUrl"):
        raise RuntimeError("未找到可调试的 AIDP 页面")
    return page


def _container_websocket_url(url: str) -> str:
    parsed = urlparse(url)
    host = _cdp_host()
    return url.replace(f"{parsed.hostname}:{parsed.port}", f"{host}:{parsed.port}")


def _internal_launcher_url() -> str:
    settings = get_settings()
    return (settings.host_launcher_internal_url or settings.host_launcher_url).rstrip("/")


def _cdp_host() -> str:
    launcher = _internal_launcher_url()
    parsed = urlparse(launcher)
    return parsed.hostname or "127.0.0.1"


def _looks_like_real_username(value: str) -> bool:
    return value.startswith("用户") and any(char.isdigit() for char in value)


def _save_username(db: Session, user_id: str, username: str) -> None:
    account = db.scalar(select(AidpAccount).where(AidpAccount.user_id == user_id))
    if account is None:
        account = AidpAccount(user_id=user_id, display_name=username, status=AccountStatus.ACTIVE, is_task_source=False, auth_mode="client-cookie")
        db.add(account)
    account.display_name = username
    if account.status == AccountStatus.DISABLED:
        account.status = AccountStatus.ACTIVE
    account.last_error = None
    db.flush()
