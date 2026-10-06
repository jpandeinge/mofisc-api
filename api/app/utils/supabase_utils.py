# api/app/utils/supabase_utils.py

import base64
import json
from time import sleep
from typing import Any, Dict, List, Optional

import httpx
from postgrest.exceptions import APIError
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from ..config.settings import settings
from ..utils.logging_config import get_logger
from .upstream import UpstreamUnavailable

logger = get_logger(__name__)

_warned_bad_service_key = False


def supabase_key_role(key: str) -> Optional[str]:
    """Decode the ``role`` claim of a Supabase JWT key (None if undecodable)."""
    try:
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except Exception:
        return None


def service_role_key_is_valid(key: Optional[str] = None) -> bool:
    """Return whether a Supabase key is safe for server-side admin access.

    Supabase supports both the legacy JWT ``service_role`` key and the current
    opaque ``sb_secret_...`` key. Public ``anon``/``sb_publishable_...`` keys
    must never be accepted here: doing so can either violate RLS or, when a
    table has an accidentally permissive policy, silently run privileged API
    code with public credentials.
    """
    global _warned_bad_service_key
    candidate = (key if key is not None else settings.SUPABASE_SERVICE_ROLE_KEY) or ""
    role = supabase_key_role(candidate)
    valid = candidate.startswith("sb_secret_") or role == "service_role"

    # Catch the concrete configuration error that triggered the trusted-device
    # RLS alerts even if a future key format cannot be decoded as a JWT.
    if candidate and candidate == (settings.SUPABASE_KEY or ""):
        valid = False

    if valid:
        return True
    if not _warned_bad_service_key:
        key_type = (
            "publishable"
            if candidate.startswith("sb_publishable_")
            else "legacy JWT" if role is not None else "unknown"
        )
        logger.error(
            "SUPABASE_SERVICE_ROLE_KEY is not an admin key "
            f"(type={key_type}, legacy_role={role!r}). Configure an sb_secret_ "
            "key (recommended) or a legacy service_role JWT from the Supabase "
            "dashboard; never place the anon/publishable key in this setting."
        )
        _warned_bad_service_key = True
    return False


def get_supabase_client(
    use_service_role: bool = False, *, http_client: Optional[httpx.Client] = None
) -> Client:
    """Get Supabase client with optional service role."""
    if not settings.SUPABASE_URL:
        raise ValueError("SUPABASE_URL is not configured")

    if use_service_role:
        if not settings.SUPABASE_SERVICE_ROLE_KEY:
            raise ValueError("SUPABASE_SERVICE_ROLE_KEY is not configured")
        if not service_role_key_is_valid():
            # Fail closed. Continuing with a public key makes the behavior
            # depend on each table's RLS policy and can become a cross-user
            # data exposure when one policy is too broad.
            raise ValueError(
                "SUPABASE_SERVICE_ROLE_KEY must be an sb_secret_ key or a "
                "legacy JWT with role=service_role"
            )
        key = settings.SUPABASE_SERVICE_ROLE_KEY
    else:
        if not settings.SUPABASE_KEY:
            raise ValueError("SUPABASE_KEY is not configured")
        key = settings.SUPABASE_KEY

    return create_client(
        settings.SUPABASE_URL,
        key,
        options=SyncClientOptions(
            httpx_client=http_client,
            postgrest_client_timeout=supabase_timeout(),
            persist_session=False,
            auto_refresh_token=False,
        ),
    )


def supabase_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        settings.SUPABASE_READ_TIMEOUT,
        connect=settings.SUPABASE_CONNECT_TIMEOUT,
        pool=settings.SUPABASE_CONNECT_TIMEOUT,
    )


def run_supabase_read(operation, *, use_service_role: bool = True):
    """Retry a SELECT-only operation once on transport failure, on fresh sockets.

    Call from a sync route or worker thread, never the event loop. Do not pass
    writes, RPCs with side effects, token refreshes or payment operations here.
    Auth/permission/schema errors are deliberately not retried or hidden.
    """
    for attempt in range(2):
        try:
            with httpx.Client(timeout=supabase_timeout()) as http_client:
                client = get_supabase_client(
                    use_service_role=use_service_role, http_client=http_client
                )
                return operation(client)
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as error:
            if attempt:
                logger.warning(
                    "Supabase read unavailable after two attempts (%s)",
                    type(error).__name__,
                )
                raise UpstreamUnavailable("Data service") from error
            logger.warning(
                "Retrying interrupted Supabase read (%s)", type(error).__name__
            )
            sleep(0.2)


