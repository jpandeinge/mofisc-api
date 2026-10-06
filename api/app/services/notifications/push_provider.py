# api/app/services/notifications/push_provider.py
"""
Production-grade push notification provider using Firebase Cloud Messaging (FCM).
"""

import asyncio
import json
import time
from datetime import datetime
from functools import partial
from typing import Any, Dict, List, Optional

import firebase_admin
from firebase_admin import credentials
from firebase_admin import exceptions as firebase_exceptions
from firebase_admin import initialize_app, messaging

from ...config.settings import settings
from ...metrics.notification_metrics import (
    record_notification_sent,
)
from ...utils.logging_config import get_logger
from ...utils.retry import push_circuit_breaker, retry_with_backoff
from ...utils.supabase_utils import get_supabase_client
from ...utils.validation import validate_fcm_token, validate_notification_data
from .base import NotificationProvider

logger = get_logger(__name__)


class PushProvider(NotificationProvider):
    """
    Production-grade push notification provider using Firebase Cloud Messaging (FCM).

    Features:
    - Retry logic with exponential backoff
    - Circuit breaker pattern
    - Structured logging
    - FCM token validation
    - Batch sending support
    - Topic messaging support
    - Data payload size validation
    """

    def __init__(self):
        self.is_initialized = False
        self._firebase_app: Optional[firebase_admin.App] = None

        try:
            credentials_json = settings.FIREBASE_CREDENTIALS_JSON
            if not credentials_json:
                logger.info(
                    "Firebase credentials not configured; background push is disabled"
                )
                return

            if not firebase_admin._apps:
                cred = credentials.Certificate(credentials_json)
                self._firebase_app = initialize_app(cred)
                logger.info("Firebase initialized successfully")
            else:
                self._firebase_app = firebase_admin.get_app()
                logger.info("Firebase already initialized, reusing existing app")

            self.is_initialized = True

        except Exception as e:
            logger.error(
                "Failed to initialize Firebase", extra={"error": str(e)}, exc_info=True
            )

    async def _run_in_executor(self, func, *args):
        """Run blocking Firebase Admin SDK calls in a separate thread."""
        loop = asyncio.get_running_loop()
        bound = partial(func, *args)
        return await loop.run_in_executor(None, bound)

    def is_configured(self) -> bool:
        """Check if Firebase is properly configured."""
        if not self.is_initialized:
            logger.warning("Firebase not initialized")

        return self.is_initialized

    def _deactivate_tokens(self, tokens: List[str], reason: str) -> None:
        if not tokens:
            return

        try:
            supabase = get_supabase_client(use_service_role=True)
            timestamp = datetime.utcnow().isoformat()

            for token in tokens:
                supabase.table("user_devices").update(
                    {"is_active": False, "updated_at": timestamp}
                ).eq("device_token", token).execute()

            logger.info(
                "Deactivated invalid FCM tokens",
                extra={"count": len(tokens), "reason": reason},
            )
        except Exception as e:
            logger.error(
                "Failed to deactivate invalid FCM tokens",
                extra={"count": len(tokens), "reason": reason, "error": str(e)},
                exc_info=True,
            )

    @staticmethod
    def _is_invalid_token_error(error: Exception) -> bool:
        error_types = []
        for attr in [
            "UnregisteredError",
            "InvalidArgumentError",
            "SenderIdMismatchError",
            "NotFoundError",
        ]:
            cls = getattr(messaging, attr, None) or getattr(
                firebase_exceptions, attr, None
            )
            if cls:
                error_types.append(cls)
        return isinstance(error, tuple(error_types)) if error_types else False

    @retry_with_backoff(max_retries=3, initial_delay=1.0, exceptions=(Exception,))
    async def send(
        self,
        recipient: str,
        subject: str,
        content: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send push notification via Firebase with retry logic.

        Args:
            recipient: Device FCM token
            subject: Notification title
            content: Notification body
            data: Additional data payload (must be dict with string values)

        Returns:
            bool: True if sent successfully
        """
        start_time = time.time()
        success = False
        error_type = None

        try:
            if not self.is_configured():
                logger.error("Firebase not configured")
                error_type = "NotConfigured"
                return False

            # Validate FCM token
            if not validate_fcm_token(recipient):
                logger.error(
                    "Invalid FCM token format",
                    extra={"token_length": len(recipient) if recipient else 0},
                )
                error_type = "InvalidToken"
                return False

            # Validate data payload size
            if data and not validate_notification_data(
                data, settings.NOTIFICATION_MAX_PAYLOAD_SIZE_KB
            ):
                logger.error(
                    "Data payload exceeds size limit",
                    extra={"max_size_kb": settings.NOTIFICATION_MAX_PAYLOAD_SIZE_KB},
                )
                error_type = "PayloadTooLarge"
                return False

            # Convert data values to strings (FCM requirement)
            if data:
                data = {k: str(v) for k, v in data.items()}

            logger.info(
                "Sending push notification",
                extra={"token_prefix": recipient[:20] + "...", "title": subject[:50]},
            )

            # Send through circuit breaker
            result = await push_circuit_breaker.call_async(
                self._send_fcm, recipient, subject, content, data
            )

            success = result
            return result

        except Exception as e:
            error_type = type(e).__name__
            logger.error(
                "Failed to send push notification",
                extra={
                    "token_prefix": recipient[:20] + "..." if recipient else None,
                    "error": str(e),
                    "error_type": error_type,
                },
                exc_info=True,
            )
            return False

        finally:
            # Record metrics
            duration = time.time() - start_time
            notification_type = (
                data.get("notification_type", "unknown") if data else "unknown"
            )

            # Calculate payload size
            payload_size = 0
            if data:
                payload_size = len(json.dumps(data).encode("utf-8"))

            record_notification_sent(
                channel="push",
                notification_type=notification_type,
                success=success,
                duration_seconds=duration,
                payload_size_bytes=payload_size,
                error_type=error_type,
            )

    async def _send_fcm(
        self, token: str, title: str, body: str, data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send FCM message with production error handling.

        Raises exceptions on failure to allow retry decorator to work.
        """
        try:
            # Use data-only message to prevent FCM from auto-displaying notification
            # This gives the client full control and prevents duplicate notifications
            message_data = data.copy() if data else {}
            message_data["title"] = title
            message_data["body"] = body

            message = messaging.Message(
                data=message_data,
                token=token,
            )

            response = await self._run_in_executor(messaging.send, message)

            logger.info(
                "Push notification sent successfully",
                extra={"token_prefix": token[:20] + "...", "message_id": response},
            )

            return True

        except messaging.UnregisteredError:
            # Token is invalid or app was uninstalled
            logger.warning(
                "FCM token unregistered (app uninstalled or token expired)",
                extra={"token_prefix": token[:20] + "..."},
            )
            self._deactivate_tokens([token], "unregistered")
            # Don't retry for invalid tokens
            return False

        except messaging.InvalidArgumentError:
            logger.error(
                "FCM invalid argument",
                extra={"token_prefix": token[:20] + "..."},
            )
            self._deactivate_tokens([token], "invalid_argument")
            return False

        except messaging.SenderIdMismatchError:
            # Token is for different sender
            logger.error(
                "FCM sender ID mismatch", extra={"token_prefix": token[:20] + "..."}
            )
            self._deactivate_tokens([token], "sender_id_mismatch")
            return False

        except messaging.QuotaExceededError:
            # Rate limit exceeded
            logger.error("FCM quota exceeded")
            # Raise to trigger retry
            raise

        except messaging.UnavailableError:
            # FCM service temporarily unavailable
            logger.error("FCM service unavailable")
            # Raise to trigger retry
            raise

        except Exception as e:
            logger.error(
                "FCM send error",
                extra={
                    "token_prefix": token[:20] + "...",
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            # Raise to trigger retry for transient errors
            raise

    async def send_batch(
        self,
        recipients: List[str],
        subject: str,
        content: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, bool]:
        """
        Send push notifications to multiple devices with production error handling.

        Args:
            recipients: List of FCM device tokens (max 500 per batch)
            subject: Notification title
            content: Notification body
            data: Additional data payload

        Returns:
            Dict mapping device token to success status
        """
        if not self.is_configured():
            logger.error("Firebase not configured for batch sending")
            return {recipient: False for recipient in recipients}

        # Validate all tokens
        valid_recipients = [r for r in recipients if validate_fcm_token(r)]
        invalid_format_tokens = [r for r in recipients if r not in valid_recipients]

        if len(valid_recipients) != len(recipients):
            logger.warning(
                "Some FCM tokens filtered out due to invalid format",
                extra={
                    "total": len(recipients),
                    "valid": len(valid_recipients),
                    "invalid": len(recipients) - len(valid_recipients),
                },
            )
            if invalid_format_tokens:
                self._deactivate_tokens(invalid_format_tokens, "invalid_format")

        if not valid_recipients:
            logger.error("No valid FCM tokens in batch")
            return {recipient: False for recipient in recipients}

        # FCM has a limit of 500 tokens per batch
        batch_size = min(500, settings.NOTIFICATION_BATCH_SIZE)
        results = {
            recipient: False
            for recipient in recipients
            if recipient not in valid_recipients
        }

        # Convert data values to strings
        if data:
            data = {k: str(v) for k, v in data.items()}

        # Process in batches
        for i in range(0, len(valid_recipients), batch_size):
            batch = valid_recipients[i : i + batch_size]
            invalid_tokens: List[str] = []

            logger.info(
                "Sending push notification batch",
                extra={"batch_number": i // batch_size + 1, "batch_size": len(batch)},
            )

            try:
                # Use data-only message to prevent FCM from auto-displaying notification
                # This gives the client full control and prevents duplicate notifications
                message_data = data.copy() if data else {}
                message_data["title"] = subject
                message_data["body"] = content

                message = messaging.MulticastMessage(
                    data=message_data,
                    tokens=batch,
                )

                if hasattr(messaging, "send_each_for_multicast"):
                    response = await self._run_in_executor(
                        messaging.send_each_for_multicast, message
                    )
                else:
                    response = await self._run_in_executor(
                        messaging.send_multicast, message
                    )

                # Map results back to recipients
                for j, recipient in enumerate(batch):
                    results[recipient] = response.responses[j].success

                    if not response.responses[j].success:
                        error = response.responses[j].exception
                        logger.warning(
                            "Failed to send to device",
                            extra={
                                "token_prefix": recipient[:20] + "...",
                                "error": str(error) if error else "Unknown error",
                            },
                        )
                        if error and self._is_invalid_token_error(error):
                            invalid_tokens.append(recipient)

                logger.info(
                    "Batch complete",
                    extra={
                        "success_count": response.success_count,
                        "failure_count": response.failure_count,
                    },
                )

                if invalid_tokens:
                    self._deactivate_tokens(invalid_tokens, "invalid_token")

            except Exception as e:
                logger.error(
                    f"Failed to send batch push notifications: {type(e).__name__}: {str(e)}",
                    extra={"batch_size": len(batch), "error_type": type(e).__name__},
                    exc_info=True,
                )
                # Mark all in batch as failed
                for recipient in batch:
                    results[recipient] = False

        return results

    async def send_to_topic(
        self,
        topic: str,
        subject: str,
        content: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send push notification to a topic with production error handling.

        Args:
            topic: FCM topic name
            subject: Notification title
            content: Notification body
            data: Additional data payload

        Returns:
            bool: True if sent successfully
        """
        if not self.is_configured():
            logger.error("Firebase not configured for topic messaging")
            return False

        try:
            # Convert data values to strings
            if data:
                data = {k: str(v) for k, v in data.items()}

            logger.info(
                "Sending push notification to topic",
                extra={"topic": topic, "title": subject[:50]},
            )

            # Use data-only message to prevent FCM from auto-displaying notification
            message_data = data.copy() if data else {}
            message_data["title"] = subject
            message_data["body"] = content

            message = messaging.Message(
                data=message_data,
                topic=topic,
            )

            response = messaging.send(message)

            logger.info(
                "Topic notification sent successfully",
                extra={"topic": topic, "message_id": response},
            )

            return True

        except Exception as e:
            logger.error(
                "Failed to send topic notification",
                extra={"topic": topic, "error": str(e), "error_type": type(e).__name__},
                exc_info=True,
            )
            return False
