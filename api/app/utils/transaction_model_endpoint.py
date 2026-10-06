"""Selection and client code for the production transaction model backend."""

from __future__ import annotations

import os
import time
from typing import Any, Mapping, Sequence

import requests

from .local_transaction_model import (
    LocalPrediction,
    classify_with_local_model,
    local_model_available,
    local_model_required,
    local_model_status,
)
from .logging_config import get_logger

logger = get_logger(__name__)

_VALID_BACKENDS = {"local", "hf_endpoint", "auto", "legacy_hf"}
_RETRYABLE_STATUS_CODES = {502, 503, 504}


def transaction_model_backend() -> str:
    backend = os.environ.get("TRANSACTION_MODEL_BACKEND", "local").strip().casefold()
    if backend not in _VALID_BACKENDS:
        raise ValueError(
            "TRANSACTION_MODEL_BACKEND must be one of "
            f"{sorted(_VALID_BACKENDS)}, got {backend!r}"
        )
    return backend


def pfc_endpoint_url() -> str | None:
    value = os.environ.get("HF_PFC_ENDPOINT_URL", "").strip()
    return value or None


def pfc_endpoint_token() -> str | None:
    value = os.environ.get("HF_PFC_TOKEN", "").strip()
    return value or None


def pfc_endpoint_configured() -> bool:
    return bool(pfc_endpoint_url() and pfc_endpoint_token())


def transaction_model_required() -> bool:
    configured = os.environ.get("PFC_MODEL_REQUIRED")
    if configured is not None:
        return configured.strip().casefold() not in {"0", "false", "no", "off"}
    return local_model_required()


def transaction_model_status() -> dict[str, Any]:
    backend = transaction_model_backend()

    if backend in {"local", "auto"}:
        local_status = local_model_status()
        if local_status["ready"] or backend == "local":
            return {**local_status, "selected_backend": "local"}

    if backend in {"hf_endpoint", "auto"}:
        configured = pfc_endpoint_configured()
        return {
            "ready": configured,
            "required": transaction_model_required(),
            "backend": "plaid_pfc_v1_hf_endpoint",
            "selected_backend": "hf_endpoint",
            "configuration_verified": configured,
            "inference_verified": False,
        }

    legacy_configured = bool(
        os.environ.get("HF_ENDPOINT_URL", "").strip()
        and os.environ.get("HF_TOKEN", "").strip()
    )
    return {
        "ready": legacy_configured,
        "required": transaction_model_required(),
        "backend": "legacy_hf",
        "selected_backend": "legacy_hf",
        "configuration_verified": legacy_configured,
        "inference_verified": False,
    }


def require_transaction_model() -> dict[str, Any]:
    status = transaction_model_status()
    if not status["ready"] or status["selected_backend"] == "legacy_hf":
        raise RuntimeError(
            "The selected transaction model backend is required but unavailable. "
            "Check TRANSACTION_MODEL_BACKEND and its model/endpoint configuration. "
            "The legacy label-only endpoint cannot satisfy PFC_MODEL_REQUIRED."
        )
    return status


def _prediction_from_response(item: Mapping[str, Any]) -> LocalPrediction:
    required_keys = {
        "category",
        "model_category",
        "confidence",
        "merchant_similarity",
        "merchant_unknown",
        "vetoed",
        "low_confidence",
        "taxonomy",
        "model_version",
    }
    missing = sorted(required_keys.difference(item))
    if missing:
        raise ValueError(
            "HF PFC endpoint returned an incompatible prediction; "
            f"missing fields: {missing}"
        )

    return LocalPrediction(
        category=str(item["category"]),
        model_category=str(item["model_category"]),
        confidence=float(item["confidence"]),
        merchant_name=(
            str(item["merchant_name"])
            if item.get("merchant_name") is not None
            else None
        ),
        merchant_similarity=float(item["merchant_similarity"]),
        merchant_unknown=bool(item["merchant_unknown"]),
        vetoed=bool(item["vetoed"]),
        low_confidence=bool(item["low_confidence"]),
        taxonomy=str(item["taxonomy"]),
        model_version=str(item["model_version"]),
    )


def classify_with_pfc_endpoint(
    transactions: Sequence[Mapping[str, Any]],
    *,
    batch_size: int = 128,
    max_attempts: int = 5,
    connect_timeout: float = 10.0,
    read_timeout: float = 120.0,
) -> list[LocalPrediction]:
    """Classify structured transactions with the custom private HF endpoint."""
    if not transactions:
        return []
    if batch_size < 1 or batch_size > 128:
        raise ValueError("batch_size must be between 1 and 128")

    endpoint_url = pfc_endpoint_url()
    endpoint_token = pfc_endpoint_token()
    if not endpoint_url or not endpoint_token:
        raise RuntimeError("HF PFC endpoint URL and token are required")

    headers = {
        "Authorization": f"Bearer {endpoint_token}",
        "Content-Type": "application/json",
    }
    predictions: list[LocalPrediction] = []

    for batch_start in range(0, len(transactions), batch_size):
        batch = list(transactions[batch_start : batch_start + batch_size])
        payload = {"inputs": batch}
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            try:
                response = requests.post(
                    endpoint_url,
                    headers=headers,
                    json=payload,
                    timeout=(connect_timeout, read_timeout),
                )
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    if attempt < max_attempts - 1:
                        delay = min(2**attempt, 16)
                        logger.info(
                            "HF PFC endpoint is starting or temporarily unavailable",
                            extra={
                                "status_code": response.status_code,
                                "attempt": attempt + 1,
                                "retry_delay_seconds": delay,
                                "batch_number": batch_start // batch_size + 1,
                            },
                        )
                        time.sleep(delay)
                        continue
                response.raise_for_status()

                result = response.json()
                if not isinstance(result, list):
                    raise ValueError("HF PFC endpoint response must be a list")
                if len(result) != len(batch):
                    raise ValueError(
                        "HF PFC endpoint response length does not match request: "
                        f"{len(result)} != {len(batch)}"
                    )
                if not all(isinstance(item, Mapping) for item in result):
                    raise ValueError("HF PFC endpoint returned a non-object prediction")
                predictions.extend(_prediction_from_response(item) for item in result)
                break
            except (requests.RequestException, ValueError, TypeError) as exc:
                last_error = exc
                retryable = isinstance(
                    exc,
                    (
                        requests.ConnectionError,
                        requests.Timeout,
                    ),
                )
                if retryable and attempt < max_attempts - 1:
                    delay = min(2**attempt, 16)
                    logger.info(
                        "HF PFC endpoint request will be retried",
                        extra={
                            "error_type": type(exc).__name__,
                            "attempt": attempt + 1,
                            "retry_delay_seconds": delay,
                            "batch_number": batch_start // batch_size + 1,
                        },
                    )
                    time.sleep(delay)
                    continue
                break
        else:  # pragma: no cover - loop always exits via success or caught error
            last_error = RuntimeError("HF PFC endpoint attempts were exhausted")

        if len(predictions) != min(batch_start + len(batch), len(transactions)):
            raise RuntimeError(
                "HF PFC endpoint did not return a valid prediction batch"
            ) from last_error

    return predictions


def classify_with_transaction_model(
    transactions: Sequence[Mapping[str, Any]],
) -> list[LocalPrediction] | None:
    """Use the selected PFC backend without silently changing its contract."""
    backend = transaction_model_backend()

    if backend in {"local", "auto"} and local_model_available():
        return classify_with_local_model(transactions)

    if backend in {"hf_endpoint", "auto"} and pfc_endpoint_configured():
        return classify_with_pfc_endpoint(transactions)

    return None
