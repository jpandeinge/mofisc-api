"""Local category + merchant model with an open-world merchant veto.

Serves the hybrid artifacts trained by the transaction-model notebooks: a
fine-tuned sentence encoder, a logistic category head, and a merchant centroid
index with an "unknown merchant" similarity gate. The currently packaged
checkpoint uses Galahad taxonomy v4 aligned to Plaid PFCv1. It must not be
described as Plaid PFCv2 or as the PRAGMA history model.

Decision policy (measured on the notebook's generated evaluation splits):
- merchant with top-1 cosine similarity below the tuned threshold is reported
  as unknown instead of force-linked. The recorded 86.8% rejection figure is
  an offline calibration result, not evidence of production-world coverage;
- merchant-unknown veto: when the merchant is unknown AND category confidence
  is below LOCAL_MODEL_VETO_CONFIDENCE, the category is not trusted — callers
  should fall back to rules or leave the row for review. On in-distribution
  data this affects <1% of rows; on foreign-bank data it blocks the dominant
  failure mode (confidently wrong categories for opaque descriptions).

Development can degrade gracefully to the legacy HF endpoint. Production is
strict by default: ``LOCAL_MODEL_REQUIRED=1`` (or ``ENVIRONMENT=production``)
turns a missing/corrupt checkpoint into a startup failure instead of silently
serving a different classifier.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence

from .logging_config import get_logger

logger = get_logger(__name__)

_API_ROOT = Path(__file__).resolve().parents[2]  # .../api
_REPO_ROOT = _API_ROOT.parent  # .../galahad-api

# Fallback display map for v3-era artifacts whose label_classes.json lacks an
# embedded "app_category_map". Newer artifacts (PFC v4+) ship their own map, so
# the API stays taxonomy-agnostic.
V3_TO_APP_CATEGORY = {
    "income": "Income",
    "transfers": "Transfers",
    "cash": "Cash",
    "bank_fees": "Bank Fees",
    "loans_credit": "Loan",
    "insurance": "Insurance",
    "groceries": "Groceries",
    "food_drink": "Eating Out",
    "shopping": "Shopping",
    "transport_fuel": "Fuel/Retail",
    "housing_utilities": "Utilities",
    "communication": "Airtime",
    "health_beauty": "Health & Beauty",
    "education": "Education",
    "entertainment_leisure": "Entertainment",
    "savings_investments": "Savings",
    "services_business": "Services",
    "uncategorised": "Uncategorized",
}

_AMOUNT_BUCKETS = (
    (10.0, "lt_10"),
    (100.0, "10_100"),
    (1_000.0, "100_1k"),
    (10_000.0, "1k_10k"),
    (100_000.0, "10k_100k"),
    (float("inf"), "gt_100k"),
)


@dataclass(frozen=True)
class LocalPrediction:
    category: str  # app-facing category (post-veto)
    model_category: str  # raw category-head label
    confidence: float
    merchant_name: Optional[str]  # None when below the unknown threshold
    merchant_similarity: float
    merchant_unknown: bool
    vetoed: bool  # merchant unknown + confidence below veto bar
    low_confidence: bool  # confidence below the auto-accept threshold
    taxonomy: str
    model_version: str


class _LocalModel:
    def __init__(self, model_dir: Path, veto_confidence: float):
        import joblib
        import numpy as np
        from sentence_transformers import SentenceTransformer

        self._np = np
        self.model_dir = model_dir
        self.veto_confidence = veto_confidence
        self.manifest = _load_and_verify_manifest(model_dir)
        self.encoder = SentenceTransformer(str(model_dir / "encoder"), device="cpu")
        # Pickle-based loads are safe here: these artifacts are produced by our
        # own training pipeline (ml_model/notebooks/04) and resolved from the
        # repo/deployment image or an operator-set LOCAL_MODEL_DIR — never from
        # user-supplied input.
        self.category_head = joblib.load(model_dir / "category_head.joblib")
        index = np.load(model_dir / "merchant_index.npz", allow_pickle=True)
        self.merchant_centroids = index["centroids"].astype(np.float32)
        self.merchant_names = list(index["names"])
        classes = json.loads((model_dir / "label_classes.json").read_text())
        artifact_merchant_threshold = float(classes["merchant_threshold"])
        # Prefer abstention over a confidently wrong merchant. The packaged
        # PFC checkpoint's 0.50/0.08 calibration rejected too few unseen
        # merchants; the later whole-merchant candidate demonstrated a safer
        # 0.65/0.20 operating point. Operators can only make these defaults
        # stricter without rebuilding the artifact.
        self.merchant_threshold = max(
            artifact_merchant_threshold,
            float(os.environ.get("LOCAL_MODEL_MERCHANT_THRESHOLD", "0.65")),
        )
        # optional top1-top2 margin: rejects rows sitting between several
        # centroids (typical for merchants absent from the index)
        artifact_merchant_margin = float(classes.get("merchant_margin", 0.0))
        self.merchant_margin = max(
            artifact_merchant_margin,
            float(os.environ.get("LOCAL_MODEL_MERCHANT_MARGIN", "0.20")),
        )
        self.auto_accept_threshold = float(
            classes.get("category_auto_accept_threshold", 0.5)
        )
        self.app_category_map = classes.get("app_category_map") or V3_TO_APP_CATEGORY
        self.taxonomy = classes.get("taxonomy", "v3")
        self.model_version = classes.get(
            "model_version", self.manifest.get("model_version", model_dir.name)
        )

        unmapped = {
            str(category)
            for category in self.category_head.classes_
            if str(category) not in self.app_category_map
        }
        if unmapped:
            raise ValueError(
                f"Model categories missing app display mappings: {sorted(unmapped)}"
            )

    def predict(
        self, transactions: Sequence[Mapping[str, Any]]
    ) -> List[LocalPrediction]:
        if not transactions:
            return []

        np = self._np
        texts = [_build_input_text(t) for t in transactions]
        embeddings = np.asarray(
            self.encoder.encode(
                texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False
            ),
            dtype=np.float32,
        )

        proba = self.category_head.predict_proba(embeddings)
        confidences = proba.max(axis=1)
        model_categories = self.category_head.classes_[proba.argmax(axis=1)]

        sims = embeddings @ self.merchant_centroids.T
        top_idx = sims.argmax(axis=1)
        top_sims = sims[np.arange(len(sims)), top_idx]
        if sims.shape[1] > 1:
            part = np.partition(sims, -2, axis=1)
            margins = top_sims - part[:, -2]
        else:
            margins = np.full(len(sims), np.inf)

        predictions: List[LocalPrediction] = []
        for i in range(len(transactions)):
            merchant_unknown = bool(
                top_sims[i] < self.merchant_threshold
                or margins[i] < self.merchant_margin
            )
            confidence = float(confidences[i])
            vetoed = merchant_unknown and confidence < self.veto_confidence
            low_confidence = confidence < self.auto_accept_threshold
            app_category = (
                "Uncategorized"
                if vetoed or low_confidence
                else self.app_category_map.get(
                    str(model_categories[i]), "Uncategorized"
                )
            )
            predictions.append(
                LocalPrediction(
                    category=app_category,
                    model_category=str(model_categories[i]),
                    confidence=confidence,
                    merchant_name=(
                        None
                        if merchant_unknown
                        else str(self.merchant_names[top_idx[i]])
                    ),
                    merchant_similarity=float(top_sims[i]),
                    merchant_unknown=merchant_unknown,
                    vetoed=vetoed,
                    low_confidence=low_confidence,
                    taxonomy=self.taxonomy,
                    model_version=self.model_version,
                )
            )
        return predictions


def _build_input_text(transaction: Mapping[str, Any]) -> str:
    """Leakage-free key-value serialisation, identical to the training format."""
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
    tx_type = {"c": "credit", "d": "debit", "credit": "credit", "debit": "debit"}.get(
        raw_type, "unknown"
    )

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
        bucket = next(name for hi, name in _AMOUNT_BUCKETS if amount < hi)
    except (StopIteration, TypeError, ValueError):
        bucket = "unknown"

    return (
        f"transaction: {description} | type: {tx_type} "
        f"| payment: {payment_type} | amount_bucket: {bucket}"
    )


def _load_and_verify_manifest(model_dir: Path) -> Mapping[str, Any]:
    """Load optional artifact metadata and verify recorded artifact checksums."""
    manifest_path = model_dir / "model_manifest.json"
    if not manifest_path.is_file():
        return {}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checksums = {
        "encoder_sha256": model_dir / "encoder" / "model.safetensors",
        "category_head_sha256": model_dir / "category_head.joblib",
        "merchant_index_sha256": model_dir / "merchant_index.npz",
    }
    for manifest_key, artifact_path in checksums.items():
        expected = manifest.get(manifest_key)
        if not expected:
            continue
        digest = hashlib.sha256()
        with artifact_path.open("rb") as artifact_file:
            for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise ValueError(
                f"Artifact checksum mismatch for {artifact_path}: "
                f"expected {expected}, got {actual}"
            )
    return manifest


def _resolve_model_dir() -> Optional[Path]:
    configured = os.environ.get("LOCAL_MODEL_DIR")
    candidates: List[Path] = []
    if configured:
        candidates.append(Path(configured))
    else:
        resources = _API_ROOT / "app" / "resources" / "models"
        candidates.append(resources / "pfc_v4")
        candidates.append(resources / "category_merchant_v3")
        runs_dir = _REPO_ROOT / "ml_model" / "models"
        if runs_dir.is_dir():
            # Newest Galahad/PFCv1-aligned runs first, then legacy v3 runs.
            candidates.extend(sorted(runs_dir.glob("pfc_v4_*"), reverse=True))
            candidates.extend(
                sorted(runs_dir.glob("category_merchant_v3_*"), reverse=True)
            )

    required = (
        "encoder/model.safetensors",
        "category_head.joblib",
        "merchant_index.npz",
        "label_classes.json",
    )
    for candidate in candidates:
        if all((candidate / name).exists() for name in required):
            return candidate
    return None


_lock = threading.Lock()
_model: Optional[_LocalModel] = None
_load_failed = False


def _get_model() -> Optional[_LocalModel]:
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    with _lock:
        if _model is not None or _load_failed:
            return _model
        if os.environ.get("LOCAL_MODEL_ENABLED", "1").lower() in {"0", "false", "no"}:
            _load_failed = True
            return None
        model_dir = _resolve_model_dir()
        if model_dir is None:
            logger.info(
                "Local transaction model artifacts not found - using HF fallback"
            )
            _load_failed = True
            return None
        try:
            veto_confidence = float(
                os.environ.get("LOCAL_MODEL_VETO_CONFIDENCE", "0.95")
            )
            _model = _LocalModel(model_dir, veto_confidence)
            logger.info(
                "Local transaction model loaded",
                extra={
                    "model_dir": str(model_dir),
                    "model_version": _model.model_version,
                    "taxonomy": _model.taxonomy,
                    "merchants": len(_model.merchant_names),
                    "merchant_threshold": _model.merchant_threshold,
                    "veto_confidence": veto_confidence,
                },
            )
        except Exception as exc:  # missing deps, corrupt artifacts, etc.
            logger.warning(f"Local transaction model unavailable: {exc}")
            _load_failed = True
    return _model


def local_model_available() -> bool:
    return _get_model() is not None


def local_model_required() -> bool:
    """Return whether falling back to a non-PFC classifier is forbidden."""
    configured = os.environ.get("LOCAL_MODEL_REQUIRED")
    if configured is not None:
        return configured.strip().casefold() not in {"0", "false", "no", "off"}
    return os.environ.get("ENVIRONMENT", "development").strip().casefold() == (
        "production"
    )


def local_model_status() -> dict[str, Any]:
    """Return non-sensitive runtime metadata for health checks and logs."""
    model = _get_model()
    if model is None:
        return {
            "ready": False,
            "required": local_model_required(),
            "backend": "plaid_pfc_v1_local",
        }
    return {
        "ready": True,
        "required": local_model_required(),
        "backend": "plaid_pfc_v1_local",
        "model_version": model.model_version,
        "taxonomy": model.taxonomy,
    }


def require_local_model() -> dict[str, Any]:
    """Load and verify the packaged PFC checkpoint or fail startup/build."""
    status = local_model_status()
    if not status["ready"]:
        raise RuntimeError(
            "Plaid-PFC-aligned local transaction model is required but "
            "its dependencies or verified artifacts are unavailable"
        )
    return status


def classify_with_local_model(
    transactions: Sequence[Mapping[str, Any]],
) -> Optional[List[LocalPrediction]]:
    """Predict categories and merchants for a batch; None if the model is unavailable."""
    model = _get_model()
    if model is None:
        return None
    return model.predict(transactions)


def reset_local_model_cache() -> None:
    """Testing hook: force the next call to reload the model."""
    global _model, _load_failed
    with _lock:
        _model = None
        _load_failed = False
