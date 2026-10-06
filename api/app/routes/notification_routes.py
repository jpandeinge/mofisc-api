# api/app/routes/notification_routes.py
"""
Notification routes - FastAPI endpoints for notification management.

The direct send endpoint completes persistence and provider delivery before the
request finishes, which is safe on request-scoped/serverless runtimes.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..middleware.rate_limiter import limiter
from ..sentry import set_sentry_context
from ..services.notifications.base import NotificationPriority, NotificationType
from ..services.notifications.notification_service import notification_service
from ..services.subscriptions import subscription_reminder_service
from ..utils.auth import get_current_user
from ..utils.logging_config import get_logger
from ..utils.supabase_utils import get_supabase_client, run_supabase_read

router = APIRouter()


class SendNotificationRequest(BaseModel):
    """Request model for sending notification."""

    notification_type: NotificationType
    data: Dict[str, Any]
    priority: Optional[NotificationPriority] = NotificationPriority.MEDIUM


class TestNotificationRequest(BaseModel):
    """Request model for testing notification delivery."""

    channels: Optional[List[str]] = None  # in_app, email, sms, push
    message: Optional[str] = "This is a test notification from Galahad"


class RegisterDeviceRequest(BaseModel):
    """Request model for registering a device token."""

    device_token: str
    device_type: Optional[str] = "web"  # ios, android, web
    device_name: Optional[str] = None


class MarkReadRequest(BaseModel):
    """Request model for marking notifications as read."""

    notification_ids: List[str]


@router.post("/send")
@limiter.limit("10/minute")
@limiter.limit("100/hour")
@limiter.limit("500/day")
async def send_notification(
    request: Request,
    notification_request: SendNotificationRequest,
    current_user=Depends(get_current_user),
):
    """
    Send notification to current user.

    This endpoint allows sending notifications programmatically.
    Useful for manual notifications or integration testing.

    The database record and selected provider attempts complete before the
    request returns. Durable asynchronous provider retries should be delegated
    to the deployment queue when the API moves to Vercel.

    Rate Limits:
    - 10 requests per minute
    - 100 requests per hour
    - 500 requests per day
    """
    try:
        result = await notification_service.send_notification(
            user_id=str(current_user.id),
            notification_type=notification_request.notification_type,
            data=notification_request.data,
            priority=notification_request.priority,
        )

        return JSONResponse(
            content={
                "success": any(result.values()),
                "message": "Notification processed",
                "status": "completed",
                "channels": result,
            }
        )

    except Exception as e:
        set_sentry_context(
            "notification_route_error",
            {
                "operation": "send_notification",
                "user_id": current_user.id,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to send notification: {str(e)}"
        )


@router.post("/test")
@limiter.limit("5/minute")
@limiter.limit("20/hour")
async def test_notification(
    request: Request,
    test_request: TestNotificationRequest,
    current_user=Depends(get_current_user),
):
    """
    Send test notification to verify delivery channels.

    This is useful for users to test their notification settings.

    Rate Limits:
    - 5 requests per minute
    - 20 requests per hour
    """
    try:
        from ..services.notifications.base import NotificationChannel

        # Map string channels to enum
        force_channels = None
        if test_request.channels:
            channel_map = {
                "in_app": NotificationChannel.IN_APP,
                "email": NotificationChannel.EMAIL,
                "sms": NotificationChannel.SMS,
                "push": NotificationChannel.PUSH,
            }
            force_channels = [
                channel_map[ch] for ch in test_request.channels if ch in channel_map
            ]

        # Send test notification
        result = await notification_service.send_notification(
            user_id=current_user.id,
            notification_type=NotificationType.SECURITY_ALERT,
            data={
                "alert_type": "Test Notification",
                "timestamp": "now",
                "location": "Settings Page",
                "message": test_request.message,
            },
            priority=NotificationPriority.LOW,
            force_channels=force_channels,
        )

        return JSONResponse(
            content={
                "success": any(result.values()),
                "channels_tested": result,
                "message": "Test notification sent",
            }
        )

    except Exception as e:
        set_sentry_context(
            "notification_route_error",
            {
                "operation": "test_notification",
                "user_id": current_user.id,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to send test notification: {str(e)}"
        )


@router.get("/status")
@limiter.limit("30/minute")
@limiter.limit("200/hour")
async def get_notification_status(
    request: Request, current_user=Depends(get_current_user)
):
    """
    Get notification provider status.

    Returns which notification channels are configured and available.

    Rate Limits:
    - 30 requests per minute
    - 200 requests per hour
    """
    try:
        status = {
            "in_app": {
                "configured": True,
                "provider": "Supabase Realtime",
            },
            "email": {
                "configured": notification_service.email_provider.is_configured(),
                "provider": "SendGrid",
            },
            "sms": {
                "configured": notification_service.sms_provider.is_configured(),
                "provider": "Firebase + Twilio",
            },
            "push": {
                "configured": notification_service.push_provider.is_configured(),
                "provider": "Firebase Cloud Messaging (optional)",
            },
        }

        return JSONResponse(
            content={
                "providers": status,
                "any_configured": any(p["configured"] for p in status.values()),
            }
        )

    except Exception as e:
        set_sentry_context(
            "notification_route_error",
            {"operation": "get_notification_status", "error": str(e)},
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to get notification status: {str(e)}"
        )


def _register_device_background(
    user_id: str,
    device_token: str,
    device_type: str,
    device_name: Optional[str],
):
    """Background task to register device token (non-blocking)."""
    try:
        from ..utils.logging_config import get_logger

        logger = get_logger(__name__)

        supabase = get_supabase_client(use_service_role=True)

        # Check if device already exists
        existing_response = (
            supabase.table("user_devices")
            .select("*")
            .eq("device_token", device_token)
            .execute()
        )

        if existing_response.data:
            # Update existing device
            existing_device = existing_response.data[0]
            supabase.table("user_devices").update(
                {
                    "user_id": user_id,
                    "device_type": device_type,
                    "device_name": device_name,
                    "is_active": True,
                    "last_used": datetime.utcnow().isoformat(),
                }
            ).eq("id", existing_device["id"]).execute()

            logger.info(
                "Device token updated in background",
                extra={"device_id": existing_device["id"], "user_id": user_id},
            )
        else:
            # Create new device
            device_id = str(uuid4())
            supabase.table("user_devices").insert(
                {
                    "id": device_id,
                    "user_id": user_id,
                    "device_token": device_token,
                    "device_type": device_type,
                    "device_name": device_name,
                    "is_active": True,
                }
            ).execute()

            logger.info(
                "Device registered successfully in background",
                extra={"device_id": device_id, "user_id": user_id},
            )

    except Exception as e:
        from ..utils.logging_config import get_logger

        logger = get_logger(__name__)
        logger.error(
            f"Failed to register device in background: {str(e)}",
            extra={"user_id": user_id, "error": str(e)},
        )
        # Don't raise - this is a background task


@router.post("/devices/register")
@limiter.limit("5/minute")
@limiter.limit("20/hour")
@limiter.limit("50/day")
async def register_device(
    request: Request,
    device_request: RegisterDeviceRequest,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
):
    """
    Register a device token for push notifications.

    This endpoint allows users to register their device tokens
    to receive push notifications via Firebase Cloud Messaging.

    Device registration happens in the background for fast response times.
    The user doesn't need to wait for database operations to complete.

    Rate Limits:
    - 5 requests per minute
    - 20 requests per hour
    - 50 requests per day
    """
    try:
        # Queue device registration in background (don't block response)
        background_tasks.add_task(
            _register_device_background,
            str(current_user.id),
            device_request.device_token,
            device_request.device_type,
            device_request.device_name,
        )

        # Return immediately (user doesn't wait for DB operations)
        return JSONResponse(
            content={
                "success": True,
                "message": "Device registration queued",
                "status": "processing",
            }
        )

    except Exception as e:
        set_sentry_context(
            "notification_route_error",
            {
                "operation": "register_device",
                "user_id": current_user.id,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to queue device registration: {str(e)}"
        )


@router.delete("/devices/{device_id}")
@limiter.limit("5/minute")
@limiter.limit("20/hour")
@limiter.limit("50/day")
async def unregister_device(
    request: Request, device_id: str, current_user=Depends(get_current_user)
):
    """
    Unregister a device token.

    This removes the device from receiving push notifications.

    Rate Limits:
    - 5 requests per minute
    - 20 requests per hour
    - 50 requests per day
    """
    try:
        supabase = get_supabase_client(use_service_role=True)
        user_id_str = str(current_user.id)

        # Check if device exists and belongs to current user
        device_response = (
            supabase.table("user_devices")
            .select("*")
            .eq("id", device_id)
            .eq("user_id", user_id_str)
            .execute()
        )

        if not device_response.data:
            raise HTTPException(status_code=404, detail="Device not found")

        # Mark device as inactive
        supabase.table("user_devices").update({"is_active": False}).eq(
            "id", device_id
        ).execute()

        return JSONResponse(
            content={"success": True, "message": "Device unregistered successfully"}
        )

    except HTTPException:
        raise
    except Exception as e:
        set_sentry_context(
            "notification_route_error",
            {
                "operation": "unregister_device",
                "user_id": user_id_str,
                "device_id": device_id,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to unregister device: {str(e)}"
        )


def _read_notification_history(client, user_id, limit, offset, unread_only):
    query = (
        client.table("notifications").select("*", count="exact").eq("user_id", user_id)
    )
    if unread_only:
        query = query.eq("is_read", False)
    response = (
        query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    )
    unread_count = _read_unread_count(client, user_id)
    return {
        "notifications": [
            {
                "id": n["id"],
                "type": n["notification_type"],
                "title": n["title"],
                "body": n["body"],
                "data": n["data"],
                "is_read": n["is_read"],
                "read_at": n.get("read_at"),
                "delivered_via": n.get("delivered_via"),
                "action_url": n.get("action_url"),
                "icon": n.get("icon"),
                "priority": n["priority"],
                "created_at": n["created_at"],
            }
            for n in (response.data or [])
        ],
        "total": response.count or 0,
        "limit": limit,
        "offset": offset,
        "unread_count": unread_count,
    }


@router.get("/history")
@limiter.limit("100/minute")
@limiter.limit("1000/hour")
async def get_notification_history(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    unread_only: bool = False,
    current_user=Depends(get_current_user),
):
    """Read notification history off the event loop; outages are not empty inboxes."""
    try:
        result = await run_in_threadpool(
            run_supabase_read,
            lambda client: _read_notification_history(
                client, str(current_user.id), limit, offset, unread_only
            ),
        )
        return JSONResponse(content=result)
    except HTTPException:
        raise
    except Exception:
        get_logger(__name__).exception("Failed to load notification history")
        raise HTTPException(
            status_code=500, detail="Failed to load notification history"
        )


@router.post("/mark-read")
@limiter.limit("50/minute")
@limiter.limit("500/hour")
async def mark_notifications_read(
    request: Request,
    mark_request: MarkReadRequest,
    current_user=Depends(get_current_user),
):
    """
    Mark notifications as read.

    Updates the read status of specified notifications.

    Rate Limits:
    - 50 requests per minute
    - 500 requests per hour
    """
    try:
        user_id_str = str(current_user.id)
        supabase = get_supabase_client(use_service_role=True)

        # First verify notifications belong to current user
        notifications_response = (
            supabase.table("notifications")
            .select("id")
            .eq("user_id", user_id_str)
            .in_("id", mark_request.notification_ids)
            .execute()
        )

        if not notifications_response.data:
            return JSONResponse(
                content={
                    "success": True,
                    "message": "No notifications found to mark as read",
                }
            )

        # Update all matching notifications
        supabase.table("notifications").update(
            {"is_read": True, "read_at": datetime.utcnow().isoformat()}
        ).in_("id", [n["id"] for n in notifications_response.data]).execute()

        return JSONResponse(
            content={
                "success": True,
                "message": f"Marked {len(notifications_response.data)} notifications as read",
            }
        )

    except Exception as e:
        set_sentry_context(
            "notification_route_error",
            {
                "operation": "mark_notifications_read",
                "user_id": current_user.id,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to mark notifications as read: {str(e)}"
        )


@router.post("/mark-all-read")
@limiter.limit("50/minute")
@limiter.limit("500/hour")
async def mark_all_notifications_read(
    request: Request, current_user=Depends(get_current_user)
):
    """
    Mark all notifications as read for the current user.

    Rate Limits:
    - 50 requests per minute
    - 500 requests per hour
    """
    try:
        supabase = get_supabase_client(use_service_role=True)
        user_id_str = str(current_user.id)

        # Get count of unread notifications first
        unread_response = (
            supabase.table("notifications")
            .select("id", count="exact")
            .eq("user_id", user_id_str)
            .eq("is_read", False)
            .execute()
        )

        # Update all unread notifications
        if unread_response.data:
            supabase.table("notifications").update(
                {"is_read": True, "read_at": datetime.utcnow().isoformat()}
            ).eq("user_id", user_id_str).eq("is_read", False).execute()

        return JSONResponse(
            content={
                "success": True,
                "message": f"Marked {unread_response.count} notifications as read",
            }
        )

    except Exception as e:
        set_sentry_context(
            "notification_route_error",
            {
                "operation": "mark_all_notifications_read",
                "user_id": user_id_str,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to mark all notifications as read: {str(e)}",
        )


def _read_unread_count(client, user_id):
    response = (
        client.table("notifications")
        .select("id", count="exact", head=True)
        .eq("user_id", user_id)
        .eq("is_read", False)
        .execute()
    )
    return response.count or 0


@router.get("/unread-count")
@limiter.limit("200/minute")
@limiter.limit("2000/hour")
async def get_unread_count(request: Request, current_user=Depends(get_current_user)):
    """Return the actual unread count, or a retryable outage response."""
    try:
        count = await run_in_threadpool(
            run_supabase_read,
            lambda client: _read_unread_count(client, str(current_user.id)),
        )
        return JSONResponse(content={"unread_count": count})
    except HTTPException:
        raise
    except Exception:
        get_logger(__name__).exception("Failed to load unread notification count")
        raise HTTPException(status_code=500, detail="Failed to load unread count")


def _check_subscriptions_background():
    """Background task to check upcoming subscriptions (non-blocking)."""
    import asyncio

    from ..utils.logging_config import get_logger

    logger = get_logger(__name__)

    try:
        # Run async subscription check in a new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(
                subscription_reminder_service.check_upcoming_subscriptions()
            )
            logger.info(
                "Subscription check completed in background",
                extra={"result": result},
            )
        finally:
            loop.close()

    except Exception as e:
        logger.error(
            f"Failed to check subscriptions in background: {str(e)}",
            exc_info=True,
        )


@router.post("/subscriptions/check")
@limiter.limit("5/minute")
@limiter.limit("20/hour")
async def trigger_subscription_check(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_user),
):
    """
    Manually trigger subscription reminder check for testing.

    This endpoint runs the subscription check which will
    send notifications for upcoming subscription payments.

    Uses FastAPI BackgroundTasks - no Redis required.

    Useful for:
    - Testing subscription reminders
    - Forcing an immediate check outside the scheduled time

    Rate Limits:
    - 5 requests per minute
    - 20 requests per hour
    """
    try:
        # Queue the subscription check in background
        background_tasks.add_task(_check_subscriptions_background)

        return JSONResponse(
            content={
                "success": True,
                "message": "Subscription check started",
                "status": "processing",
            }
        )

    except Exception as e:
        set_sentry_context(
            "notification_route_error",
            {
                "operation": "trigger_subscription_check",
                "user_id": current_user.id,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to start subscription check: {str(e)}"
        )


@router.get("/subscriptions/upcoming")
@limiter.limit("100/minute")
@limiter.limit("1000/hour")
async def get_upcoming_subscriptions(
    request: Request,
    days: int = 7,
    current_user=Depends(get_current_user),
):
    """
    Get upcoming subscription payments for the current user.

    Returns subscriptions that are due within the specified number of days.
    This is a read-only endpoint that doesn't send notifications.

    Args:
        days: Number of days to look ahead (default: 7)

    Rate Limits:
    - 100 requests per minute
    - 1000 requests per hour
    """
    try:
        # Use the subscription reminder service directly
        result = await subscription_reminder_service.get_upcoming_for_user(
            user_id=str(current_user.id),
            days=days,
        )

        return JSONResponse(content=result)

    except Exception as e:
        set_sentry_context(
            "notification_route_error",
            {
                "operation": "get_upcoming_subscriptions",
                "user_id": current_user.id,
                "error": str(e),
            },
        )
        raise HTTPException(
            status_code=500, detail=f"Failed to get upcoming subscriptions: {str(e)}"
        )
