from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    READONLY = "readonly"


PERMISSION_MATRIX: dict[Role, dict[str, bool]] = {
    Role.ADMIN: {
        "view_dashboard": True,
        "view_sensitive_config": True,
        "refresh_tasks": True,
        "manage_accounts": True,
        "manage_rules": True,
        "manage_secrets": True,
        "run_backup": True,
        "restore_backup": True,
        "delete_records": True,
        "export_reports": True,
    },
    Role.OPERATOR: {
        "view_dashboard": True,
        "view_sensitive_config": False,
        "refresh_tasks": True,
        "manage_accounts": True,
        "manage_rules": True,
        "manage_secrets": False,
        "run_backup": True,
        "restore_backup": False,
        "delete_records": False,
        "export_reports": True,
    },
    Role.READONLY: {
        "view_dashboard": True,
        "view_sensitive_config": False,
        "refresh_tasks": False,
        "manage_accounts": False,
        "manage_rules": False,
        "manage_secrets": False,
        "run_backup": False,
        "restore_backup": False,
        "delete_records": False,
        "export_reports": False,
    },
}


def get_permission_matrix() -> dict[str, dict[str, bool]]:
    return {role.value: permissions for role, permissions in PERMISSION_MATRIX.items()}