def insert_transaction(transaction_data: Dict[str, Any] | List[Dict[str, Any]]):
    """Insert transactions using service role."""
    try:
        supabase = get_supabase_client(use_service_role=True)

        allowed_columns = [
            "id",
            "statement_id",
            "user_id",
            "bank_name",
            "transaction_date",
            "transaction_description",
            "transaction_amount",
            "transaction_type",
            "balance",
            "category",
            "confidence",
            "merchant_name",
            "original_merchant_name",
            "original_category",
        ]

        if isinstance(transaction_data, list):
            cleaned_data = [
                {k: v for k, v in t.items() if k in allowed_columns}
                for t in transaction_data
            ]
            for t in cleaned_data:
                if "bank_name" not in t:
                    t["bank_name"] = "unknown"
        else:
            cleaned_data = {
                k: v for k, v in transaction_data.items() if k in allowed_columns
            }
            if "bank_name" not in cleaned_data:
                cleaned_data["bank_name"] = "unknown"

        return (
            supabase.table(settings.SUPABASE_TABLE_NAME).insert(cleaned_data).execute()
        )
    except APIError as e:
        logger.error(f"Supabase API error while inserting transactions: {e.code}")
        raise
    except Exception:
        logger.error("Unexpected error while inserting transactions")
        raise


def get_transactions(
    user_id: str,
    skip: int = 0,
    limit: int = 100,
    statement_id: Optional[str] = None,
    sort_by: str = "transaction_date",
    sort_order: str = "desc",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    transaction_id: Optional[str] = None,
):
    """Get transactions with filtering and pagination."""
    supabase = get_supabase_client()
    query = (
        supabase.table(settings.SUPABASE_TABLE_NAME).select("*").eq("user_id", user_id)
    )

    if statement_id:
        query = query.eq("statement_id", statement_id)
    if transaction_id:
        query = query.eq("id", transaction_id)
    if date_from:
        query = query.gte("transaction_date", date_from)
    if date_to:
        query = query.lte("transaction_date", date_to)

    query = query.order(sort_by, desc=(sort_order.lower() == "desc"))
    query = query.range(skip, skip + limit - 1)

    return query.execute()


def get_transactions_by_statement_id(statement_id: str):
    """Get all transactions for a specific statement - handles large datasets."""
    supabase = get_supabase_client()

    all_transactions = []
    page_size = 1000  # Supabase default limit
    offset = 0

    while True:
        # Get transactions in batches to handle large datasets
        result = (
            supabase.table(settings.SUPABASE_TABLE_NAME)
            .select("*")
            .eq("statement_id", statement_id)
            .range(offset, offset + page_size - 1)
            .execute()
        )

        if not result.data or len(result.data) == 0:
            break

        all_transactions.extend(result.data)

        # If we got less than page_size, we've reached the end
        if len(result.data) < page_size:
            break

        offset += page_size

    # Return in the same format as the original function
    class MockResult:
        def __init__(self, data):
            self.data = data

    return MockResult(all_transactions)


def update_transaction(
    transaction_id: str, transaction_data: Dict[str, Any], *, user_id: str
):
    """Update a transaction owned by the authenticated user."""
    if not user_id:
        raise ValueError("user_id is required")
    try:
        supabase = get_supabase_client()
        return (
            supabase.table(settings.SUPABASE_TABLE_NAME)
            .update(transaction_data)
            .eq("id", transaction_id)
            .eq("user_id", user_id)
            .execute()
        )
    except APIError as e:
        logger.error(
            f"Supabase API error while updating transaction {transaction_id}: {e.code}"
        )
        raise
    except Exception:
        logger.error(f"Unexpected error while updating transaction {transaction_id}")
        raise


def delete_transaction(transaction_id: str, *, user_id: str):
    """Delete a transaction owned by the authenticated user."""
    if not user_id:
        raise ValueError("user_id is required")
    try:
        supabase = get_supabase_client()
        return (
            supabase.table(settings.SUPABASE_TABLE_NAME)
            .delete()
            .eq("id", transaction_id)
            .eq("user_id", user_id)
            .execute()
        )
    except APIError as e:
        logger.error(
            f"Supabase API error while deleting transaction {transaction_id}: {e.code}"
        )
        raise
    except Exception:
        logger.error(f"Unexpected error while deleting transaction {transaction_id}")
        raise


def get_statement_metadata(user_id: str):
    """Get metadata about all statements for a user."""
    supabase = get_supabase_client()
    return supabase.rpc("get_statement_metadata", {"p_user_id": user_id}).execute()
