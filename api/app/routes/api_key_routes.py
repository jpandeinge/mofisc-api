# api/app/routes/api_key_routes.py

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..config.settings import settings
from ..schemas.api_key import ApiKeyCreateRequest
from ..services.api_keys import ApiKeyService
from ..services.base_service import ServiceOperationError, ServiceValidationError
from ..utils.auth import get_current_user
from ..utils.subscription import require_enterprise
from ..utils.supabase_utils import get_supabase_client

router = APIRouter()


@router.post("")
async def create_api_key(
    payload: ApiKeyCreateRequest,
    current_user=Depends(get_current_user),
    _subscription=Depends(require_enterprise),
):
    try:
        service = ApiKeyService()
        result = await service.create_api_key(
            user_id=str(current_user.id),
            name=payload.name,
            scopes=payload.scopes,
            expires_at=payload.expires_at,
        )
        return {"success": True, "data": result}
    except ServiceValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ServiceOperationError as e:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(status_code=500, detail="Failed to create API key")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(status_code=500, detail="Failed to create API key")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def list_api_keys(
    current_user=Depends(get_current_user),
    _subscription=Depends(require_enterprise),
):
    try:
        service = ApiKeyService()
        keys = await service.list_api_keys(user_id=str(current_user.id))
        return {"success": True, "data": keys}
    except ServiceOperationError as e:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(status_code=500, detail="Failed to retrieve API keys")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(status_code=500, detail="Failed to retrieve API keys")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/usage")
async def get_api_key_usage(
    days: int = 7,
    current_user=Depends(get_current_user),
    _subscription=Depends(require_enterprise),
):
    try:
        service = ApiKeyService()
        usage = await service.get_usage_summary(user_id=str(current_user.id), days=days)
        return {"success": True, "data": usage}
    except ServiceOperationError as e:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(status_code=500, detail="Failed to retrieve usage data")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(status_code=500, detail="Failed to retrieve usage data")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/usage/export")
async def export_api_key_usage(
    days: int = 7,
    current_user=Depends(get_current_user),
    _subscription=Depends(require_enterprise),
):
    try:
        user_id = str(current_user.id)
        days = max(1, min(days, 30))
        max_rows = 10000
        since = datetime.now(timezone.utc) - timedelta(days=days)

        supabase = get_supabase_client(use_service_role=True)
        response = (
            supabase.table("api_key_usage")
            .select(
                "created_at,request_method,request_path,endpoint_group,status_code,response_time_ms"
            )
            .eq("user_id", user_id)
            .gte("created_at", since.isoformat())
            .order("created_at", desc=False)
            .range(0, max_rows - 1)
            .execute()
        )

        rows = response.data or []

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "created_at",
                "request_method",
                "request_path",
                "endpoint_group",
                "status_code",
                "response_time_ms",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.get("created_at"),
                    row.get("request_method"),
                    row.get("request_path"),
                    row.get("endpoint_group"),
                    row.get("status_code"),
                    row.get("response_time_ms"),
                ]
            )

        filename = f"api-usage-{user_id}-{days}d.csv"
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(status_code=500, detail="Failed to export usage data")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{api_key_id}")
async def revoke_api_key(
    api_key_id: str,
    current_user=Depends(get_current_user),
    _subscription=Depends(require_enterprise),
):
    try:
        service = ApiKeyService()
        await service.revoke_api_key(
            user_id=str(current_user.id), api_key_id=api_key_id
        )
        return {"success": True, "message": "API key revoked"}
    except ServiceOperationError as e:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(status_code=500, detail="Failed to revoke API key")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        if settings.ENVIRONMENT == "production":
            raise HTTPException(status_code=500, detail="Failed to revoke API key")
        raise HTTPException(status_code=500, detail=str(e))
