# api/app/utils/validation.py
"""
Production-grade input validation and sanitization.
Prevents SQL injection, XSS, and other security vulnerabilities.
"""

import html
import re
from typing import Optional


try:
    import phonenumbers

    PHONENUMBERS_AVAILABLE = True
except ImportError:
    PHONENUMBERS_AVAILABLE = False

try:
    from email_validator import validate_email

    EMAIL_VALIDATOR_AVAILABLE = True
except ImportError:
    EMAIL_VALIDATOR_AVAILABLE = False


class ValidationError(Exception):
    """Custom validation error."""

    pass


def sanitize_html(text: str) -> str:
    """
    Sanitize HTML content to prevent XSS attacks.

    Args:
        text: Text to sanitize

    Returns:
        Sanitized text with HTML entities escaped
    """
    if not text:
        return text

    return html.escape(text)


def validate_phone_number(phone: str, region: str = "ZA") -> str:
    """
    Validate and format phone number to E.164 format.

    Args:
        phone: Phone number to validate
        region: Country code (default: ZA for South Africa)

    Returns:
        Formatted phone number in E.164 format

    Raises:
        ValidationError: If phone number is invalid
    """
    if not PHONENUMBERS_AVAILABLE:
        return phone

    try:
        parsed = phonenumbers.parse(phone, region)

        if not phonenumbers.is_valid_number(parsed):
            raise ValidationError(f"Invalid phone number: {phone}")

        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)

    except Exception as e:
        raise ValidationError(f"Failed to parse phone number: {str(e)}")


def validate_email_address(email: str) -> str:
    """
    Validate email address.

    Args:
        email: Email address to validate

    Returns:
        Normalized email address

    Raises:
        ValidationError: If email is invalid
    """
    if not EMAIL_VALIDATOR_AVAILABLE:
        return email

    try:
        valid = validate_email(email, check_deliverability=False)
        return valid.normalized

    except Exception as e:
        raise ValidationError(f"Invalid email address: {str(e)}")


def validate_fcm_token(token: str) -> bool:
    """
    Validate Firebase Cloud Messaging token format.

    Args:
        token: FCM token to validate

    Returns:
        True if valid, False otherwise
    """
    if not token:
        return False

    # FCM tokens are typically 152+ characters alphanumeric with some symbols
    if len(token) < 100:
        return False

    # Check for valid characters (alphanumeric, hyphens, underscores, colons)
    if not re.match(r"^[A-Za-z0-9_:-]+$", token):
        return False

    return True


def validate_notification_data(data: dict, max_size_kb: int = 4) -> bool:
    """
    Validate notification data payload size.

    FCM has a 4KB limit for data payloads.

    Args:
        data: Data dictionary to validate
        max_size_kb: Maximum size in KB (default: 4KB for FCM)

    Returns:
        True if valid, False otherwise
    """
    import json

    if not data:
        return True

    # Calculate approximate size
    size_bytes = len(json.dumps(data).encode("utf-8"))
    size_kb = size_bytes / 1024

    return size_kb <= max_size_kb


def sanitize_sql_like_pattern(pattern: str) -> str:
    """
    Sanitize SQL LIKE pattern to prevent SQL injection.

    Args:
        pattern: Pattern to sanitize

    Returns:
        Sanitized pattern
    """
    if not pattern:
        return pattern

    # Escape special characters
    pattern = pattern.replace("\\", "\\\\")
    pattern = pattern.replace("%", "\\%")
    pattern = pattern.replace("_", "\\_")

    return pattern


def validate_uuid(uuid_string: str) -> bool:
    """
    Validate UUID format.

    Args:
        uuid_string: UUID string to validate

    Returns:
        True if valid UUID, False otherwise
    """
    uuid_pattern = re.compile(
        r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$", re.IGNORECASE
    )
    return bool(uuid_pattern.match(uuid_string))


def validate_url(url: str, allowed_schemes: Optional[list] = None) -> bool:
    """
    Validate URL format and scheme.

    Args:
        url: URL to validate
        allowed_schemes: List of allowed schemes (default: ['http', 'https'])

    Returns:
        True if valid, False otherwise
    """
    if not url:
        return False

    if allowed_schemes is None:
        allowed_schemes = ["http", "https"]

    # Basic URL pattern
    url_pattern = re.compile(
        r"^(https?):\/\/"  # http:// or https://
        r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"  # domain...
        r"localhost|"  # localhost...
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # ...or ip
        r"(?::\d+)?"  # optional port
        r"(?:/?|[/?]\S+)$",
        re.IGNORECASE,
    )

    if not url_pattern.match(url):
        return False

    # Check scheme
    scheme = url.split("://")[0].lower()
    return scheme in allowed_schemes


def rate_limit_key(identifier: str, action: str) -> str:
    """
    Generate rate limit cache key.

    Args:
        identifier: User/IP identifier
        action: Action being rate limited

    Returns:
        Cache key string
    """
    return f"rate_limit:{action}:{identifier}"
