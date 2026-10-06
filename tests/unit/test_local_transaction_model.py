import hashlib
import json

import pytest

from api.app.utils.local_transaction_model import (
    _build_input_text,
    _load_and_verify_manifest,
    local_model_required,
)


def test_zero_amount_is_not_treated_as_missing():
    text = _build_input_text(
        {
            "transaction_description": "zero value adjustment",
            "transaction_type": "credit",
            "transaction_amount": 0,
        }
    )

    assert "amount_bucket: lt_10" in text


def test_database_amount_field_matches_training_serialization():
    text = _build_input_text(
        {
            "transaction_description": "POS PURCHASE SHOPRITE",
            "transaction_type": "debit",
            "transaction_amount": 468.50,
        }
    )

    assert text == (
        "transaction: pos purchase shoprite | type: debit "
        "| payment: unknown | amount_bucket: 100_1k"
    )


def test_manifest_verifies_every_recorded_artifact(tmp_path):
    artifacts = {
        "encoder_sha256": tmp_path / "encoder" / "model.safetensors",
        "category_head_sha256": tmp_path / "category_head.joblib",
        "merchant_index_sha256": tmp_path / "merchant_index.npz",
    }
    manifest = {}
    for index, (key, path) in enumerate(artifacts.items()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"artifact-{index}".encode())
        manifest[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "model_manifest.json").write_text(json.dumps(manifest))

    assert _load_and_verify_manifest(tmp_path) == manifest

    artifacts["merchant_index_sha256"].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="merchant_index.npz"):
        _load_and_verify_manifest(tmp_path)


def test_production_requires_local_pfc_model_by_default(monkeypatch):
    monkeypatch.delenv("LOCAL_MODEL_REQUIRED", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")

    assert local_model_required() is True


def test_explicit_local_model_requirement_overrides_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LOCAL_MODEL_REQUIRED", "false")

    assert local_model_required() is False
