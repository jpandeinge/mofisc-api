"""Sanitized, retryable responses for dependency outages (not empty data)."""

from fastapi import HTTPException


class UpstreamUnavailable(HTTPException):
    def __init__(self, service: str):
        super().__init__(
            status_code=503,
            detail=f"{service} is temporarily unavailable. Please try again shortly.",
            headers={"Retry-After": "5"},
        )
