import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from api.app.utils.local_transaction_model import _build_input_text


REPO_ROOT = Path(__file__).resolve().parents[2]
HANDLER_PATH = REPO_ROOT / "deploy/huggingface/pfc_v4/handler.py"
PACKAGER_PATH = REPO_ROOT / "scripts/prepare_hf_model_repo.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


handler_module = load_module("galahad_hf_handler", HANDLER_PATH)
packager_module = load_module("galahad_hf_packager", PACKAGER_PATH)


class DummyEncoder:
    def encode(self, texts, **_kwargs):
        return np.asarray(
            [[1.0, 0.0] if "shoprite" in text else [0.0, 1.0] for text in texts],
            dtype=np.float32,
        )


class DummyCategoryHead:
    classes_ = np.asarray(["FOOD_AND_DRINK", "TRAVEL"])

    def predict_proba(self, embeddings):
        return np.asarray(
            [[0.92, 0.08] if row[0] > row[1] else [0.15, 0.85] for row in embeddings],
            dtype=np.float32,
        )


def make_handler():
    handler = handler_module.EndpointHandler.__new__(handler_module.EndpointHandler)
    handler._np = np
    handler.encoder = DummyEncoder()
    handler.category_head = DummyCategoryHead()
    handler.merchant_centroids = np.asarray(
        [[1.0, 0.0], [0.0, 1.0]],
        dtype=np.float32,
    )
    handler.merchant_names = ["Shoprite", "Airline"]
    handler.merchant_threshold = 0.65
    handler.merchant_margin = 0.20
    handler.veto_confidence = 0.95
    handler.auto_accept_threshold = 0.50
    handler.app_category_map = {
        "FOOD_AND_DRINK": "Eating Out",
        "TRAVEL": "Travel",
    }
    handler.taxonomy = "test-taxonomy"
    handler.model_version = "test-version"
    handler.max_batch_size = 2
    return handler


def test_hf_handler_uses_identical_training_serialization():
    transaction = {
        "transaction_description": "POS PURCHASE SHOPRITE",
        "transaction_type": "debit",
        "transaction_amount": 468.50,
    }

    assert handler_module.build_input_text(transaction) == _build_input_text(
        transaction
    )


def test_hf_handler_returns_full_production_contract():
    predictions = make_handler()(
        {
            "inputs": [
                {
                    "transaction_description": "SHOPRITE",
                    "transaction_type": "debit",
                    "transaction_amount": 100,
                }
            ]
        }
    )

    assert predictions == [
        {
            "label": "Eating Out",
            "score": pytest.approx(0.92),
            "category": "Eating Out",
            "model_category": "FOOD_AND_DRINK",
            "confidence": pytest.approx(0.92),
            "merchant_name": "Shoprite",
            "merchant_similarity": pytest.approx(1.0),
            "merchant_unknown": False,
            "vetoed": False,
            "low_confidence": False,
            "taxonomy": "test-taxonomy",
            "model_version": "test-version",
        }
    ]


def test_hf_handler_rejects_unbounded_or_legacy_text_inputs():
    handler = make_handler()

    with pytest.raises(ValueError, match="transaction object"):
        handler({"inputs": ["legacy [SEP] debit"]})

    with pytest.raises(ValueError, match="maximum is 2"):
        handler({"inputs": [{}, {}, {}]})


def test_packager_copies_only_the_explicit_allowlist(tmp_path):
    model_dir = tmp_path / "source"
    deployment_dir = tmp_path / "deployment"
    output_dir = tmp_path / "bundle"

    for relative_path in packager_module.MODEL_FILES:
        path = model_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative_path.encode())
    for relative_path in packager_module.DEPLOYMENT_FILES:
        path = deployment_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative_path.encode())

    model_manifest = {
        "model_version": "test-version",
        "internal_taxonomy_version": "test-taxonomy",
    }
    for manifest_key, relative_path in packager_module.RECORDED_CHECKSUMS.items():
        model_manifest[manifest_key] = packager_module.sha256(model_dir / relative_path)
    (model_dir / "model_manifest.json").write_text(json.dumps(model_manifest))
    (model_dir / "raw-transactions.csv").write_text("must,not,upload\n")
    (model_dir / ".env").write_text("HF_TOKEN=must-not-upload\n")

    manifest = packager_module.build_bundle(
        model_dir,
        deployment_dir,
        output_dir,
    )

    uploaded_paths = {item["path"] for item in manifest["files"]}
    assert "raw-transactions.csv" not in uploaded_paths
    assert ".env" not in uploaded_paths
    assert "handler.py" in uploaded_paths
    assert "encoder/model.safetensors" in uploaded_paths
    assert (output_dir / "HF_UPLOAD_MANIFEST.json").is_file()


def test_packager_refuses_to_mix_with_an_existing_bundle(tmp_path):
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    (output_dir / "stale-model.bin").write_bytes(b"stale")

    with pytest.raises(ValueError, match="must not contain files"):
        packager_module.ensure_empty_output(output_dir)
