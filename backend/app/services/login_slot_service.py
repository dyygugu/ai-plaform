from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.account import AccountStatus, AidpAccount
from app.schemas.account import AccountClientSessionRequest, AccountClientSessionResponse, AccountLoginSlotCreateRequest, AccountLoginSlotRead
from app.services.audit_service import write_audit


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_data_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _launcher_command() -> str:
    settings = get_settings()
    return f'pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "{settings.host_launcher_script_path}" -Port 8790'


def _pick_cdp_port(seed: str, requested: Optional[int] = None) -> int:
    if requested and 9323 <= requested <= 9422:
        return requested
    number = sum(ord(char) for char in seed) % 100
    return 9323 + number


def _slot_urls(user_id: str, cdp_port: int, login_session_id: str) -> tuple[str, str, str]:
    settings = get_settings()
    base = settings.host_launcher_url.rstrip('/')
    monitor_url = settings.public_base_url.rstrip('/')
    open_url = f'{base}/api/open-profile?userId={quote(user_id)}&port={cdp_port}'
    sync_url = f'{base}/api/sync-aidp-session?port={cdp_port}&monitorUrl={quote(monitor_url, safe="")}&loginSessionId={quote(login_session_id)}'
    return monitor_url, open_url, sync_url


def _to_slot(account: AidpAccount, cdp_port: Optional[int] = None) -> AccountLoginSlotRead:
    port = _pick_cdp_port(account.user_id, cdp_port)
    login_session_id = account.user_id
    monitor_url, open_url, sync_url = _slot_urls(account.user_id, port, login_session_id)
    return AccountLoginSlotRead(
        login_session_id=login_session_id,
        user_id=account.user_id,
        display_name=account.display_name or account.user_id,
        status=account.status.value if hasattr(account.status, 'value') else str(account.status),
        auth_mode=account.auth_mode,
        pending_login=True,
        enabled=False,
        cdp_port=port,
        launcher_start_command=_launcher_command(),
        open_profile_url=open_url,
        sync_url=sync_url,
        monitor_url=monitor_url,
        instructions=[
            '先在本机启动登录助手命令。',
            '点击打开登录窗口，完成 AIDP 登录并进入 /operation/ 页面。',
            '确认页面显示真实账号后点击同步登录态；未识别真实 userId 时系统会拒绝保存。',
            'Cookie 只写入本地运行数据文件，不在前端、报告或迁移证据中展示。',
        ],
        created_at=account.created_at,
    )


def _pending_slot_to_read(slot: dict[str, Any]) -> AccountLoginSlotRead:
    login_session_id = str(slot.get('login_session_id') or '')
    cdp_port = _pick_cdp_port(login_session_id, _int_or_none(slot.get('cdp_port')))
    monitor_url, open_url, sync_url = _slot_urls(login_session_id, cdp_port, login_session_id)
    return AccountLoginSlotRead(
        login_session_id=login_session_id,
        user_id=login_session_id,
        display_name=str(slot.get('display_name') or '新账号待登录'),
        status='pending_login',
        auth_mode='local-profile-pending',
        pending_login=True,
        enabled=False,
        cdp_port=cdp_port,
        launcher_start_command=_launcher_command(),
        open_profile_url=open_url,
        sync_url=sync_url,
        monitor_url=monitor_url,
        instructions=[
            '这是临时登录会话，不是 AIDP 账号，不会进入账号列表或生产统计。',
            '先在本机启动登录助手命令。',
            '点击打开登录窗口，完成 AIDP 登录并进入 /operation/ 页面。',
            '同步成功且识别真实 userId/用户名后，系统才创建真实账号记录。',
        ],
        created_at=_parse_datetime(slot.get('created_at')),
    )


def list_login_slots(db: Session) -> list[AccountLoginSlotRead]:
    pending_slots = [_pending_slot_to_read(slot) for slot in _read_login_slots()]
    accounts = list(db.scalars(select(AidpAccount).where(AidpAccount.status == AccountStatus.NEEDS_LOGIN).order_by(AidpAccount.updated_at.desc(), AidpAccount.id.desc())))
    return pending_slots + [_to_slot(account) for account in accounts]


def create_new_login_slot(db: Session, payload: Optional[AccountLoginSlotCreateRequest] = None) -> AccountLoginSlotRead:
    timestamp = _now().strftime('%Y%m%d%H%M%S')
    login_session_id = f'pending-{timestamp}'
    slot_data = {
        'login_session_id': login_session_id,
        'display_name': payload.display_name if payload and payload.display_name else f'新账号待登录-{timestamp[-6:]}',
        'cdp_port': payload.cdp_port if payload else None,
        'created_at': _now().isoformat(),
    }
    _upsert_login_slot(slot_data)
    audit = write_audit(db, event_type='account_login_slot_create', message=f'Created pending login session {login_session_id}; not an account record', target_type='login_session', target_id=login_session_id)
    db.commit()
    slot = _pending_slot_to_read(slot_data)
    slot.instructions.append(f'审计 trace：{audit.trace_id}')
    return slot


