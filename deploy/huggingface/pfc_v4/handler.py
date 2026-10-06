"""Hugging Face Inference Endpoint handler for Galahad's PFC model.

The model is a composite artifact:

* a SentenceTransformer encoder;
* a scikit-learn category head; and
* a merchant-centroid index with an open-world rejection gate.

Keeping all three parts in this handler preserves the same prediction and
abstention contract used by the API's local model implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


_AMOUNT_BUCKETS = (
    (10.0, "lt_10"),
    (100.0, "10_100"),
    (1_000.0, "100_1k"),
    (10_000.0, "1k_10k"),
    (100_000.0, "10k_100k"),
    (float("inf"), "gt_100k"),
)

_REQUIRED_CHECKSUMS = {
    "encoder_sha256": Path("encoder/model.safetensors"),
    "category_head_sha256": Path("category_head.joblib"),
    "merchant_index_sha256": Path("merchant_index.npz"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_and_verify_manifest(model_dir: Path) -> dict[str, Any]:
    manifest_path = model_dir / "model_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("model_manifest.json is required")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for checksum_key, relative_path in _REQUIRED_CHECKSUMS.items():
        expected = manifest.get(checksum_key)
        if not expected:
            raise ValueError(f"{checksum_key} is missing from model_manifest.json")
        artifact_path = model_dir / relative_path
        if not artifact_path.is_file():
            raise ValueError(f"required model artifact is missing: {relative_path}")
        actual = _sha256(artifact_path)
        if actual != expected:
            raise ValueError(
                f"checksum mismatch for {relative_path}: "
                f"expected {expected}, got {actual}"
            )
    return manifest


def build_input_text(transaction: Mapping[str, Any]) -> str:
    """Serialize one transaction exactly as the Galahad model was trained."""
    description = (
        str(
            transaction.get("transaction_description")
            or transaction.get("TransactionParticulars")
            or transaction.get("description")
            or ""
        )
        .lower()
        .strip()
    )

    raw_type = (
        str(
            transaction.get("transaction_type")
            or transaction.get("TransactionTypeDebitCredit")
            or ""
        )
        .lower()
        .strip()
    )
    transaction_type = {
        "c": "credit",
        "d": "debit",
        "credit": "credit",
        "debit": "debit",
    }.get(raw_type, "unknown")

    payment_type = (
        str(transaction.get("payment_type") or "unknown").lower().strip() or "unknown"
    )

    try:
        raw_amount = next(
            transaction[key]
            for key in ("amount", "transaction_amount", "TransactionAmount")
            if transaction.get(key) is not None
        )
        amount = abs(float(raw_amount))
        amount_bucket = next(
            name for upper_bound, name in _AMOUNT_BUCKETS if amount < upper_bound
        )
    except (StopIteration, TypeError, ValueError):
        amount_bucket = "unknown"

    return (
        f"transaction: {description} | type: {transaction_type} "
        f"| payment: {payment_type} | amount_bucket: {amount_bucket}"
    )


class EndpointHandler:
    """Load the verified composite model once and serve bounded batches."""

    def __init__(self, path: str = ""):
        import joblib
        import numpy as np
        from sentence_transformers import SentenceTransformer

        self._np = np
        self.model_dir = Path(path or ".").resolve()
        self.manifest = _load_and_verify_manifest(self.model_dir)
        self.encoder = SentenceTransformer(
            str(self.model_dir / "encoder"),
            device=os.environ.get("GALAHAD_MODEL_DEVICE", "cpu"),
        )
        # The joblib artifact is trusted because the endpoint loads it only
        # from this private, checksum-verified model repository.
        self.category_head = joblib.load(self.model_dir / "category_head.joblib")

        merchant_index = np.load(
            self.model_dir / "merchant_index.npz",
            allow_pickle=True,
        )
        self.merchant_centroids = merchant_index["centroids"].astype(np.float32)
        self.merchant_names = [str(name) for name in merchant_index["names"]]

        label_config = json.loads(
            (self.model_dir / "label_classes.json").read_text(encoding="utf-8")
        )
        self.merchant_threshold = max(
            float(label_config["merchant_threshold"]),
            float(os.environ.get("LOCAL_MODEL_MERCHANT_THRESHOLD", "0.65")),
        )
        self.merchant_margin = max(
            float(label_config.get("merchant_margin", 0.0)),
            float(os.environ.get("LOCAL_MODEL_MERCHANT_MARGIN", "0.20")),
        )
        self.veto_confidence = float(
            os.environ.get("LOCAL_MODEL_VETO_CONFIDENCE", "0.95")
        )
        self.auto_accept_threshold = float(
            label_config.get("category_auto_accept_threshold", 0.5)
        )
        self.app_category_map = label_config["app_category_map"]
        self.taxonomy = label_config["taxonomy"]
        self.model_version = label_config.get(
            "model_version",
            self.manifest["model_version"],
        )
        self.max_batch_size = int(os.environ.get("GALAHAD_MODEL_MAX_BATCH_SIZE", "128"))

        unmapped = {
            str(category)
            for category in self.category_head.classes_
            if str(category) not in self.app_category_map
        }
        if unmapped:
            raise ValueError(
                f"model categories missing app mappings: {sorted(unmapped)}"
            )

    def __call__(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        inputs = data.get("inputs") if isinstance(data, dict) else None
        if isinstance(inputs, Mapping):
            transactions: Sequence[Mapping[str, Any]] = [inputs]
        elif isinstance(inputs, list) and all(
            isinstance(item, Mapping) for item in inputs
        ):
            transactions = inputs
        else:
            raise ValueError(
                "inputs must be a transaction object or a list of transaction objects"
            )

        if not transactions:
            return []
        if len(transactions) > self.max_batch_size:
            raise ValueError(
                f"batch contains {len(transactions)} transactions; "
                f"maximum is {self.max_batch_size}"
            )

        np = self._np
        texts = [build_input_text(transaction) for transaction in transactions]
        embeddings = np.asarray(
            self.encoder.encode(
                texts,
                batch_size=min(64, len(texts)),
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )

        probabilities = self.category_head.predict_proba(embeddings)
        confidences = probabilities.max(axis=1)
        model_categories = self.category_head.classes_[probabilities.argmax(axis=1)]

        similarities = embeddings @ self.merchant_centroids.T
        top_indices = similarities.argmax(axis=1)
        top_similarities = similarities[
            np.arange(len(similarities)),
            top_indices,
        ]
        if similarities.shape[1] > 1:
            partitioned = np.partition(similarities, -2, axis=1)
            margins = top_similarities - partitioned[:, -2]
        else:
            margins = np.full(len(similarities), np.inf)

        predictions: list[dict[str, Any]] = []
        for index in range(len(transactions)):
            confidence = float(confidences[index])
            merchant_unknown = bool(
                top_similarities[index] < self.merchant_threshold
                or margins[index] < self.merchant_margin
            )
            vetoed = bool(merchant_unknown and confidence < self.veto_confidence)
            low_confidence = bool(confidence < self.auto_accept_threshold)
            model_category = str(model_categories[index])
            category = (
                "Uncategorized"
                if vetoed or low_confidence
                else self.app_category_map.get(model_category, "Uncategorized")
            )

            # label/score retain compatibility with simple HF clients. The
            # remaining fields are the production contract consumed by Galahad.
            predictions.append(
                {
                    "label": category,
                    "score": 0.0 if vetoed or low_confidence else confidence,
                    "category": category,
                    "model_category": model_category,
                    "confidence": confidence,
                    "merchant_name": (
                        None
                        if merchant_unknown
                        else self.merchant_names[int(top_indices[index])]
                    ),
                    "merchant_similarity": float(top_similarities[index]),
                    "merchant_unknown": merchant_unknown,
                    "vetoed": vetoed,
                    "low_confidence": low_confidence,
                    "taxonomy": self.taxonomy,
                    "model_version": self.model_version,
                }
            )
        return predictions
