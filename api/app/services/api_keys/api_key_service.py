# api/app/services/api_keys/api_key_service.py

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...config.settings import settings
from ...utils.logging_config import get_logger
from ...utils.supabase_utils import get_supabase_client
from ..base_service import BaseService, ServiceOperationError, ServiceValidationError

logger = get_logger(__name__)


class ApiKeyService(BaseService):
    """Service for creating, listing, revoking, and validating API keys."""

    def _get_secret(self) -> str:
        secret = settings.API_KEY_SECRET or settings.SECRET_KEY
        if not secret:
            raise ServiceOperationError("API key secret is not configured")
        if settings.is_production and not settings.API_KEY_SECRET:
            logger.warning("API_KEY_SECRET is not set - falling back to SECRET_KEY")
        return secret

    def _hash_key(self, raw_key: str) -> str:
        secret = self._get_secret()
        return hmac.new(
            secret.encode("utf-8"), raw_key.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _hash_value(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        secret = self._get_secret()
        return hmac.new(
            secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _build_key(self) -> Dict[str, str]:
        token = secrets.token_urlsafe(48)
        env_prefix = "live" if settings.is_production else "test"
        raw_key = f"mofisc_{env_prefix}_{token}"
        prefix = f"{env_prefix}_{secrets.token_hex(4)}"
        return {"raw_key": raw_key, "key_prefix": prefix}

    @staticmethod
    def _parse_optional_datetime(value: Optional[Any]) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None
        return None

    async def create_api_key(
        self,
        *,
        user_id: str,
        name: str,
        scopes: Optional[List[str]] = None,
        expires_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        if not user_id:
            raise ServiceValidationError("User ID is required")
        if not name or not name.strip():
            raise ServiceValidationError("API key name is required")

        key_payload = self._build_key()
        raw_key = key_payload["raw_key"]
        key_prefix = key_payload["key_prefix"]
        key_hash = self._hash_key(raw_key)
        scope_list = scopes or ["*"]
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        expires_at_value = expires_at or (
            datetime.now(timezone.utc) + timedelta(days=365 * 5)
        )

        supabase = get_supabase_client(use_service_role=True)

        response = (
            supabase.table("api_keys")
            .insert(
                {
                    "user_id": user_id,
                    "name": name.strip(),
                    "key_prefix": key_prefix,
                    "key_hash": key_hash,
                    "scopes": scope_list,
                    "expires_at": expires_at_value.isoformat(),
                }
            )
            .execute()
        )

        if not response.data:
            raise ServiceOperationError("Failed to create API key")

        record = response.data[0]
        return {
            "id": record.get("id"),
            "name": record.get("name"),
            "key_prefix": record.get("key_prefix"),
            "scopes": record.get("scopes", []),
            "created_at": record.get("created_at"),
            "expires_at": record.get("expires_at"),
            "api_key": raw_key,
        }

    async def list_api_keys(self, *, user_id: str) -> List[Dict[str, Any]]:
        if not user_id:
            raise ServiceValidationError("User ID is required")

        supabase = get_supabase_client(use_service_role=True)
        response = (
            supabase.table("api_keys")
            .select(
                "id, name, key_prefix, scopes, is_active, created_at, last_used_at, expires_at, revoked_at"
            )
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )

        return response.data or []

    async def revoke_api_key(self, *, user_id: str, api_key_id: str) -> None:
        if not user_id or not api_key_id:
            raise ServiceValidationError("User ID and API key ID are required")

        supabase = get_supabase_client(use_service_role=True)
        response = (
            supabase.table("api_keys")
            .update(
                {
                    "is_active": False,
                    "revoked_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", api_key_id)
            .eq("user_id", user_id)
            .execute()
        )

        if response.data is None:
            raise ServiceOperationError("Failed to revoke API key")

    async def validate_api_key(self, raw_key: str) -> Optional[Dict[str, Any]]:
        if not raw_key or len(raw_key) < 20:
            return None

        key_hash = self._hash_key(raw_key)
        supabase = get_supabase_client(use_service_role=True)

        response = (
            supabase.table("api_keys")
            .select("id, user_id, is_active, scopes, expires_at")
            .eq("key_hash", key_hash)
            .limit(1)
            .execute()
        )

        if not response.data:
            return None

        record = response.data[0]
        if not record.get("is_active"):
            return None

        expires_at = self._parse_optional_datetime(record.get("expires_at"))
        if expires_at and expires_at <= datetime.now(timezone.utc):
            return None

        supabase.table("api_keys").update(
            {"last_used_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", record["id"]).execute()

        return record

    def log_usage(
        self,
        *,
        api_key_id: str,
        user_id: str,
        request_path: str,
        request_method: str,
        endpoint_group: Optional[str],
        status_code: int,
        response_time_ms: int,
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> None:
        if not api_key_id or not user_id:
            return

        supabase = get_supabase_client(use_service_role=True)
        supabase.table("api_key_usage").insert(
            {
                "api_key_id": api_key_id,
                "user_id": user_id,
                "request_path": request_path,
                "request_method": request_method,
                "endpoint_group": endpoint_group,
                "status_code": status_code,
                "response_time_ms": response_time_ms,
                "success": status_code < 400,
                "ip_hash": self._hash_value(ip_address),
                "user_agent_hash": self._hash_value(user_agent),
            }
        ).execute()

    async def get_usage_summary(self, *, user_id: str, days: int = 7) -> Dict[str, Any]:
        if not user_id:
            raise ServiceValidationError("User ID is required")

        days = max(1, min(days, 30))
        since = datetime.now(timezone.utc) - timedelta(days=days)

        supabase = get_supabase_client(use_service_role=True)
        response = (
            supabase.table("api_key_usage")
            .select("created_at, endpoint_group, status_code")
            .eq("user_id", user_id)
            .gte("created_at", since.isoformat())
            .execute()
        )

        records = response.data or []

        total_requests = len(records)
        error_requests = sum(
            1 for record in records if (record.get("status_code") or 0) >= 400
        )
        error_rate = (
            round((error_requests / total_requests) * 100, 2) if total_requests else 0.0
        )

        daily_counts: Dict[str, int] = {}
        endpoint_counts: Dict[str, int] = {}

        for record in records:
            created_at = record.get("created_at")
            if created_at:
                date_key = created_at[:10]
                daily_counts[date_key] = daily_counts.get(date_key, 0) + 1

            endpoint = record.get("endpoint_group") or "other"
            endpoint_counts[endpoint] = endpoint_counts.get(endpoint, 0) + 1

        daily_series = [
            {"date": date, "count": daily_counts[date]}
            for date in sorted(daily_counts.keys())
        ]

        top_endpoints = sorted(
            [
                {"endpoint": key, "count": value}
                for key, value in endpoint_counts.items()
            ],
            key=lambda item: item["count"],
            reverse=True,
        )[:5]

        return {
            "total_requests": total_requests,
            "error_rate": error_rate,
            "daily": daily_series,
            "top_endpoints": top_endpoints,
        }
