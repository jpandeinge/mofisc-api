"""Privacy-minimized exact lookup over the deduplicated 37M-row corpus.

The offline source has 37.18M rows but only about 292k distinct, labelled
transaction templates. The packaged artifact stores only a 128-bit digest of
each normalized template plus its category and conservative merchant label;
raw descriptions and names from transaction text are never shipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .transaction_consistency import canonical_category

DEFAULT_LOOKUP_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "transaction_template_lookup.bin"
)

_DIGIT_RUN_RE = re.compile(r"\d+")
_NON_TEMPLATE_RE = re.compile(r"[^a-z0-9#]+")
_SPACE_RE = re.compile(r"\s+")
_MAGIC = b"GLTL1"
_HEADER = struct.Struct(">5sI")
_RECORD = struct.Struct(">16sBBB")


@dataclass(frozen=True)
class TemplateLookupClassification:
    category: str
    merchant_name: str | None
    confidence: float


def corpus_transaction_template(description: Any) -> str:
    """Match the normalization used by the 37M-row labelled corpus."""
    value = unicodedata.normalize("NFKC", str(description or "")).casefold().strip()
    value = _DIGIT_RUN_RE.sub("#", value)
    value = _NON_TEMPLATE_RE.sub(" ", value)
    return _SPACE_RE.sub(" ", value).strip()


def _direction(transaction: Mapping[str, Any]) -> str:
    raw = str(
        transaction.get("transaction_type")
        or transaction.get("TransactionTypeDebitCredit")
        or ""
    ).casefold()
    return {
        "c": "credit",
        "cr": "credit",
        "credit": "credit",
        "d": "debit",
        "dr": "debit",
        "debit": "debit",
    }.get(raw, "unknown")


def template_digest(template: str, direction: str) -> str:
    """Return a compact, collision-resistant key without retaining raw text."""
    material = f"{direction}|{template}".encode("utf-8")
    return hashlib.blake2b(
        material,
        digest_size=16,
        person=b"galahad-template",
    ).hexdigest()


def transaction_template_digest(transaction: Mapping[str, Any]) -> str:
    description = (
        transaction.get("transaction_description")
        or transaction.get("TransactionParticulars")
        or transaction.get("TransactionDescription")
        or transaction.get("description")
        or ""
    )
    return template_digest(
        corpus_transaction_template(description),
        _direction(transaction),
    )


def _lookup_path() -> Path:
    configured = os.environ.get("TRANSACTION_TEMPLATE_LOOKUP_PATH")
    return Path(configured) if configured else DEFAULT_LOOKUP_PATH


class TransactionTemplateLookup:
    """Compact sorted index backed by one small immutable bytes object."""

    def __init__(self, path: Path):
        data = path.read_bytes()
        if len(data) < _HEADER.size:
            raise ValueError(f"template lookup is truncated: {path}")
        magic, metadata_size = _HEADER.unpack_from(data)
        if magic != _MAGIC:
            raise ValueError(f"invalid template lookup header: {path}")
        metadata_start = _HEADER.size
        records_start = metadata_start + metadata_size
        if records_start > len(data):
            raise ValueError(f"template lookup metadata is truncated: {path}")

        metadata = json.loads(data[metadata_start:records_start].decode("utf-8"))
        record_count = int(metadata["record_count"])
        expected_size = records_start + record_count * _RECORD.size
        if len(data) != expected_size:
            raise ValueError(
                f"template lookup size mismatch: expected {expected_size}, "
                f"found {len(data)}"
            )
        self._data = data
        self._records_start = records_start
        self._record_count = record_count
        self._categories = tuple(str(value) for value in metadata["categories"])
        self._merchants = tuple(str(value) for value in metadata["merchants"])

    def get(self, digest_hex: str) -> TemplateLookupClassification | None:
        target = bytes.fromhex(digest_hex)
        low = 0
        high = self._record_count
        while low < high:
            middle = (low + high) // 2
            offset = self._records_start + middle * _RECORD.size
            candidate = self._data[offset : offset + 16]
            if candidate < target:
                low = middle + 1
            else:
                high = middle
        if low >= self._record_count:
            return None

        offset = self._records_start + low * _RECORD.size
        digest, category_id, merchant_id, confidence_percent = _RECORD.unpack_from(
            self._data, offset
        )
        if digest != target:
            return None
        try:
            category = canonical_category(self._categories[category_id])
            merchant = self._merchants[merchant_id - 1] if merchant_id else None
        except IndexError as exc:
            raise ValueError("template lookup contains an invalid label id") from exc
        return TemplateLookupClassification(
            category=category,
            merchant_name=merchant,
            confidence=confidence_percent / 100.0,
        )


@lru_cache(maxsize=4)
def load_transaction_template_lookup(
    path_text: str | None = None,
) -> TransactionTemplateLookup | None:
    path = Path(path_text) if path_text else _lookup_path()
    if not path.is_file():
        return None
    return TransactionTemplateLookup(path)


def classify_transaction_with_template_lookup(
    transaction: Mapping[str, Any],
    *,
    lookup_path: Path | None = None,
) -> TemplateLookupClassification | None:
    lookup = load_transaction_template_lookup(str(lookup_path) if lookup_path else None)
    if lookup is None:
        return None
    return lookup.get(transaction_template_digest(transaction))


def clear_template_lookup_cache() -> None:
    load_transaction_template_lookup.cache_clear()
