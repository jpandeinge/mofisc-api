# api/app/utils/api_scopes.py

import re
from typing import Iterable, Optional, Tuple

READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# Ordered by specificity (longest prefix first)
RESOURCE_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("/api/v1/public/platform-stats", "metrics"),
    ("/api/v1/metrics", "metrics"),
    ("/api/v1/upload-statement", "transactions"),
    ("/api/v1/transactions", "transactions"),
    ("/api/v1/goals", "goals"),
    ("/api/v1/budgets", "budgets"),
    ("/api/v1/subscriptions", "subscriptions"),
    ("/api/v1/achievements", "achievements"),
    ("/api/v1/user-achievement-metrics", "achievements"),
    ("/api/v1/calculators", "calculators"),
    ("/api/v1/notifications", "notifications"),
    ("/api/v1/security", "security"),
    ("/api/v1/users", "users"),
    ("/api/v1/api-keys", "api_keys"),
)

UUID_PATTERN = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-" r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def sanitize_path(path: str) -> str:
    return UUID_PATTERN.sub("/{id}", path)


def resolve_required_scope(path: str, method: str) -> Optional[str]:
    if not path.startswith("/api/v1"):
        return None

    for prefix, resource in RESOURCE_PREFIXES:
        if path.startswith(prefix):
            action = "read" if method.upper() in READ_METHODS else "write"
            return f"{resource}:{action}"

    return None


def is_scope_allowed(scopes: Iterable[str], required_scope: Optional[str]) -> bool:
    if not required_scope:
        return True

    scope_set = {scope.strip() for scope in scopes if scope}
    if not scope_set:
        return False

    if "*" in scope_set:
        return True

    if required_scope in scope_set:
        return True

    resource, action = required_scope.split(":", 1)
    if f"{resource}:*" in scope_set:
        return True
    if f"*:{action}" in scope_set:
        return True

    return False
