import base64
import json
from types import SimpleNamespace

import pytest

from api.app.utils import supabase_utils


def _unsigned_legacy_key(role: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode()
    return f"header.{payload.rstrip('=')}.signature"


def test_accepts_current_secret_key(monkeypatch):
    monkeypatch.setattr(supabase_utils.settings, "SUPABASE_KEY", "sb_publishable_public")

    assert supabase_utils.service_role_key_is_valid("sb_secret_server")


def test_accepts_legacy_service_role_key(monkeypatch):
    monkeypatch.setattr(supabase_utils.settings, "SUPABASE_KEY", _unsigned_legacy_key("anon"))

    assert supabase_utils.service_role_key_is_valid(
        _unsigned_legacy_key("service_role")
    )


@pytest.mark.parametrize(
    "candidate",
    ["sb_publishable_public", _unsigned_legacy_key("anon"), "not-a-key", ""],
)
def test_rejects_public_or_unknown_keys(monkeypatch, candidate):
    monkeypatch.setattr(supabase_utils.settings, "SUPABASE_KEY", "different-public-key")

    assert not supabase_utils.service_role_key_is_valid(candidate)


def test_rejects_key_reused_from_public_slot(monkeypatch):
    key = "sb_secret_accidentally_reused"
    monkeypatch.setattr(supabase_utils.settings, "SUPABASE_KEY", key)

    assert not supabase_utils.service_role_key_is_valid(key)


def test_admin_client_fails_closed_for_anon_key(monkeypatch):
    anon_key = _unsigned_legacy_key("anon")
    monkeypatch.setattr(supabase_utils.settings, "SUPABASE_KEY", anon_key)
    monkeypatch.setattr(supabase_utils.settings, "SUPABASE_SERVICE_ROLE_KEY", anon_key)

    with pytest.raises(ValueError, match="role=service_role"):
        supabase_utils.get_supabase_client(use_service_role=True)


def test_insert_transaction_keeps_classification_fields(monkeypatch):
    captured = {}

    class FakeTable:
        def insert(self, payload):
            captured["payload"] = payload
            return self

        def execute(self):
            return SimpleNamespace(data=[captured["payload"]])

    class FakeClient:
        def table(self, name):
            captured["table"] = name
            return FakeTable()

    monkeypatch.setattr(
        supabase_utils,
        "get_supabase_client",
        lambda use_service_role=False: FakeClient(),
    )

    supabase_utils.insert_transaction(
        {
            "id": "tx-1",
            "user_id": "user-1",
            "statement_id": "statement-1",
            "transaction_date": "2026-07-24",
            "transaction_description": "POS Purchase Hungry Lion",
            "transaction_amount": 100,
            "transaction_type": "debit",
            "balance": 900,
            "category": "Fast Food",
            "confidence": 0.95,
            "merchant_name": "Hungry Lion",
            "original_merchant_name": "Hungry Lion",
            "not_a_database_column": "drop me",
        }
    )

    assert captured["payload"]["category"] == "Fast Food"
    assert captured["payload"]["confidence"] == 0.95
    assert captured["payload"]["merchant_name"] == "Hungry Lion"
    assert captured["payload"]["original_merchant_name"] == "Hungry Lion"
    assert "not_a_database_column" not in captured["payload"]
