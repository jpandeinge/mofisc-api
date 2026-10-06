# api/app/sentry.py

import logging
import re
from typing import Any, Dict

import sentry_sdk
from sentry_sdk import set_tag, set_user, start_transaction
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration

from .config.settings import settings

SENSITIVE_FIELDS = {
    "password",
    "secret",
    "authorization",
    "api_key",
    "access_token",
    "refresh_token",
    "credit_card",
    "account_number",
    "transaction_id",
    "user_id",
    "statement_id",
    "supabase_key",
    "supabase_url",
    "database_url",
    "jwt_token",
    "session_token",
    "balance",
    "transaction_amount",
    "email",
    "phone",
    "address",
    "full_name",
    "dob",
    "social_security",
    "bank_account",
    "raw_user_meta_data",
    "raw_app_meta_data",
}

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "x-api-key",
    "x-forwarded-for",
    "x-client-info",
    "apikey",
    "x-session-id",
    "x-supabase-auth",
    "authentication",
    "proxy-authorization",
    "set-cookie",
    "x-real-ip",
}


def sanitize_string(s: str) -> str:
    """Enhanced sanitization for strings"""
    # Enhanced regex patterns
    patterns = [
        (r"[\w\.-]+@[\w\.-]+", "[EMAIL]"),  # Email
        (
            r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
            "[CARD_NUMBER]",
        ),  # Card numbers
        (r"\b\d{10,}\b", "[NUMERIC_ID]"),  # Long numbers (potential IDs)
        (r"(?i)bearer\s+[a-zA-Z0-9\-._~+/]+=*", "[BEARER_TOKEN]"),  # Bearer tokens
        (
            r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*",
            "[JWT]",
        ),  # JWT tokens
        (r"\b\d+\.\d{2}\b", "[AMOUNT]"),  # Financial amounts
    ]

    result = s
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result)
    return result


def sanitize_dict(data: Dict[str, Any], parent_key: str = "") -> Dict[str, Any]:
    """Enhanced dictionary sanitization"""
    cleaned = {}
    for key, value in data.items():
        full_key = f"{parent_key}.{key}" if parent_key else key

        # Check if key contains sensitive information
        if any(sensitive in full_key.lower() for sensitive in SENSITIVE_FIELDS):
            cleaned[key] = "[REDACTED]"
        elif isinstance(value, dict):
            cleaned[key] = sanitize_dict(value, full_key)
        elif isinstance(value, list):
            cleaned[key] = [
                (
                    sanitize_dict(item, full_key)
                    if isinstance(item, dict)
                    else sanitize_string(str(item)) if isinstance(item, str) else item
                )
                for item in value
            ]
        elif isinstance(value, str):
            cleaned[key] = sanitize_string(value)
        else:
            cleaned[key] = value
    return cleaned


def before_send(event, hint):
    """Enhanced event sanitization"""
    try:
        # Don't process transactions
        if event.get("type") == "transaction":
            return event

        # Sanitize request data
        if "request" in event:
            request = event["request"]

            # Headers sanitization
            if "headers" in request:
                event["request"]["headers"] = {
                    k: "[REDACTED]" if k.lower() in SENSITIVE_HEADERS else v
                    for k, v in request["headers"].items()
                }

            # Data sanitization
            if "data" in request:
                event["request"]["data"] = sanitize_dict(request["data"])

            # Query string sanitization
            if "query_string" in request:
                event["request"]["query_string"] = sanitize_string(
                    str(request["query_string"])
                )

        # Sanitize user data
        if "user" in event:
            safe_user_fields = ["id", "ip_address"]
            event["user"] = {
                k: "[REDACTED]" if k not in safe_user_fields else v
                for k, v in event["user"].items()
            }

        # Sanitize exception data
        if "exception" in event:
            for exception in event["exception"].get("values", []):
                if "stacktrace" in exception:
                    for frame in exception["stacktrace"].get("frames", []):
                        if "vars" in frame:
                            frame["vars"] = sanitize_dict(frame["vars"])

        return event
    except Exception:
        return None


def before_breadcrumb(breadcrumb, hint):
    """Enhanced breadcrumb sanitization"""
    try:
        # Remove sensitive breadcrumbs completely
        if breadcrumb.get("category") in ["db", "auth"]:
            return None

        if breadcrumb.get("category") == "http":
            # Remove data for HTTP breadcrumbs
            if "data" in breadcrumb:
                del breadcrumb["data"]

            # Sanitize URL
            if "url" in breadcrumb:
                breadcrumb["url"] = sanitize_string(breadcrumb["url"])

        return breadcrumb
    except Exception:
        return None


def init_sentry():
    """Initialize Sentry with all integrations and security measures"""
    if not settings.SENTRY_DSN:
        return

    # Keep routine/recovered events as breadcrumbs; unexpected errors remain
    # Sentry issues. INFO events for every database request swamp real failures.
    logging_integration = LoggingIntegration(
        level=logging.DEBUG,  # Capture all log levels
        event_level=logging.ERROR,
    )

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT or settings.ENVIRONMENT,
        release=getattr(settings, "RELEASE", settings.APP_VERSION),
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        profiles_sample_rate=settings.SENTRY_PROFILES_SAMPLE_RATE,
        integrations=[
            FastApiIntegration(
                transaction_style="endpoint",
                middleware_spans=True,
            ),
            StarletteIntegration(
                middleware_spans=True,
            ),
            SqlalchemyIntegration(),
            logging_integration,
        ],
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
        send_default_pii=False,
        attach_stacktrace=True,
        max_breadcrumbs=50,
        debug=settings.DEBUG,
    )


class SentryTransaction:
    """Context manager for Sentry transactions"""

    def __init__(self, name: str, op: str = "default"):
        self.name = name
        self.op = op
        self.transaction = None

    def __enter__(self):
        self.transaction = start_transaction(name=self.name, op=self.op)
        return self.transaction

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.transaction:
            self.transaction.finish()


def set_sentry_context(key: str, value: Dict[str, Any]):
    """Safely set Sentry context with sanitized data"""
    sentry_sdk.set_context(key, sanitize_dict(value))


def set_sentry_user(user_data: Any):
    """Enhanced user context sanitization"""
    sanitized_user = {"id": "[USER_ID]", "ip_address": "[IP_ADDRESS]"}
    email = (
        user_data.get("email")
        if isinstance(user_data, dict)
        else getattr(user_data, "email", None)
    )
    if isinstance(email, str) and "@" in email:
        sanitized_user["email_domain"] = email.rsplit("@", 1)[-1]
    set_user(sanitized_user)


def set_sentry_tag(key: str, value: str):
    """Safely set Sentry tag"""
    set_tag(key, sanitize_string(str(value)))
