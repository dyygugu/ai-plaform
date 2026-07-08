from typing import Optional

from app.core.settings import Settings, get_settings


def api_path(suffix: str, settings: Optional[Settings] = None) -> str:
    current = settings or get_settings()
    prefix = str(current.api_prefix or "").rstrip("/") or str(Settings.model_fields["api_prefix"].default).rstrip("/")
    return f"{prefix}/{str(suffix or '').lstrip('/')}"


def public_api_url(suffix: str, settings: Optional[Settings] = None) -> str:
    current = settings or get_settings()
    return f"{current.public_base_url.rstrip('/')}{api_path(suffix, current)}"


def api_paths(*suffixes: str, settings: Optional[Settings] = None) -> str:
    current = settings or get_settings()
    return ",".join(api_path(suffix, current) for suffix in suffixes)
