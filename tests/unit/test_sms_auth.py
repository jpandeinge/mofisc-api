import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from api.app.services.notifications import sms_provider


def make_provider(monkeypatch, secret=None):
    monkeypatch.setattr(sms_provider.settings, "FIREBASE_SMS_CLOUD_FUNCTION_URL", "https://sms.example/sendSMS")
    monkeypatch.setattr(sms_provider.settings, "SMS_API_SECRET", secret)
    for name in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER"):
        monkeypatch.setattr(sms_provider.settings, name, None)
    return sms_provider.SMSProvider()


def test_public_firebase_key_does_not_configure_sms(monkeypatch):
    monkeypatch.setattr(sms_provider.settings, "FIREBASE_WEB_API_KEY", "public-web-key")
    provider = make_provider(monkeypatch)
    assert not provider.is_configured()
    with pytest.raises(ValueError, match="SMS_API_SECRET"):
        asyncio.run(provider._send_via_cloud_function("+264811234567", "Test"))


def test_cloud_function_uses_private_secret(monkeypatch):
    secret = "test-only-server-secret-32-characters-long"
    provider = make_provider(monkeypatch, secret)
    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json={"success": True})
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(sms_provider.httpx, "AsyncClient", lambda **kwargs: context)

    assert provider.is_configured()
    assert asyncio.run(provider._send_via_cloud_function("+264811234567", "Test"))
    assert client.post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {secret}"
