"""Deterministic category and merchant consistency for transaction templates.

The classifier has several evidence sources (reviewed edits, regex rules,
models, and structured-description extraction). This module gives those
sources one precedence policy and makes near-identical transactions for the
same user resolve to the same category and merchant.

User-reviewed edits are scoped to that user and always win. They are sourced
from ``transaction_edit_history`` rather than ``user_edited_fields`` because
the legacy database trigger also marks automatic classifier writes as edits.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

_SPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_MASKED_REFERENCE_RE = re.compile(r"(?:\*+|x{2,})\d{2,}", re.IGNORECASE)
_LONG_REFERENCE_RE = re.compile(r"\b(?:\d[\s*-]?){6,}\b")
_NA_CARRIER_PHONE_RE = re.compile(r"\b(?:264|0)(81|85)\d{7}\b")
_PURCHASE_AMOUNT_RE = re.compile(
    r"\b((?:pos|card)\s+purchase)\s+[a-z]{0,3}\s*\d+(?:[.,]\d{1,2})?\b",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(r"\b(?:nad|zar|usd|eur|gbp)?\s*\d+[.,]\d{2}\b", re.IGNORECASE)
_NON_TOKEN_RE = re.compile(r"[^a-z0-9<>]+")

_SOURCE_PRIORITY = {
    "reviewed": 500,
    "rule": 400,
    "exact_lookup": 350,
    "local_model": 300,
    "hf_model": 250,
    "extracted": 200,
    "stored_consensus": 150,
    "fallback": 100,
    "unknown": 0,
}
FALLBACK_CATEGORIES = {
    "",
    "classification failed",
    "miscellaneous/unknown",
    "processing...",
    "uncategorized",
    "uncategorised",
    "unknown",
}
_CATEGORY_ALIASES = {
    # Legacy labels that describe the same Plaid-PFC-aligned app category.
    "clothing": "Shopping",
    "investment": "Investments",
    "payment": "Payments",
    "personal / business loan": "Loan",
    "service station": "Fuel/Retail",
}


@dataclass(frozen=True)
class ReviewedValue:
    value: str
    edited_at: str


@dataclass(frozen=True)
class ClassificationDecision:
    category: str
    confidence: float
    merchant_name: str | None
    category_source: str
    merchant_source: str


ReviewedOverrides = dict[tuple[str, str], dict[str, ReviewedValue]]


def is_fallback_category(category: Any) -> bool:
    return str(category or "").strip().casefold() in FALLBACK_CATEGORIES


def canonical_category(category: Any) -> str:
    """Return the single app display label for known legacy aliases."""
    cleaned = str(category or "").strip()
    return _CATEGORY_ALIASES.get(cleaned.casefold(), cleaned)


def transaction_template(transaction: Mapping[str, Any]) -> str:
    """Return a stable template with volatile dates, amounts and refs removed."""
    description = str(
        transaction.get("transaction_description")
        or transaction.get("TransactionParticulars")
        or transaction.get("TransactionDescription")
        or transaction.get("description")
        or ""
    )
    value = unicodedata.normalize("NFKC", description).casefold().strip()
    value = _UUID_RE.sub(" <ref> ", value)
    value = _DATE_RE.sub(" <date> ", value)
    value = _TIME_RE.sub(" <time> ", value)
    value = _PURCHASE_AMOUNT_RE.sub(r"\1 <amount>", value)
    value = _MONEY_RE.sub(" <amount> ", value)
    value = _MASKED_REFERENCE_RE.sub(" <ref> ", value)
    # 081 (MTC) and 085 (Telecom Namibia) are merchant/category evidence, not
    # volatile references. Preserve the carrier prefix while removing the
    # subscriber number so repeated top-ups still share a template.
    value = _NA_CARRIER_PHONE_RE.sub(
        lambda match: f" <phone{match.group(1)}> ",
        value,
    )
    value = _LONG_REFERENCE_RE.sub(" <ref> ", value)
    value = _NON_TOKEN_RE.sub(" ", value)
    return _SPACE_RE.sub(" ", value).strip()


def transaction_template_key(transaction: Mapping[str, Any]) -> str:
    """Include direction so credits and debits never share a classification."""
    raw_direction = str(
        transaction.get("transaction_type")
        or transaction.get("TransactionTypeDebitCredit")
        or ""
    ).casefold()
    direction = {
        "c": "credit",
        "credit": "credit",
        "d": "debit",
        "debit": "debit",
    }.get(raw_direction, "unknown")
    return f"{direction}|{transaction_template(transaction)}"


def scoped_template_key(transaction: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(transaction.get("user_id") or ""),
        transaction_template_key(transaction),
    )


def build_reviewed_template_overrides(
    edit_rows: Iterable[Mapping[str, Any]],
    transactions_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[ReviewedOverrides, set[tuple[str, str]]]:
    """Build latest-per-field reviewed truth and exact edited-field protection."""
    overrides: ReviewedOverrides = {}
    edited_pairs: set[tuple[str, str]] = set()
    for edit in edit_rows:
        field_name = str(edit.get("field_name") or "")
        transaction_id = str(edit.get("transaction_id") or "")
        value = str(edit.get("new_value") or "").strip()
        if field_name not in {"category", "merchant_name"} or not value:
            continue
        transaction = transactions_by_id.get(transaction_id)
        if transaction is None:
            continue
        edited_pairs.add((transaction_id, field_name))
        key = scoped_template_key(transaction)
        edited_at = str(edit.get("edited_at") or "")
        current = overrides.setdefault(key, {}).get(field_name)
        if current is None or edited_at >= current.edited_at:
            overrides[key][field_name] = ReviewedValue(value, edited_at)
    return overrides, edited_pairs


def fetch_reviewed_template_overrides(
    supabase: Any,
    *,
    user_id: str | None = None,
    page_size: int = 1_000,
) -> tuple[ReviewedOverrides, set[tuple[str, str]]]:
    """Load reviewed corrections and their source transactions from Supabase."""
    edits: list[Mapping[str, Any]] = []
    offset = 0
    while True:
        query = (
            supabase.table("transaction_edit_history")
            .select("transaction_id,user_id,field_name,new_value,edited_at")
            .order("edited_at", desc=True)
            .range(offset, offset + page_size - 1)
        )
        if user_id:
            query = query.eq("user_id", user_id)
        page = query.execute().data or []
        edits.extend(page)
        if len(page) < page_size:
            break
        offset += len(page)

    transaction_ids = sorted(
        {
            str(edit.get("transaction_id"))
            for edit in edits
            if edit.get("transaction_id")
        }
    )
    transactions_by_id: dict[str, Mapping[str, Any]] = {}
    for start in range(0, len(transaction_ids), 100):
        rows = (
            supabase.table("transactions")
            .select("id,user_id,transaction_description,transaction_type")
            .in_("id", transaction_ids[start : start + 100])
            .execute()
            .data
            or []
        )
        transactions_by_id.update({str(row["id"]): row for row in rows})
    return build_reviewed_template_overrides(edits, transactions_by_id)


def _priority(source: str) -> int:
    return _SOURCE_PRIORITY.get(source, _SOURCE_PRIORITY["unknown"])


def _choose_value(
    decisions: Sequence[ClassificationDecision],
    *,
    field: str,
) -> tuple[str | None, str, float]:
    candidates: list[tuple[str, str, float]] = []
    for decision in decisions:
        value: str | None
        if field == "category":
            value = decision.category
            source = decision.category_source
        else:
            value = decision.merchant_name
            source = decision.merchant_source
        cleaned = str(value or "").strip()
        if cleaned:
            candidates.append((cleaned, source, float(decision.confidence)))
    if not candidates:
        return None, "unknown", 0.0

    top_priority = max(_priority(source) for _, source, _ in candidates)
    candidates = [
        candidate for candidate in candidates if _priority(candidate[1]) == top_priority
    ]
    normalized_counts = Counter(value.casefold() for value, _, _ in candidates)
    normalized_confidence: dict[str, float] = defaultdict(float)
    normalized_max_confidence: dict[str, float] = defaultdict(float)
    display_values: dict[str, Counter[str]] = defaultdict(Counter)
    for value, _, confidence in candidates:
        normalized = value.casefold()
        normalized_confidence[normalized] += confidence
        normalized_max_confidence[normalized] = max(
            normalized_max_confidence[normalized], confidence
        )
        display_values[normalized][value] += 1

    selected_normalized = sorted(
        normalized_counts,
        key=lambda value: (
            -normalized_counts[value],
            -normalized_confidence[value],
            -normalized_max_confidence[value],
            value,
        ),
    )[0]
    selected_display = sorted(
        display_values[selected_normalized],
        key=lambda value: (-display_values[selected_normalized][value], value),
    )[0]
    selected_source = sorted(
        source
        for value, source, _ in candidates
        if value.casefold() == selected_normalized
    )[0]
    return (
        selected_display,
        selected_source,
        normalized_max_confidence[selected_normalized],
    )


def _single_stored_value(
    transactions: Sequence[Mapping[str, Any]],
    indices: Sequence[int],
    *,
    field: str,
) -> str | None:
    """Return a display value only when stored non-fallback truth is unanimous."""
    display_values: dict[str, Counter[str]] = defaultdict(Counter)
    for index in indices:
        value = str(transactions[index].get(field) or "").strip()
        if not value:
            continue
        if field == "category" and is_fallback_category(value):
            continue
        if field == "category":
            value = canonical_category(value)
        display_values[value.casefold()][value] += 1
    if len(display_values) != 1:
        return None
    normalized = next(iter(display_values))
    return sorted(
        display_values[normalized],
        key=lambda value: (-display_values[normalized][value], value),
    )[0]


def harmonize_classification_decisions(
    transactions: Sequence[Mapping[str, Any]],
    decisions: Sequence[ClassificationDecision],
    reviewed_overrides: (
        Mapping[tuple[str, str], Mapping[str, ReviewedValue]] | None
    ) = None,
    prior_decisions: Mapping[tuple[str, str], ClassificationDecision] | None = None,
) -> list[ClassificationDecision]:
    """Apply reviewed truth and make each scoped template group deterministic."""
    if len(transactions) != len(decisions):
        raise ValueError("transactions and decisions must have the same length")
    reviewed_overrides = reviewed_overrides or {}
    prior_decisions = prior_decisions or {}
    resolved: list[ClassificationDecision] = []
    for transaction, decision in zip(transactions, decisions):
        overrides = reviewed_overrides.get(scoped_template_key(transaction), {})
        reviewed_category = overrides.get("category")
        reviewed_merchant = overrides.get("merchant_name")
        resolved.append(
            replace(
                decision,
                category=(
                    reviewed_category.value
                    if reviewed_category
                    else canonical_category(decision.category)
                ),
                merchant_name=(
                    reviewed_merchant.value if reviewed_merchant else decision.merchant_name
                ),
                category_source="reviewed" if reviewed_category else decision.category_source,
                merchant_source="reviewed" if reviewed_merchant else decision.merchant_source,
                confidence=1.0 if reviewed_category else decision.confidence,
            )
        )

    grouped_indices: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, transaction in enumerate(transactions):
        key = scoped_template_key(transaction)
        if key[1].endswith("|"):
            continue
        grouped_indices[key].append(index)

    for indices in grouped_indices.values():
        group = [resolved[index] for index in indices]
        prior = prior_decisions.get(scoped_template_key(transactions[indices[0]]))
        if prior is not None:
            group.append(prior)
        category, category_source, category_confidence = _choose_value(
            group, field="category"
        )
        merchant, merchant_source, _ = _choose_value(group, field="merchant_name")
        if is_fallback_category(category):
            stored_category = _single_stored_value(
                transactions,
                indices,
                field="category",
            )
            if stored_category:
                category = stored_category
                category_source = "stored_consensus"
                category_confidence = max(
                    (
                        float(transactions[index].get("confidence") or 0.0)
                        for index in indices
                        if canonical_category(
                            transactions[index].get("category")
                        ).casefold()
                        == stored_category.casefold()
                    ),
                    default=0.0,
                )
        if not merchant:
            stored_merchant = _single_stored_value(
                transactions,
                indices,
                field="merchant_name",
            )
            if stored_merchant:
                merchant = stored_merchant
                merchant_source = "stored_consensus"
        for index in indices:
            resolved[index] = replace(
                resolved[index],
                category=category or resolved[index].category,
                confidence=category_confidence,
                merchant_name=merchant,
                category_source=category_source,
                merchant_source=merchant_source,
            )
    return resolved


def build_safe_classification_update(
    transaction: Mapping[str, Any],
    decision: ClassificationDecision,
    *,
    edited_pairs: set[tuple[str, str]],
    review_history_available: bool,
) -> dict[str, Any]:
    """Build an automatic update without overwriting reviewed or safer values."""
    transaction_id = str(transaction["id"])
    old_category = transaction.get("category")
    category_edited = (transaction_id, "category") in edited_pairs
    can_update_category = not category_edited or decision.category_source == "reviewed"
    if not review_history_available and not is_fallback_category(old_category):
        can_update_category = False
    if is_fallback_category(decision.category) and not is_fallback_category(
        old_category
    ):
        can_update_category = False

    payload: dict[str, Any] = {}
    if can_update_category and decision.category != old_category:
        payload["category"] = decision.category
        payload["confidence"] = float(decision.confidence)

    existing_merchant = transaction.get("merchant_name")
    merchant_edited = (transaction_id, "merchant_name") in edited_pairs
    can_replace_merchant = decision.merchant_source in {
        "reviewed",
        "rule",
        "exact_lookup",
    }
    if merchant_edited and decision.merchant_source != "reviewed":
        can_replace_merchant = False
    if not review_history_available and existing_merchant:
        can_replace_merchant = False
    if decision.merchant_name and (not existing_merchant or can_replace_merchant):
        payload["merchant_name"] = decision.merchant_name
        if not transaction.get("original_merchant_name"):
            payload["original_merchant_name"] = (
                existing_merchant or decision.merchant_name
            )
    return payload
