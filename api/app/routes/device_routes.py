# api/app/routes/device_routes.py

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..errors.exceptions import DatabaseError, NotFoundError
from ..middleware.rate_limiter import limiter
from ..schemas.device import (
    DeviceTrustCheckRequest,
    DeviceTrustCheckResponse,
)
from ..sentry import set_sentry_context
from ..services.security.device_service import DeviceService
from ..utils.auth import get_current_user

router = APIRouter()


@router.post("/devices/check", response_model=DeviceTrustCheckResponse)
@limiter.limit("10/minute")
@limiter.limit("60/hour")
async def check_device(
    request: Request,
    device_request: DeviceTrustCheckRequest,
    current_user=Depends(get_current_user),
):
    """Check if a device is trusted for the current user. Registers new devices automatically."""
    from ..utils.logging_config import get_logger

    logger = get_logger(__name__)

    try:
        device_info = {
            "device_name": device_request.device_name,
            "user_agent": device_request.user_agent,
            "ip_address": device_request.ip_address,
            "browser": device_request.browser,
            "os": device_request.os,
        }
        result = await DeviceService.check_device(
            current_user.id, device_request.fingerprint, device_info
        )
        return JSONResponse(content=result)
    except DatabaseError as e:
        # Device trust should fail open (without blocking the user) if security telemetry is temporarily unavailable.
        logger.error(f"Database error in check_device: {str(e)}", exc_info=True)
        return JSONResponse(
            content={
                "is_trusted": False,
                "is_new_device": False,
                "is_suspicious": False,
                "device_id": None,
                "friendly_name": None,
                "location": None,
                "message": "Device trust temporarily unavailable",
            },
            status_code=200,
        )
    except Exception as e:
        logger.error(f"Unexpected error in check_device: {str(e)}", exc_info=True)
        set_sentry_context(
            "route_error",
            {
                "operation": "check_device",
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        return JSONResponse(
            content={
                "is_trusted": False,
                "is_new_device": False,
                "is_suspicious": False,
                "device_id": None,
                "friendly_name": None,
                "location": None,
                "message": "Device trust temporarily unavailable",
            },
            status_code=200,
        )


@router.get("/devices")
@limiter.limit("30/minute")
@limiter.limit("200/hour")
async def get_devices(
    request: Request,
    current_fingerprint: Optional[str] = Query(
        None,
        description="Current device fingerprint to mark as 'is_current_device'",
        min_length=32,
        max_length=64,
    ),
    current_user=Depends(get_current_user),
):
    """
    Get all devices registered for the current user.

    Pass current_fingerprint to mark the current device in the response.
    """
    try:
        result = await DeviceService.get_devices(current_user.id, current_fingerprint)
        return JSONResponse(content=result)
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        set_sentry_context("route_error", {"operation": "get_devices", "error": str(e)})
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/devices/{device_id}/trust")
@limiter.limit("5/minute")
@limiter.limit("20/hour")
@limiter.limit("50/day")
async def trust_device(
    request: Request,
    device_id: str,
    current_user=Depends(get_current_user),
):
    """Mark a device as trusted."""
    try:
        result = await DeviceService.trust_device(current_user.id, device_id)
        return JSONResponse(content=result)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Device not found")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        set_sentry_context(
            "route_error",
            {"operation": "trust_device", "device_id": device_id, "error": str(e)},
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/devices/{device_id}")
@limiter.limit("5/minute")
@limiter.limit("20/hour")
@limiter.limit("50/day")
async def remove_device(
    request: Request,
    device_id: str,
    current_user=Depends(get_current_user),
):
    """Remove a specific device."""
    try:
        result = await DeviceService.remove_device(current_user.id, device_id)
        return JSONResponse(content=result)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Device not found")
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        set_sentry_context(
            "route_error",
            {"operation": "remove_device", "device_id": device_id, "error": str(e)},
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/devices")
@limiter.limit("5/minute")
@limiter.limit("20/hour")
@limiter.limit("50/day")
async def remove_all_devices(
    request: Request,
    current_user=Depends(get_current_user),
):
    """Remove all registered devices for the current user."""
    try:
        result = await DeviceService.remove_all_devices(current_user.id)
        return JSONResponse(content=result)
    except DatabaseError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        set_sentry_context(
            "route_error", {"operation": "remove_all_devices", "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="Internal server error")
