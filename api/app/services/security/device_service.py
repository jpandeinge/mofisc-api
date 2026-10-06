# api/app/services/security/device_service.py

"""
Device trust management service.

Features:
- Atomic upsert to prevent duplicate devices
- IP geolocation for location tracking
- Friendly device naming
- Suspicious login detection
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from postgrest.exceptions import APIError

from ...config.settings import settings
from ...errors.exceptions import DatabaseError, NotFoundError
from ...sentry import set_sentry_context
from ...utils.supabase_utils import get_supabase_client, service_role_key_is_valid
from .device_naming_service import DeviceNamingService
from .geolocation_service import GeoLocationService


def _extract_error_code(error: Exception) -> Optional[str]:
    return getattr(error, "code", None)


def _is_duplicate_violation(error: Exception) -> bool:
    error_message = str(error).lower()
    error_code = _extract_error_code(error)
    return (
        error_code == "23505"
        or "duplicate key value violates unique constraint" in error_message
    )


def _is_missing_conflict_constraint(error: Exception) -> bool:
    error_message = str(error).lower()
    error_code = _extract_error_code(error)
    return (
        error_code == "42P10"
        or "no unique or exclusion constraint matching the on conflict specification"
        in error_message
    )


def _is_undefined_column(error: Exception) -> bool:
    error_message = str(error).lower()
    error_code = _extract_error_code(error)
    return error_code == "42703" or "does not exist" in error_message


def _parse_ts(value: Optional[str]) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _device_sort_key(device: Dict[str, Any]) -> tuple:
    return (
        _parse_ts(device.get("last_used_at")),
        _parse_ts(device.get("last_login_at")),
        _parse_ts(device.get("created_at")),
    )


def _pick_primary_device(devices: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not devices:
        return None
    return max(devices, key=_device_sort_key)


def _legacy_device_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "id",
        "user_id",
        "fingerprint",
        "device_name",
        "user_agent",
        "ip_address",
        "browser",
        "os",
        "is_trusted",
        "last_used_at",
    }
    return {k: v for k, v in data.items() if k in allowed}


def _cleanup_duplicate_rows(
    supabase_client,
    user_id: str,
    fingerprint: str,
    primary_id: Optional[str],
    logger,
) -> None:
    if not primary_id:
        return
    try:
        rows = (
            supabase_client.table("trusted_devices")
            .select("id")
            .eq("user_id", user_id)
            .eq("fingerprint", fingerprint)
            .execute()
        )
        duplicate_ids = [
            row["id"]
            for row in (rows.data or [])
            if str(row.get("id")) != str(primary_id)
        ]
        for duplicate_id in duplicate_ids:
            supabase_client.table("trusted_devices").delete().eq(
                "id", duplicate_id
            ).execute()
        if duplicate_ids:
            logger.info(
                "Removed duplicate trusted_devices rows for user %s (fingerprint suffix: %s, duplicates: %s)",
                user_id[:8],
                fingerprint[-8:],
                len(duplicate_ids),
            )
    except Exception as cleanup_err:
        logger.warning("Failed to cleanup duplicate devices: %s", cleanup_err)


def _cleanup_duplicate_rows_by_device_name(
    supabase_client,
    user_id: str,
    device_name: Optional[str],
    primary_id: Optional[str],
    logger,
) -> None:
    if not primary_id or not device_name:
        return
    try:
        rows = (
            supabase_client.table("trusted_devices")
            .select("id")
            .eq("user_id", user_id)
            .eq("device_name", device_name)
            .execute()
        )
        duplicate_ids = [
            row["id"]
            for row in (rows.data or [])
            if str(row.get("id")) != str(primary_id)
        ]
        for duplicate_id in duplicate_ids:
            supabase_client.table("trusted_devices").delete().eq(
                "id", duplicate_id
            ).execute()
        if duplicate_ids:
            logger.info(
                "Removed duplicate trusted_devices rows for user %s (device_name suffix: %s, duplicates: %s)",
                user_id[:8],
                device_name[-8:],
                len(duplicate_ids),
            )
    except Exception as cleanup_err:
        logger.warning(
            "Failed to cleanup duplicate devices by device_name: %s", cleanup_err
        )


def _normalize_client_device_name(raw_value: Optional[str]) -> Optional[str]:
    value = (raw_value or "").strip()
    if not value:
        return None
    # Only treat generated client identifiers as strong device identity hints.
    if not value.startswith("client_"):
        return None
    if len(value) < 20:
        return None
    return value


def _update_known_device(
    *,
    supabase_client,
    device: Dict[str, Any],
    user_id: str,
    fingerprint: str,
    device_info: Dict[str, Any],
    friendly_name: str,
    geo,
    now: str,
    logger,
) -> Dict[str, Any]:
    ip_address = device_info.get("ip_address")
    device_name = _normalize_client_device_name(device_info.get("device_name"))

    is_suspicious = False
    if geo.success and device.get("country_code"):
        if geo.country_code != device.get("country_code"):
            is_suspicious = True
            logger.warning(
                "Suspicious login: user %s from new country %s",
                user_id[:8],
                geo.country_code,
            )

    update_data: Dict[str, Any] = {
        "fingerprint": fingerprint,
        "last_used_at": now,
        "last_login_at": now,
        "login_count": (device.get("login_count") or 0) + 1,
    }

    if device_name:
        update_data["device_name"] = device_name
    if device_info.get("user_agent"):
        update_data["user_agent"] = device_info.get("user_agent")
    if device_info.get("browser"):
        update_data["browser"] = device_info.get("browser")
    if device_info.get("os"):
        update_data["os"] = device_info.get("os")

    if ip_address:
        update_data["ip_address"] = ip_address
        update_data["last_login_ip"] = ip_address

    if geo.success:
        update_data["last_login_city"] = geo.city
        update_data["last_login_country"] = geo.country

    if is_suspicious:
        update_data["is_suspicious"] = True

    if (
        not device.get("friendly_name")
        or device.get("friendly_name") == "Unknown Device"
    ):
        update_data["friendly_name"] = friendly_name

    try:
        supabase_client.table("trusted_devices").update(update_data).eq(
            "id", device["id"]
        ).execute()
    except Exception as update_err:
        logger.warning("Failed to update device: %s", update_err)

    primary_id = str(device.get("id"))
    _cleanup_duplicate_rows(supabase_client, user_id, fingerprint, primary_id, logger)
    _cleanup_duplicate_rows_by_device_name(
        supabase_client, user_id, device_name, primary_id, logger
    )

    return {
        "is_trusted": bool(device.get("is_trusted")),
        "is_new_device": False,
        "is_suspicious": is_suspicious,
        "device_id": device["id"],
        "friendly_name": device.get("friendly_name") or friendly_name,
        "location": GeoLocationService.format_location(geo),
        "message": (
            "Known device"
            if device.get("is_trusted")
            else "Device recognized but not trusted"
        ),
    }


def _manual_merge_device(
    supabase_client, payload: Dict[str, Any], user_id: str, fingerprint: str
):
    existing = (
        supabase_client.table("trusted_devices")
        .select("*")
        .eq("user_id", user_id)
        .eq("fingerprint", fingerprint)
        .execute()
    )
    primary = _pick_primary_device(existing.data or [])
    if primary:
        update_data = {k: v for k, v in payload.items() if k != "id"}
        supabase_client.table("trusted_devices").update(update_data).eq(
            "id", primary["id"]
        ).execute()
        return (
            supabase_client.table("trusted_devices")
            .select("*")
            .eq("id", primary["id"])
            .execute()
        )

    return supabase_client.table("trusted_devices").insert(payload).execute()


def _safe_upsert_device(supabase_client, data: dict, user_id: str, fingerprint: str):
    """
    Upsert helper that gracefully handles unique constraint collisions.

    Supabase upsert should merge on the conflict target, but Postgres can still
    throw 23505 errors in race conditions. Fall back to update if that happens.
    """
    from ...utils.logging_config import get_logger

    logger = get_logger(__name__)

    def _upsert(payload: Dict[str, Any]):
        return (
            supabase_client.table("trusted_devices")
            .upsert(payload, on_conflict="user_id,fingerprint")
            .execute()
        )

    try:
        return _upsert(data)
    except Exception as e:
        if _is_undefined_column(e):
            logger.warning(
                "trusted_devices extended columns unavailable; falling back to legacy payload"
            )
            legacy_payload = _legacy_device_payload(data)
            try:
                return _upsert(legacy_payload)
            except Exception as legacy_err:
                if _is_missing_conflict_constraint(
                    legacy_err
                ) or _is_duplicate_violation(legacy_err):
                    return _manual_merge_device(
                        supabase_client, legacy_payload, user_id, fingerprint
                    )
                raise

        if _is_missing_conflict_constraint(e) or _is_duplicate_violation(e):
            logger.warning(
                "trusted_devices conflict upsert unavailable; using manual merge fallback"
            )
            return _manual_merge_device(supabase_client, data, user_id, fingerprint)

        raise


class DeviceService:
    @staticmethod
    async def check_device(
        user_id: str, fingerprint: str, device_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Check if device is trusted for a user.
        Uses atomic upsert to prevent duplicate device entries.

        Returns trust status, whether it's new, and location info.
        """
        from ...utils.logging_config import get_logger

        logger = get_logger(__name__)

        try:
            user_id_str = str(user_id)
            logger.info(f"Checking device for user {user_id_str[:8]}...")
            if (
                not settings.SUPABASE_SERVICE_ROLE_KEY
                or not service_role_key_is_valid()
            ):
                # Absent OR mis-set key (e.g. anon key in the service slot):
                # writes would run as anon and violate trusted_devices RLS, so
                # fail open instead of erroring on every login.
                logger.warning(
                    "Service-role key missing or invalid - device trust check disabled"
                )
                return {
                    "is_trusted": False,
                    "is_new_device": False,
                    "is_suspicious": False,
                    "device_id": None,
                    "friendly_name": None,
                    "location": None,
                    "message": "Device trust temporarily unavailable",
                }

            supabase = get_supabase_client(use_service_role=True)

            # Get IP geolocation
            ip_address = device_info.get("ip_address")
            geo = await GeoLocationService.lookup(ip_address)
            client_device_name = _normalize_client_device_name(
                device_info.get("device_name")
            )

            # Generate friendly device name
            friendly_name = DeviceNamingService.generate_friendly_name(
                device_info.get("browser"), device_info.get("os")
            )

            now = datetime.utcnow().isoformat()

            # Prefer stable client identifier when available to avoid repeated duplicate
            # rows if browser properties fluctuate between sessions.
            if client_device_name:
                existing_by_name = (
                    supabase.table("trusted_devices")
                    .select("*")
                    .eq("user_id", user_id_str)
                    .eq("device_name", client_device_name)
                    .execute()
                )
                existing_named_devices = existing_by_name.data or []
                if existing_named_devices:
                    known_device = _pick_primary_device(existing_named_devices)
                    if not known_device:
                        raise DatabaseError("Unable to resolve existing trusted device")
                    return _update_known_device(
                        supabase_client=supabase,
                        device=known_device,
                        user_id=user_id_str,
                        fingerprint=fingerprint,
                        device_info=device_info,
                        friendly_name=friendly_name,
                        geo=geo,
                        now=now,
                        logger=logger,
                    )

            # Check if device already exists
            existing_response = (
                supabase.table("trusted_devices")
                .select("*")
                .eq("user_id", user_id_str)
                .eq("fingerprint", fingerprint)
                .execute()
            )

            existing_devices = existing_response.data or []
            if existing_devices:
                device = _pick_primary_device(existing_devices)
                if not device:
                    raise DatabaseError("Unable to resolve existing trusted device")
                return _update_known_device(
                    supabase_client=supabase,
                    device=device,
                    user_id=user_id_str,
                    fingerprint=fingerprint,
                    device_info=device_info,
                    friendly_name=friendly_name,
                    geo=geo,
                    now=now,
                    logger=logger,
                )

            # New device - use atomic upsert to prevent race conditions
            new_device_id = str(uuid4())
            device_data = {
                "id": new_device_id,
                "user_id": user_id_str,
                "fingerprint": fingerprint,
                "device_name": client_device_name,
                "user_agent": device_info.get("user_agent"),
                "ip_address": ip_address,
                "browser": device_info.get("browser"),
                "os": device_info.get("os"),
                "is_trusted": False,
                "friendly_name": friendly_name,
                "login_count": 1,
                "is_suspicious": False,
                "last_login_at": now,
                "last_login_ip": ip_address,
                "last_used_at": now,
            }

            # Add initial location if available
            if geo.success:
                device_data["city"] = geo.city
                device_data["country"] = geo.country
                device_data["country_code"] = geo.country_code
                device_data["region"] = geo.region
                device_data["last_login_city"] = geo.city
                device_data["last_login_country"] = geo.country

            # Atomic upsert - uses unique constraint on (user_id, fingerprint)
            # Uses safe wrapper to handle race condition edge cases
            upsert_response = _safe_upsert_device(
                supabase, device_data, user_id_str, fingerprint
            )

            # Determine if this was actually a new insert or an update
            # by checking the returned ID
            result_rows = upsert_response.data or []
            result_device = _pick_primary_device(result_rows)
            if not result_device:
                lookup = (
                    supabase.table("trusted_devices")
                    .select("*")
                    .eq("user_id", user_id_str)
                    .eq("fingerprint", fingerprint)
                    .execute()
                )
                result_device = _pick_primary_device(lookup.data or [])
            actual_device_id = (
                str(result_device["id"]) if result_device else new_device_id
            )
            is_new_device = actual_device_id == new_device_id

            _cleanup_duplicate_rows(
                supabase, user_id_str, fingerprint, actual_device_id, logger
            )
            _cleanup_duplicate_rows_by_device_name(
                supabase,
                user_id_str,
                client_device_name,
                actual_device_id,
                logger,
            )

            return {
                "is_trusted": False,
                "is_new_device": is_new_device,
                "is_suspicious": False,
                "device_id": actual_device_id,
                "friendly_name": friendly_name,
                "location": GeoLocationService.format_location(geo),
                "message": (
                    "New device detected" if is_new_device else "Device recognized"
                ),
            }

        except APIError as e:
            logger.error(f"Supabase error checking device: {str(e)}", exc_info=True)
            set_sentry_context(
                "device_service_error",
                {
                    "operation": "check_device",
                    "user_id": user_id_str,
                    "error": str(e),
                    "error_type": "APIError",
                },
            )
            raise DatabaseError("Error checking device")
        except Exception as e:
            logger.error(f"Error checking device: {str(e)}", exc_info=True)
            set_sentry_context(
                "device_service_error",
                {
                    "operation": "check_device",
                    "user_id": str(user_id),
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            raise DatabaseError(f"Error checking device: {str(e)}")

    @staticmethod
    async def get_devices(
        user_id: str, current_fingerprint: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all devices for a user.

        Args:
            user_id: The user's ID
            current_fingerprint: Optional fingerprint to mark as current device
        """
        from ...utils.logging_config import get_logger

        logger = get_logger(__name__)

        try:
            user_id_str = str(user_id)
            logger.info(f"Fetching devices for user {user_id_str[:8]}...")

            supabase = get_supabase_client(use_service_role=True)

            response = (
                supabase.table("trusted_devices")
                .select("*")
                .eq("user_id", user_id_str)
                .order("last_used_at", desc=True)
                .execute()
            )

            # Double-check that all returned devices belong to this user
            # (defense in depth - should never happen if query is correct)
            filtered_data = [
                d for d in response.data if str(d.get("user_id")) == user_id_str
            ]

            if len(filtered_data) != len(response.data):
                logger.error(
                    f"SECURITY: Device query returned {len(response.data)} devices but only "
                    f"{len(filtered_data)} belong to user {user_id_str[:8]}. This should not happen!"
                )

            logger.info(
                f"Found {len(filtered_data)} devices for user {user_id_str[:8]}"
            )

            # Deduplicate by fingerprint (keep most recently used) to avoid repeated rows in UI.
            deduped_map: Dict[str, Dict[str, Any]] = {}
            duplicate_ids: List[str] = []
            for device in sorted(filtered_data, key=_device_sort_key, reverse=True):
                fingerprint = str(device.get("fingerprint") or device.get("id"))
                if fingerprint in deduped_map:
                    duplicate_ids.append(str(device.get("id")))
                    continue
                deduped_map[fingerprint] = device

            if duplicate_ids:
                logger.warning(
                    "Found %s duplicate trusted device rows for user %s; cleaning up",
                    len(duplicate_ids),
                    user_id_str[:8],
                )
                for duplicate_id in duplicate_ids:
                    if duplicate_id and duplicate_id != "None":
                        try:
                            supabase.table("trusted_devices").delete().eq(
                                "id", duplicate_id
                            ).execute()
                        except Exception as cleanup_err:
                            logger.warning(
                                "Failed to delete duplicate trusted device row %s: %s",
                                duplicate_id,
                                cleanup_err,
                            )

            devices = []
            for device in deduped_map.values():
                # Build location string
                location = None
                if device.get("city") and device.get("country"):
                    location = f"{device['city']}, {device['country']}"
                elif device.get("country"):
                    location = device["country"]

                # Build last login location
                last_login_location = None
                if device.get("last_login_city") and device.get("last_login_country"):
                    last_login_location = (
                        f"{device['last_login_city']}, {device['last_login_country']}"
                    )

                devices.append(
                    {
                        "id": device["id"],
                        "fingerprint": device["fingerprint"],
                        "device_name": device.get("device_name"),
                        "friendly_name": device.get("friendly_name")
                        or "Unknown Device",
                        "browser": device.get("browser"),
                        "os": device.get("os"),
                        "ip_address": device.get("ip_address"),
                        "is_trusted": device["is_trusted"],
                        "is_current_device": (
                            current_fingerprint
                            and device["fingerprint"] == current_fingerprint
                        ),
                        "is_suspicious": device.get("is_suspicious", False),
                        "location": location,
                        "last_login_location": last_login_location,
                        "login_count": device.get("login_count", 1),
                        "last_used_at": device.get("last_used_at"),
                        "last_login_at": device.get("last_login_at"),
                        "created_at": device.get("created_at"),
                    }
                )

            return devices

        except APIError as e:
            set_sentry_context(
                "device_service_error",
                {"operation": "get_devices", "user_id": user_id, "error": str(e)},
            )
            raise DatabaseError("Error fetching devices")
        except Exception as e:
            set_sentry_context(
                "device_service_error",
                {"operation": "get_devices", "user_id": user_id, "error": str(e)},
            )
            raise DatabaseError(f"Error fetching devices: {str(e)}")

    @staticmethod
    async def trust_device(user_id: str, device_id: str) -> Dict[str, Any]:
        """Mark a device as trusted."""
        try:
            user_id_str = str(user_id)
            supabase = get_supabase_client(use_service_role=True)

            # Check if device exists and belongs to user
            response = (
                supabase.table("trusted_devices")
                .select("*")
                .eq("id", device_id)
                .eq("user_id", user_id_str)
                .execute()
            )

            if not response.data:
                raise NotFoundError("TrustedDevice", device_id)

            # Update to trusted and clear suspicious flag
            supabase.table("trusted_devices").update(
                {"is_trusted": True, "is_suspicious": False}
            ).eq("id", device_id).execute()

            return {
                "id": device_id,
                "is_trusted": True,
                "message": "Device trusted successfully",
            }

        except NotFoundError:
            raise
        except APIError as e:
            set_sentry_context(
                "device_service_error",
                {
                    "operation": "trust_device",
                    "user_id": user_id,
                    "device_id": device_id,
                    "error": str(e),
                },
            )
            raise DatabaseError("Error trusting device")
        except Exception as e:
            set_sentry_context(
                "device_service_error",
                {
                    "operation": "trust_device",
                    "user_id": user_id,
                    "device_id": device_id,
                    "error": str(e),
                },
            )
            raise DatabaseError(f"Error trusting device: {str(e)}")

    @staticmethod
    async def remove_device(user_id: str, device_id: str) -> Dict[str, Any]:
        """Remove a device from trusted devices."""
        try:
            user_id_str = str(user_id)
            supabase = get_supabase_client(use_service_role=True)

            # Check if device exists and belongs to user
            response = (
                supabase.table("trusted_devices")
                .select("*")
                .eq("id", device_id)
                .eq("user_id", user_id_str)
                .execute()
            )

            if not response.data:
                raise NotFoundError("TrustedDevice", device_id)

            # Delete device
            supabase.table("trusted_devices").delete().eq("id", device_id).execute()

            return {"message": "Device removed successfully"}

        except NotFoundError:
            raise
        except APIError as e:
            set_sentry_context(
                "device_service_error",
                {
                    "operation": "remove_device",
                    "user_id": user_id,
                    "device_id": device_id,
                    "error": str(e),
                },
            )
            raise DatabaseError("Error removing device")
        except Exception as e:
            set_sentry_context(
                "device_service_error",
                {
                    "operation": "remove_device",
                    "user_id": user_id,
                    "device_id": device_id,
                    "error": str(e),
                },
            )
            raise DatabaseError(f"Error removing device: {str(e)}")

    @staticmethod
    async def remove_all_devices(user_id: str) -> Dict[str, Any]:
        """Remove all devices for a user except current (used on password change)."""
        try:
            user_id_str = str(user_id)
            supabase = get_supabase_client(use_service_role=True)

            # Get count first
            count_response = (
                supabase.table("trusted_devices")
                .select("id", count="exact")
                .eq("user_id", user_id_str)
                .execute()
            )

            # Delete all devices
            supabase.table("trusted_devices").delete().eq(
                "user_id", user_id_str
            ).execute()

            return {"message": f"Removed {count_response.count} devices"}

        except APIError as e:
            set_sentry_context(
                "device_service_error",
                {
                    "operation": "remove_all_devices",
                    "user_id": user_id,
                    "error": str(e),
                },
            )
            raise DatabaseError("Error removing devices")
        except Exception as e:
            set_sentry_context(
                "device_service_error",
                {
                    "operation": "remove_all_devices",
                    "user_id": user_id,
                    "error": str(e),
                },
            )
            raise DatabaseError(f"Error removing devices: {str(e)}")
