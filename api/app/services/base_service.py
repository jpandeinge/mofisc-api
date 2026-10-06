# api/app/services/base_service.py

"""
Base service class providing standard patterns for all services.

"""

from typing import Optional

from ..utils.logging_config import get_logger


class BaseService:
    """
    Base class for all service classes.

    Provides common functionality and establishes service patterns.
    All services should inherit from this class and follow its conventions.

    Attributes:
        logger: Logger instance for this service class
    """

    def __init__(self):
        """
        Initialize base service.

        Sets up logging with the service class name for better log organization.
        """
        self.logger = get_logger(self.__class__.__name__)

    def _log_operation(self, operation: str, **context) -> None:
        """
        Log an operation with context.

        Args:
            operation: Description of the operation
            **context: Additional context to log
        """
        self.logger.info(f"{operation}", extra=context)

    def _log_error(
        self, error: str, exception: Optional[Exception] = None, **context
    ) -> None:
        """
        Log an error with context.

        Args:
            error: Error description
            exception: Optional exception object
            **context: Additional context to log
        """
        if exception:
            self.logger.error(
                f"{error}: {str(exception)}", extra=context, exc_info=True
            )
        else:
            self.logger.error(error, extra=context)


class ServiceValidationError(Exception):
    """Raised when service-level validation fails."""

    pass


class ServiceOperationError(Exception):
    """Raised when a service operation fails."""

    pass