def create_relogin_slot(db: Session, user_id: str, payload: Optional[AccountLoginSlotCreateRequest] = None) -> AccountLoginSlotRead:
    account = db.scalar(select(AidpAccount).where(AidpAccount.user_id == user_id))
    if account is None:
        account = AidpAccount(user_id=user_id, display_name=user_id, status=AccountStatus.NEEDS_LOGIN, is_task_source=False, auth_mode='local-profile-pending')
        db.add(account)
    account.status = AccountStatus.NEEDS_LOGIN
    account.auth_mode = 'local-profile-pending'
    account.last_error = '已进入重新登录流程：同步真实 /operation/ 登录态前不参与刷新。'
    audit = write_audit(db, event_type='account_relogin_slot_create', message=f'Created relogin slot {user_id}', target_type='account', target_id=user_id)
    db.commit()
    db.refresh(account)
    slot = _to_slot(account, payload.cdp_port if payload else None)
    slot.instructions.append(f'审计 trace：{audit.trace_id}')
    return slot


def _read_session_store() -> dict[str, Any]:
    path = _resolve_data_path(get_settings().session_accounts_path)
    if not path.exists():
        return {'accounts': [], 'login_slots': []}
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if isinstance(data, list):
        return {'accounts': data, 'login_slots': []}
    if not isinstance(data, dict):
        return {'accounts': [], 'login_slots': []}
    accounts = data.get('accounts')
    if not isinstance(accounts, list):
        data['accounts'] = []
    login_slots = data.get('login_slots')
    if not isinstance(login_slots, list):
        data['login_slots'] = []
    return data


def _write_session_store(data: dict[str, Any]) -> Path:
    path = _resolve_data_path(get_settings().session_accounts_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def _read_login_slots() -> list[dict[str, Any]]:
    data = _read_session_store()
    slots = data.get('login_slots')
    return [slot for slot in slots if isinstance(slot, dict)] if isinstance(slots, list) else []


def _upsert_login_slot(slot: dict[str, Any]) -> None:
    data = _read_session_store()
    login_session_id = str(slot.get('login_session_id') or '')
    slots = [item for item in _read_login_slots() if str(item.get('login_session_id') or '') != login_session_id]
    slots.append(slot)
    data['login_slots'] = slots
    _write_session_store(data)


def _remove_login_slot(login_session_id: str) -> None:
    if not login_session_id:
        return
    data = _read_session_store()
    slots = [item for item in _read_login_slots() if str(item.get('login_session_id') or '') != login_session_id]
    data['login_slots'] = slots
    _write_session_store(data)


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None


def load_session_account(source_account_user_id: str) -> Optional[dict[str, Any]]:
    data = _read_session_store()
    for account in data.get('accounts', []):
        if str(account.get('userId') or account.get('user_id') or '') == source_account_user_id:
            return account
    return None


def register_client_session(db: Session, payload: AccountClientSessionRequest) -> AccountClientSessionResponse:
    user_id = (payload.authoritative_user_id or payload.user_id or '').strip()
    display_name = (payload.authoritative_name or payload.display_name or payload.name or user_id).strip()
    operation_url = (payload.referer or payload.href or '').strip()
    cookie = payload.cookie.strip()
    if not re.fullmatch(r'\d{12,24}', user_id):
        raise ValueError('未识别真实 AIDP userId，拒绝保存登录态。')
    if not cookie:
        raise ValueError('未读取到 Cookie，拒绝保存登录态。')
    if 'aidp.juejin.cn' not in operation_url or '/operation' not in operation_url:
        raise ValueError('当前页面不是 AIDP /operation/，拒绝保存登录态。')

    data = _read_session_store()
    accounts = [item for item in data.get('accounts', []) if str(item.get('userId') or item.get('user_id') or '') != user_id]
    accounts.append({
        'userId': user_id,
        'name': display_name,
        'enabled': True,
        'authMode': 'client-cookie',
        'cookie': cookie,
        'referer': operation_url,
        'operationUrl': operation_url,
        'cdpPort': payload.cdp_port,
        'userInfoSource': payload.user_info_source,
        'syncedFrom': payload.synced_from or 'aidp-local-helper-cdp',
        'savedAt': _now().isoformat(),
    })
    data['accounts'] = accounts
    _write_session_store(data)

    account = db.scalar(select(AidpAccount).where(AidpAccount.user_id == user_id))
    if account is None:
        account = AidpAccount(user_id=user_id, display_name=display_name, status=AccountStatus.ACTIVE, is_task_source=False, auth_mode='client-cookie')
        db.add(account)
    account.display_name = display_name
    account.status = AccountStatus.ACTIVE
    account.auth_mode = 'client-cookie'
    account.last_error = None

    if payload.login_session_id and payload.login_session_id.startswith('pending-'):
        _remove_login_slot(payload.login_session_id)
        pending = db.scalar(select(AidpAccount).where(AidpAccount.user_id == payload.login_session_id))
        if pending is not None:
            pending.status = AccountStatus.DISABLED
            pending.auth_mode = 'local-profile-bound'
            pending.last_error = f'已绑定真实账号 {user_id}，占位不参与刷新。'

    audit = write_audit(db, event_type='account_client_session_register', message=f'Registered local client session for {user_id}; cookie redacted', target_type='account', target_id=user_id)
    db.commit()
    return AccountClientSessionResponse(
        ok=True,
        user_id=user_id,
        display_name=display_name,
        account_status=AccountStatus.ACTIVE.value,
        session_saved=True,
        cookie_saved=True,
        audit_trace_id=audit.trace_id,
        message='登录态已通过 /operation/ 与真实 userId 校验，Cookie 已保存到本地运行数据文件且不会在前端展示。',
    )
