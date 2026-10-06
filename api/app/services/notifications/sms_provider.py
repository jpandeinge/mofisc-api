# api/app/services/notifications/sms_provider.py
"""
Production-grade SMS notification provider using Firebase Cloud Functions + Twilio.
"""

from typing import Any, Dict, List, Optional

import httpx

from ...config.settings import settings
from ...utils.logging_config import get_logger
from ...utils.retry import retry_with_backoff, sms_circuit_breaker
from ...utils.validation import sanitize_html, validate_phone_number
from .base import NotificationProvider

logger = get_logger(__name__)


class SMSProvider(NotificationProvider):
    """
    Production-grade SMS notification provider using Firebase Cloud Functions with Twilio.

    Features:
    - Retry logic with exponential backoff
    - Circuit breaker pattern
    - Structured logging
    - Phone number validation (E.164 format)
    - Connection pooling
    - Rate limiting support
    """

    def __init__(self):
        self.cloud_function_url = settings.FIREBASE_SMS_CLOUD_FUNCTION_URL
        self.twilio_account_sid = settings.TWILIO_ACCOUNT_SID
        self.twilio_auth_token = settings.TWILIO_AUTH_TOKEN
        self.twilio_from_number = settings.TWILIO_PHONE_NUMBER
        self.sms_api_secret = settings.SMS_API_SECRET
        self.timeout = settings.SMS_TIMEOUT
        self.twilio_api_url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_account_sid}/Messages.json"

        logger.info(
            "SMSProvider initialized",
            extra={
                "cloud_function_configured": bool(self.cloud_function_url),
                "twilio_direct_configured": bool(self.twilio_account_sid),
                "timeout": self.timeout,
            },
        )

    def is_configured(self) -> bool:
        """Check if SMS provider is properly configured."""
        is_configured = bool(self.cloud_function_url and self.sms_api_secret) or (
            self.twilio_account_sid
            and self.twilio_auth_token
            and self.twilio_from_number
        )

        if not is_configured:
            logger.warning("SMS provider not configured")

        return bool(is_configured)

    @retry_with_backoff(
        max_retries=settings.SMS_MAX_RETRIES,
        initial_delay=settings.NOTIFICATION_RETRY_DELAY,
        exceptions=(httpx.HTTPError, httpx.TimeoutException),
    )
    async def send(
        self,
        recipient: str,
        subject: str,
        content: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Send SMS via Firebase Cloud Function or Twilio with retry logic.

        Args:
            recipient: Phone number in E.164 format (e.g., +27821234567)
            subject: Not used for SMS (included for interface compliance)
            content: SMS message content (max 1600 chars for concatenated messages)
            data: Additional data

        Returns:
            bool: True if sent successfully
        """
        if not self.is_configured():
            logger.error("SMS provider not configured")
            return False

        try:
            # Validate and format phone number to E.164
            recipient = validate_phone_number(recipient)

            # Sanitize content
            content = sanitize_html(content)

            # Truncate if too long (SMS limit)
            if len(content) > 1600:
                logger.warning(
                    "SMS content truncated",
                    extra={"original_length": len(content), "truncated_to": 1600},
                )
                content = content[:1597] + "..."

            logger.info(
                "Sending SMS",
                extra={
                    "recipient": recipient[:5] + "****",  # Partial phone for privacy
                    "content_length": len(content),
                },
            )

            # Try Cloud Function first, fallback to direct Twilio
            if self.cloud_function_url and self.sms_api_secret:
                return await sms_circuit_breaker.call_async(
                    self._send_via_cloud_function, recipient, content, data
                )
            elif self.twilio_account_sid:
                return await sms_circuit_breaker.call_async(
                    self._send_via_twilio, recipient, content
                )

            logger.error("No SMS sending method configured")
            return False

        except Exception as e:
            logger.error(
                "Failed to send SMS",
                extra={
                    "recipient": recipient[:5] + "****" if recipient else None,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            return False

    async def _send_via_cloud_function(
        self, recipient: str, content: str, data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send SMS via Firebase Cloud Function.

        Raises exceptions on failure to allow retry decorator to work.
        """
        payload = {"to": recipient, "body": content, "data": data or {}}

        headers = {"Content-Type": "application/json"}

        if not self.sms_api_secret:
            raise ValueError("SMS_API_SECRET is required for Cloud Function calls")
        headers["Authorization"] = f"Bearer {self.sms_api_secret}"

        # Use connection pooling
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=50)

        async with httpx.AsyncClient(limits=limits) as client:
            response = await client.post(
                self.cloud_function_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )

            if response.status_code in [200, 201]:
                logger.info(
                    "SMS sent successfully via Cloud Function",
                    extra={
                        "recipient": recipient[:5] + "****",
                        "status_code": response.status_code,
                    },
                )
                return True
            else:
                logger.error(
                    "Cloud Function SMS error",
                    extra={
                        "recipient": recipient[:5] + "****",
                        "status_code": response.status_code,
                        "response": response.text,
                    },
                )
                raise httpx.HTTPStatusError(
                    f"Cloud Function returned {response.status_code}",
                    request=response.request,
                    response=response,
                )

    async def _send_via_twilio(self, recipient: str, content: str) -> bool:
        """
        Send SMS directly via Twilio API with production error handling.

        Raises exceptions on failure to allow retry decorator to work.
        """
        # Twilio uses Basic Auth
        auth = httpx.BasicAuth(self.twilio_account_sid, self.twilio_auth_token)

        payload = {"To": recipient, "From": self.twilio_from_number, "Body": content}

        limits = httpx.Limits(max_keepalive_connections=10, max_connections=50)

        async with httpx.AsyncClient(limits=limits) as client:
            response = await client.post(
                self.twilio_api_url, data=payload, auth=auth, timeout=self.timeout
            )

            if response.status_code in [200, 201]:
                logger.info(
                    "SMS sent successfully via Twilio",
                    extra={
                        "recipient": recipient[:5] + "****",
                        "status_code": response.status_code,
                    },
                )
                return True
            else:
                logger.error(
                    "Twilio SMS error",
                    extra={
                        "recipient": recipient[:5] + "****",
                        "status_code": response.status_code,
                        "response": response.text,
                    },
                )
                raise httpx.HTTPStatusError(
                    f"Twilio returned {response.status_code}",
                    request=response.request,
                    response=response,
                )

    async def send_batch(
        self,
        recipients: List[str],
        subject: str,
        content: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, bool]:
        """
        Send SMS to multiple recipients with rate limiting awareness.

        Args:
            recipients: List of phone numbers
            subject: Not used for SMS
            content: SMS message content
            data: Additional data

        Returns:
            Dict mapping phone number to success status
        """
        if not self.is_configured():
            logger.error("SMS provider not configured for batch sending")
            return {recipient: False for recipient in recipients}

        logger.info("Sending batch SMS", extra={"recipient_count": len(recipients)})

        # SMS doesn't support batch sending, send sequentially
        results = {}

        for recipient in recipients:
            results[recipient] = await self.send(recipient, subject, content, data)

        success_count = sum(1 for success in results.values() if success)
        logger.info(
            "Batch SMS complete",
            extra={
                "total": len(recipients),
                "successful": success_count,
                "failed": len(recipients) - success_count,
            },
        )

        return results

    async def send_with_verification_code(
        self,
        recipient: str,
        code: str,
        template: str = "Your verification code is: {code}",
    ) -> bool:
        """
        Send SMS verification code (useful with Firebase Auth).

        Args:
            recipient: Phone number
            code: Verification code
            template: Message template with {code} placeholder

        Returns:
            bool: True if sent successfully
        """
        message = template.format(code=code)
        return await self.send(recipient, "", message)
