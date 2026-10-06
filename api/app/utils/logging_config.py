# api/app/utils/logging_config.py

import logging
import re

from ..config.settings import settings


class SensitiveDataFilter(logging.Filter):
    """Filter to remove sensitive data from logs"""

    def __init__(self):
        super().__init__()
        self.patterns = [
            (r"bearer\s+[a-zA-Z0-9\-._~+/]+=*", "[BEARER_TOKEN]"),
            (r'apikey\s*[:=]\s*[^\'"\s]+', "apikey=[REDACTED]"),
            (r'password\s*[:=]\s*[^\'"\s]+', "password=[REDACTED]"),
            (r'secret\s*[:=]\s*[^\'"\s]+', "secret=[REDACTED]"),
            (r'token\s*[:=]\s*[^\'"\s]+', "token=[REDACTED]"),
            (r"\b[\w\.-]+@[\w\.-]+\.\w+\b", "[EMAIL]"),
            (r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b", "[CARD_NUMBER]"),
            (r"(\d+\.\d{2})", "[AMOUNT]"),
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            msg = record.msg
            for pattern, replacement in self.patterns:
                msg = re.sub(pattern, replacement, msg)
            record.msg = msg
        return True


# Additional patterns for sensitive data
additional_patterns = [
    # Paths and URLs
    (r"(?<=/)[^/]+\.py\b", "[FILE]"),  # Hide Python file names
    (r"(?<=from\s)[^\s]+(?=\simport)", "[MODULE]"),  # Hide import paths
    (r"(?<=at\s)/.*?\.py", "[PATH]"),  # Hide file paths in tracebacks
    (r"(https?://[^/]+)/.*", r"\1/[PATH]"),  # Hide URL paths
    # Version Information
    (r"version\s+\d+\.\d+\.\d+", "[VERSION]"),  # Hide version numbers
    # Framework Details
    (r"(FastAPI|Uvicorn|Starlette|SQLAlchemy).*?[\d.]+", r"\1 [VERSION]"),
    # System Information
    (r"(Linux|Windows|Darwin).*?\d+\.\d+\.\d+", "[OS]"),
    (r"Python\s+\d+\.\d+\.\d+", "Python [VERSION]"),
    # Development Paths
    (r"(venv|virtualenv|conda).*?/.*?/", "[VENV]/"),
    (r"/Users/[^/]+/", "[USER_HOME]/"),
    (r"/home/[^/]+/", "[USER_HOME]/"),
    # Database Information
    (r"(postgresql|mysql|sqlite)://[^@\s]+@", r"\1://[CREDENTIALS]@"),
    # API Information
    (r"api[_-]?key[_-]?[a-zA-Z0-9]+", "[API_KEY]"),
]


# Define additional patterns for SensitiveDataFilter
class EnhancedSensitiveDataFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        # Combine existing and new patterns
        self.patterns = [
            (re.compile(pattern), repl) for pattern, repl in additional_patterns
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        # Skip debug logs entirely in production
        if settings.ENVIRONMENT == "production" and record.levelno <= logging.DEBUG:
            return False

        if isinstance(record.msg, str):
            msg = record.msg
            # Apply all sanitization patterns
            for pattern, repl in self.patterns:
                msg = pattern.sub(repl, msg)
            record.msg = msg

            # Remove stack traces in production
            if (
                settings.ENVIRONMENT == "production"
                and hasattr(record, "exc_info")
                and record.exc_info
            ):
                record.exc_info = None
                record.exc_text = None

        return True


def configure_logging():
    """Configure logging to send all logs to Sentry only, no local logging"""

    # Critical security: Disable ALL debug logs from security-sensitive packages
    critical_loggers = {
        "sentry_sdk": logging.ERROR,
        "urllib3": logging.ERROR,
        "pythonjsonlogger": logging.ERROR,
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "asyncio": logging.WARNING,
        "hpack.hpack": logging.ERROR,
        "sqlalchemy.engine": logging.WARNING,
        "alembic": logging.WARNING,
        "boto3": logging.WARNING,
        "botocore": logging.WARNING,
        "pdfminer": logging.ERROR,
        "transformers": logging.ERROR,
        "huggingface_hub": logging.ERROR,
        "pytorch_pretrained_bert": logging.ERROR,
        "matplotlib": logging.WARNING,
        "PIL": logging.WARNING,
        "swagger_ui": logging.WARNING,
        "multipart": logging.WARNING,
        "py.warnings": logging.ERROR,
        "fsspec": logging.WARNING,
        "azure": logging.WARNING,
        "requests": logging.WARNING,
        "supabase": logging.WARNING,
        "python-dotenv": logging.ERROR,
        "uvicorn": logging.WARNING,
        "uvicorn.access": logging.WARNING,
        "uvicorn.error": logging.WARNING,
        "fastapi": logging.WARNING,
        "sqlalchemy.pool": logging.WARNING,
        "sqlalchemy.dialects": logging.WARNING,
        "email_validator": logging.WARNING,
        "stripe": logging.WARNING,
        "password_strength": logging.WARNING,
        "aiohttp": logging.WARNING,
    }

    # Configure all critical loggers first
    for logger_name, level in critical_loggers.items():
        logging.getLogger(logger_name).setLevel(level)

    # Configure root logger - all logs go to Sentry via LoggingIntegration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Clear all handlers - no local logging, everything goes to Sentry
    root_logger.handlers = []

    # In development only, add minimal console output for debugging
    if settings.ENVIRONMENT == "development":
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(levelname)s: [%(name)s] %(message)s")
        console_handler.setFormatter(formatter)
        console_handler.addFilter(EnhancedSensitiveDataFilter())
        root_logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with security controls"""
    logger = logging.getLogger(name)
    logger.addFilter(EnhancedSensitiveDataFilter())

    # Ensure child loggers don't propagate sensitive messages
    if settings.ENVIRONMENT == "production":
        logger.propagate = False

    return logger
