# api/app/config/settings.py
"""
Production-grade configuration management for enterprise deployment.
Supports environment-specific settings, secrets management, and validation.
"""

import base64
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# Production mounts the dotenv-formatted Secret Manager value outside /app so
# that credentials never become part of the container image. Local development
# retains the conventional repository-root .env default.
ENV_PATH = Path(os.getenv("GALAHAD_ENV_FILE", str(PROJECT_ROOT / ".env"))).expanduser()
load_dotenv(dotenv_path=ENV_PATH)


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # Application
    APP_NAME: str = "Mofisc API"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(default="development", env="ENVIRONMENT")
    DEBUG: bool = Field(default=False, env="DEBUG")
    PORT: int = Field(default=8000, env="PORT")

    # Security
    SECRET_KEY: str = Field(..., env="SECRET_KEY")
    JWT_SECRET_KEY: str = Field(..., env="JWT_SECRET_KEY")
    API_KEY_SECRET: Optional[str] = Field(None, env="API_KEY_SECRET")
    ALGORITHM: str = Field(default="HS256", env="ALGORITHM")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=30, env="ACCESS_TOKEN_EXPIRE_MINUTES"
    )

    # Database
    DATABASE_URL: str = Field(..., env="DATABASE_URL")
    DB_POOL_SIZE: int = Field(default=10, env="DB_POOL_SIZE")
    DB_MAX_OVERFLOW: int = Field(default=20, env="DB_MAX_OVERFLOW")
    DB_POOL_TIMEOUT: int = Field(default=30, env="DB_POOL_TIMEOUT")
    DB_POOL_RECYCLE: int = Field(default=300, ge=1)
    DB_CONNECT_TIMEOUT: int = Field(default=5, ge=1, le=60)

    # Supabase
    SUPABASE_URL: str = Field(..., env="SUPABASE_URL")
    SUPABASE_KEY: str = Field(..., env="SUPABASE_KEY")
    SUPABASE_SERVICE_ROLE_KEY: str = Field(..., env="SUPABASE_SERVICE_ROLE_KEY")
    SUPABASE_TABLE_NAME: str = Field(default="transactions", env="SUPABASE_TABLE_NAME")
    SUPABASE_CONNECT_TIMEOUT: float = Field(default=3, gt=0, le=60)
    SUPABASE_READ_TIMEOUT: float = Field(default=5, gt=0, le=120)

    # Firebase Cloud Messaging (optional background/browser push)
    FIREBASE_CREDENTIALS_BASE64: Optional[str] = Field(
        default=None, env="FIREBASE_CREDENTIALS_BASE64"
    )
    FIREBASE_WEB_API_KEY: Optional[str] = Field(
        default=None, env="FIREBASE_WEB_API_KEY"
    )

    # SendGrid (Email) - Optional, notifications work without it
    SENDGRID_API_KEY: Optional[str] = Field(default=None, env="SENDGRID_API_KEY")
    FROM_EMAIL: Optional[str] = Field(default=None, env="FROM_EMAIL")
    FROM_NAME: str = Field(default="Mofisc", env="FROM_NAME")
    SENDGRID_TIMEOUT: int = Field(default=30, env="SENDGRID_TIMEOUT")
    SENDGRID_MAX_RETRIES: int = Field(default=3, env="SENDGRID_MAX_RETRIES")

    # SMS (Firebase Cloud Function + Twilio)
    FIREBASE_SMS_CLOUD_FUNCTION_URL: Optional[str] = Field(
        None, env="FIREBASE_SMS_CLOUD_FUNCTION_URL"
    )
    SMS_API_SECRET: Optional[str] = Field(default=None, min_length=32, repr=False)
    TWILIO_ACCOUNT_SID: Optional[str] = Field(None, env="TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: Optional[str] = Field(None, env="TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER: Optional[str] = Field(None, env="TWILIO_PHONE_NUMBER")
    SMS_TIMEOUT: int = Field(default=30, env="SMS_TIMEOUT")
    SMS_MAX_RETRIES: int = Field(default=3, env="SMS_MAX_RETRIES")

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = Field(default=True, env="RATE_LIMIT_ENABLED")
    RATE_LIMIT_PER_MINUTE: int = Field(default=60, env="RATE_LIMIT_PER_MINUTE")
    RATE_LIMIT_PER_HOUR: int = Field(default=1000, env="RATE_LIMIT_PER_HOUR")
    RATE_LIMIT_NOTIFICATIONS_PER_HOUR: int = Field(
        default=100, env="RATE_LIMIT_NOTIFICATIONS_PER_HOUR"
    )

    # Circuit Breaker
    CIRCUIT_BREAKER_ENABLED: bool = Field(default=True, env="CIRCUIT_BREAKER_ENABLED")
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(
        default=5, env="CIRCUIT_BREAKER_FAILURE_THRESHOLD"
    )
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT: int = Field(
        default=60, env="CIRCUIT_BREAKER_RECOVERY_TIMEOUT"
    )

    # Logging
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    LOG_FORMAT: str = Field(default="json", env="LOG_FORMAT")  # json or text

    # Monitoring
    SENTRY_DSN: Optional[str] = Field(None, env="SENTRY_DSN")
    SENTRY_ENVIRONMENT: Optional[str] = Field(None, env="SENTRY_ENVIRONMENT")
    SENTRY_TRACES_SAMPLE_RATE: float = Field(
        default=0.1, env="SENTRY_TRACES_SAMPLE_RATE"
    )
    SENTRY_PROFILES_SAMPLE_RATE: float = Field(
        default=0.1, env="SENTRY_PROFILES_SAMPLE_RATE"
    )
    METRICS_TOKEN: Optional[str] = Field(None, env="METRICS_TOKEN")
    PUBLIC_API_TOKEN: Optional[str] = Field(None, env="PUBLIC_API_TOKEN")

    # Redis (for caching and rate limiting)
    REDIS_HOST: str = Field(default="localhost", env="REDIS_HOST")
    REDIS_PORT: int = Field(default=6379, env="REDIS_PORT")
    REDIS_DB: int = Field(default=0, env="REDIS_DB")
    REDIS_PASSWORD: Optional[str] = Field(default=None, env="REDIS_PASSWORD")
    REDIS_MAX_CONNECTIONS: int = Field(default=50, env="REDIS_MAX_CONNECTIONS")
    REDIS_SOCKET_TIMEOUT: int = Field(default=5, env="REDIS_SOCKET_TIMEOUT")

    # CORS
    CORS_ORIGINS: List[str] = Field(
        default=[
            "http://localhost:3000",
            "https://mofisc.com",
            "https://www.mofisc.com",
            "https://app.mofisc.com",
        ],
        env="CORS_ORIGINS",
    )
    ALLOWED_ORIGINS: List[str] = Field(
        default=[
            "http://localhost:3000",
            "https://mofisc.com",
            "https://www.mofisc.com",
            "https://app.mofisc.com",
        ],
        env="ALLOWED_ORIGINS",
    )
    ALLOWED_HOSTS: List[str] = Field(default=["*"], env="ALLOWED_HOSTS")
    CORS_ALLOW_CREDENTIALS: bool = Field(default=True, env="CORS_ALLOW_CREDENTIALS")

    # Notification Settings
    NOTIFICATION_BATCH_SIZE: int = Field(default=100, env="NOTIFICATION_BATCH_SIZE")
    NOTIFICATION_RETRY_DELAY: int = Field(default=1, env="NOTIFICATION_RETRY_DELAY")
    NOTIFICATION_MAX_PAYLOAD_SIZE_KB: int = Field(
        default=4, env="NOTIFICATION_MAX_PAYLOAD_SIZE_KB"
    )

    # Health Check
    HEALTH_CHECK_TIMEOUT: int = Field(default=5, env="HEALTH_CHECK_TIMEOUT")

    # Uploads
    MAX_STATEMENT_UPLOAD_MB: int = Field(default=25, env="MAX_STATEMENT_UPLOAD_MB")

    # Dodo Payments Processing
    DODO_PAYMENTS_API_KEY: str = Field(default="", env="DODO_PAYMENTS_API_KEY")
    DODO_PAYMENTS_WEBHOOK_SECRET: str = Field(
        default="", env="DODO_PAYMENTS_WEBHOOK_SECRET"
    )
    DODO_PAYMENTS_BASE_URL: str = Field(
        default="https://test.dodopayments.com", env="DODO_PAYMENTS_BASE_URL"
    )
    DODO_PAYMENTS_SUCCESS_URL: str = Field(
        default="http://localhost:3000/payment/success", env="DODO_PAYMENTS_SUCCESS_URL"
    )
    DODO_PAYMENTS_CANCEL_URL: str = Field(
        default="http://localhost:3000/pricing", env="DODO_PAYMENTS_CANCEL_URL"
    )
    DODO_PAYMENTS_TRIAL_DAYS: int = Field(
        default=7, env="DODO_PAYMENTS_TRIAL_DAYS"
    )  # Set to 0 to disable trials for testing

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v):
        """Validate environment value."""
        allowed = ["development", "staging", "production", "test"]
        if v not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {allowed}")
        return v

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v):
        """Validate log level."""
        allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return v

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS origins from string or list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v):
        """Parse allowed origins from string or list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    @field_validator("ALLOWED_HOSTS", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, v):
        """Parse allowed hosts from string or list."""
        if isinstance(v, str):
            return [host.strip() for host in v.split(",")]
        return v

    @property
    def FIREBASE_CREDENTIALS_JSON(self) -> Optional[dict]:
        if not self.FIREBASE_CREDENTIALS_BASE64:
            return None
        decoded_bytes = base64.b64decode(self.FIREBASE_CREDENTIALS_BASE64)
        return json.loads(decoded_bytes.decode("utf-8"))

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.ENVIRONMENT == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development."""
        return self.ENVIRONMENT == "development"

    @property
    def database_pool_config(self) -> dict:
        """Get database pool configuration."""
        return {
            "pool_size": self.DB_POOL_SIZE,
            "max_overflow": self.DB_MAX_OVERFLOW,
            "pool_timeout": self.DB_POOL_TIMEOUT,
            "pool_recycle": self.DB_POOL_RECYCLE,
        }

    model_config = {"case_sensitive": True, "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses LRU cache to avoid re-reading environment variables.

    Returns:
        Settings instance
    """
    return Settings()


# Global settings instance
settings = get_settings()
