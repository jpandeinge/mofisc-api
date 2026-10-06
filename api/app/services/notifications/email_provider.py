# api/app/services/notifications/email_provider.py

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from ...config.settings import settings
from ...utils.logging_config import get_logger
from ...utils.retry import email_circuit_breaker, retry_with_backoff
from ...utils.validation import sanitize_html, validate_email_address
from .base import NotificationProvider

logger = get_logger(__name__)


class EmailProvider(NotificationProvider):
    """
    Production-grade email notification provider using SendGrid.

    Features:
    - Retry logic with exponential backoff
    - Circuit breaker pattern
    - Structured logging
    - Input validation and sanitization
    - Connection pooling
    - Rate limiting support
    """

    def __init__(self):
        self.sendgrid_api_key = settings.SENDGRID_API_KEY
        self.from_email = settings.FROM_EMAIL
        self.from_name = settings.FROM_NAME
        self.timeout = settings.SENDGRID_TIMEOUT
        self.sendgrid_url = "https://api.sendgrid.com/v3/mail/send"
        self._sendgrid_disabled_until: Optional[datetime] = None

        logger.info(
            "EmailProvider initialized",
            extra={"from_email": self.from_email, "timeout": self.timeout},
        )

    def is_configured(self) -> bool:
        """Check if email provider is properly configured."""
        if self._sendgrid_disabled_until:
            if datetime.utcnow() < self._sendgrid_disabled_until:
                logger.warning(
                    "Email provider temporarily disabled due to auth errors",
                    extra={"disabled_until": self._sendgrid_disabled_until.isoformat()},
                )
                return False
            self._sendgrid_disabled_until = None

        is_configured = bool(self.sendgrid_api_key and self.from_email)

        if not is_configured:
            logger.warning(
                "Email provider not configured",
                extra={
                    "has_api_key": bool(self.sendgrid_api_key),
                    "has_from_email": bool(self.from_email),
                },
            )

        return is_configured

    @retry_with_backoff(
        max_retries=settings.SENDGRID_MAX_RETRIES,
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
        Send email via SendGrid with retry logic.

        Args:
            recipient: Email address
            subject: Email subject
            content: HTML email content
            data: Additional data

        Returns:
            bool: True if sent successfully
        """
        if not self.is_configured():
            logger.error("Email provider not configured")
            return False

        try:
            # Validate and sanitize recipient
            recipient = validate_email_address(recipient)
            subject = sanitize_html(subject)

            logger.info(
                "Sending email",
                extra={
                    "recipient": recipient,
                    "subject": subject[:50],  # Log first 50 chars only
                },
            )

            # Use SendGrid API through circuit breaker
            if self.sendgrid_api_key:
                return await email_circuit_breaker.call_async(
                    self._send_via_sendgrid, recipient, subject, content
                )

            logger.warning("No email provider configured, email not sent")
            return False

        except Exception as e:
            logger.error(
                "Failed to send email",
                extra={
                    "recipient": recipient,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            return False

    async def _send_via_sendgrid(
        self, recipient: str, subject: str, content: str
    ) -> bool:
        """
        Send email via SendGrid with production error handling.

        Raises exceptions on failure to allow retry decorator to work.
        """
        headers = {
            "Authorization": f"Bearer {self.sendgrid_api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "personalizations": [{"to": [{"email": recipient}], "subject": subject}],
            "from": {"email": self.from_email, "name": self.from_name},
            "content": [{"type": "text/html", "value": content}],
        }

        # Use connection pooling with limits
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)

        async with httpx.AsyncClient(limits=limits) as client:
            response = await client.post(
                self.sendgrid_url, json=payload, headers=headers, timeout=self.timeout
            )

            if response.status_code in [200, 202]:
                logger.info(
                    "Email sent successfully via SendGrid",
                    extra={"recipient": recipient, "status_code": response.status_code},
                )
                return True
            if response.status_code in [401, 403]:
                self._sendgrid_disabled_until = datetime.utcnow() + timedelta(hours=1)
                logger.error(
                    "SendGrid authentication failed - disabling email provider temporarily",
                    extra={
                        "status_code": response.status_code,
                        "disabled_until": self._sendgrid_disabled_until.isoformat(),
                    },
                )
                raise ValueError("SendGrid authentication failed")
            else:
                logger.error(
                    "SendGrid API error",
                    extra={
                        "recipient": recipient,
                        "status_code": response.status_code,
                        "response": response.text,
                    },
                )
                # Raise exception to trigger retry
                raise httpx.HTTPStatusError(
                    f"SendGrid returned {response.status_code}",
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
        Send emails to multiple recipients.

        Args:
            recipients: List of email addresses
            subject: Email subject
            content: HTML email content
            data: Additional data

        Returns:
            Dict mapping email to success status
        """
        if not self.is_configured():
            return {recipient: False for recipient in recipients}

        # SendGrid supports batch sending
        if self.sendgrid_api_key:
            try:
                headers = {
                    "Authorization": f"Bearer {self.sendgrid_api_key}",
                    "Content-Type": "application/json",
                }

                personalizations = [
                    {"to": [{"email": recipient}], "subject": subject}
                    for recipient in recipients
                ]

                payload = {
                    "personalizations": personalizations,
                    "from": {"email": self.from_email, "name": self.from_name},
                    "content": [{"type": "text/html", "value": content}],
                }

                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self.sendgrid_url, json=payload, headers=headers, timeout=30.0
                    )

                    if response.status_code in [401, 403]:
                        self._sendgrid_disabled_until = datetime.utcnow() + timedelta(
                            hours=1
                        )
                        logger.error(
                            "SendGrid authentication failed in batch - disabling email provider temporarily",
                            extra={
                                "status_code": response.status_code,
                                "disabled_until": self._sendgrid_disabled_until.isoformat(),
                            },
                        )
                        return {recipient: False for recipient in recipients}

                    success = response.status_code in [200, 202]
                    return {recipient: success for recipient in recipients}

            except Exception as e:
                print(f"Batch email error: {e}")
                return {recipient: False for recipient in recipients}

        # Fallback to sequential sending
        results = {}
        for recipient in recipients:
            results[recipient] = await self.send(recipient, subject, content, data)

        return results

    async def send_with_template(
        self, recipient: str, template_id: str, template_data: Dict[str, Any]
    ) -> bool:
        """
        Send email using template.

        Args:
            recipient: Email address
            template_id: Template ID
            template_data: Data to populate template

        Returns:
            bool: True if sent successfully
        """
        if not self.is_configured():
            return False

        try:
            if self.sendgrid_api_key:
                headers = {
                    "Authorization": f"Bearer {self.sendgrid_api_key}",
                    "Content-Type": "application/json",
                }

                payload = {
                    "personalizations": [
                        {
                            "to": [{"email": recipient}],
                            "dynamic_template_data": template_data,
                        }
                    ],
                    "from": {"email": self.from_email, "name": self.from_name},
                    "template_id": template_id,
                }

                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self.sendgrid_url, json=payload, headers=headers, timeout=30.0
                    )

                    return response.status_code in [200, 202]

        except Exception as e:
            print(f"Template email error: {e}")
            return False

        return False
