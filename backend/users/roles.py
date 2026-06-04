ROLE_PERMISSIONS: dict[str, dict[str, bool]] = {
    "admin": {
        "upload_dataset": True,
        "create_session": True,
        "configure_session": True,
        "run_training": True,
        "view_forecasts": True,
        "download_reports": True,
        "manage_users": True,
        "manage_tenant": True,
    },
    "analyst": {
        "upload_dataset": True,
        "create_session": True,
        "configure_session": True,
        "run_training": True,
        "view_forecasts": True,
        "download_reports": True,
        "manage_users": False,
        "manage_tenant": False,
    },
    "viewer": {
        "upload_dataset": False,
        "create_session": False,
        "configure_session": False,
        "run_training": False,
        "view_forecasts": True,
        "download_reports": True,
        "manage_users": False,
        "manage_tenant": False,
    },
}

VALID_ROLES = set(ROLE_PERMISSIONS.keys())


def can(role: str, permission: str) -> bool:
    return ROLE_PERMISSIONS.get(role, {}).get(permission, False)
