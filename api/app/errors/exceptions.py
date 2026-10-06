# api/app/errors/exceptions.py

from typing import Any, Dict, Optional


class GalahadError(Exception):
    """Base exception for Galahad API"""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: str = "INTERNAL_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)


class ValidationError(GalahadError):
    """Raised when input validation fails"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=400,
            error_code="VALIDATION_ERROR",
            details=details,
        )


class AuthenticationError(GalahadError):
    """Raised when authentication fails"""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(
            message=message, status_code=401, error_code="AUTHENTICATION_ERROR"
        )


class AuthorizationError(GalahadError):
    """Raised when user doesn't have required permissions"""

    def __init__(self, message: str = "Not authorized"):
        super().__init__(
            message=message, status_code=403, error_code="AUTHORIZATION_ERROR"
        )


class NotFoundError(GalahadError):
    """Raised when a resource is not found"""

    def __init__(self, resource: str, resource_id: str):
        super().__init__(
            message=f"{resource} with ID {resource_id} not found",
            status_code=404,
            error_code="NOT_FOUND",
            details={"resource": resource, "id": resource_id},
        )


class StatementProcessingError(GalahadError):
    """Raised when statement processing fails"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=422,
            error_code="STATEMENT_PROCESSING_ERROR",
            details=details,
        )


class DatabaseError(GalahadError):
    """Raised when database operations fail"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=500,
            error_code="DATABASE_ERROR",
            details=details,
        )
