"""Lightweight transaction rule classifier for category and merchant enrichment."""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .transaction_consistency import canonical_category

DEFAULT_RULES_PATH = (
    Path(__file__).resolve().parents[1] / "resources" / "transaction_regex_rules.csv"
)

CATEGORY_MAP = {
    "Alcohol & Tobacco": "Liquor Stores/Alcohol",
    "Bank Charges": "Bank Fees",
    "Bills & Utilities": "Utilities",
    "Cash & Transfer": "Transfers",
    "Cash Deposit": "Deposit",
    "Financial Obligations": "Loan",
    "Food & Drink": "Fast Food",
    "Health & Wellness": "Healthcare",
    "Home & Utilities": "Shopping",
    "Leisure": "Eating Out",
    "Lifestyle": "Entertainment",
    "Loans": "Loan",
    "Mortgage": "Home Loan",
    "Outdoor & Recreation": "Shopping",
    "Transportation": "Fuel/Retail",
}

SUBCATEGORY_CATEGORY_MAP = {
    "Account Maintanance": "Bank Fees",
    "Adventure Gear": "Shopping",
    "Agricultural Retail": "Agricultural Supplies",
    "Airtime": "Airtime",
    "Automotive": "Automotive",
    "Automotive & Outdoors": "Automotive",
    "Building Supplies": "Building Supplies",
    "Café": "Coffee Shops/Cafes",
    "Car Rental": "Car Rentals",
    "Card Replacement": "Bank Fees",
    "Credit Card": "Credit Card Payment",
    "Debt Repayment": "Loan",
    "Deposit": "Deposit",
    "Doctor Clinic": "Healthcare",
    "Electronic Payment": "Online Payments",
    "Fast Food": "Fast Food",
    "Fees and Taxes": "Bank Fees",
    "Fuel": "Fuel/Retail",
    "Furniture & Appliances": "Shopping",
    "Hardware": "Building Supplies",
    "Home Loan": "Home Loan",
    "Insurance": "Insurance",
    "Liquor Store": "Liquor Stores/Alcohol",
    "Lodges & Resorts": "Travel",
    "Maintennace & Accessories": "Automotive",
    "Medical Specialist": "Healthcare",
    "Municipality": "Utilities",
    "Nightlife": "Entertainment",
    "Pension": "Pension Benefit",
    "Pension & Benefits": "Pension Benefit",
    "Personal Loan": "Personal / Business Loan",
    "Personal Transfer": "Personal Transfer",
    "Pharmacy": "Health & Beauty",
    "Received Money": "Income",
    "Restaurant": "Eating Out",
    "Reversal": "Reversal",
    "Salary & Wages": "Salary",
    "Savings": "Savings",
    "Send Money": "Transfers",
    "Store Account": "Loan",
    "Tax Levy": "Bank Fees",
    "Tours & Safaris": "Travel",
    "Unit Trusts": "Investments",
    "Vehicle & Asset Finance": "Vehicle Finance",
    "Withdrawal": "Cash",
}


@dataclass(frozen=True)
class TransactionRule:
    rule_id: str
    order: int
    display_name: str
    pattern: re.Pattern[str]
    merchant_name: str
    category: str
    subcategory: str


@dataclass(frozen=True)
class RuleClassification:
    category: str | None
    merchant_name: str | None
    subcategory: str | None
    rule_id: str
    rule_order: int
    rule_name: str
    confidence: float


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null", "<na>"}:
        return ""
    return " ".join(text.split())


def _int_or_zero(value: Any) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _clean_pattern(value: Any) -> str:
    return _clean(value).replace("(?)", "")


def _app_category(category: str, subcategory: str) -> str:
    return canonical_category(
        SUBCATEGORY_CATEGORY_MAP.get(
            subcategory,
            CATEGORY_MAP.get(category, category),
        )
    )


def _rules_path() -> Path:
    configured = os.environ.get("TRANSACTION_REGEX_RULES_PATH")
    if configured:
        return Path(configured)
    return DEFAULT_RULES_PATH


@lru_cache(maxsize=4)
def load_transaction_rules(path_text: str | None = None) -> tuple[TransactionRule, ...]:
    """Load ordered regex rules from a CSV artifact."""
    path = Path(path_text) if path_text else _rules_path()
    if not path.exists():
        return tuple()

    rules: list[TransactionRule] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            pattern_text = _clean_pattern(row.get("regex_include"))
            if not pattern_text:
                continue
            try:
                pattern = re.compile(pattern_text, flags=re.IGNORECASE)
            except re.error:
                continue

            rules.append(
                TransactionRule(
                    rule_id=_clean(row.get("rules_id")),
                    order=_int_or_zero(row.get("order")),
                    display_name=(
                        _clean(row.get("display_name_detail"))
                        or _clean(row.get("display_name"))
                        or _clean(row.get("merchant_name"))
                    ),
                    pattern=pattern,
                    merchant_name=_clean(row.get("merchant_name")),
                    category=_app_category(
                        _clean(row.get("category")),
                        _clean(row.get("subcategory")),
                    ),
                    subcategory=_clean(row.get("subcategory")),
                )
            )

    return tuple(sorted(rules, key=lambda rule: (rule.order, rule.rule_id)))


def transaction_description(transaction: Mapping[str, Any]) -> str:
    return _clean(
        transaction.get("transaction_description")
        or transaction.get("TransactionParticulars")
        or transaction.get("TransactionDescription")
        or transaction.get("description")
    )


def classify_transaction_with_rules(
    transaction: Mapping[str, Any],
    *,
    rules_path: Path | None = None,
) -> RuleClassification | None:
    """Return the first matching high-precision rule for a transaction."""
    description = transaction_description(transaction)
    if not description:
        return None

    rules = load_transaction_rules(str(rules_path) if rules_path else None)
    if not rules:
        return None

    for rule in rules:
        if rule.pattern.search(description):
            return RuleClassification(
                category=rule.category or None,
                merchant_name=rule.merchant_name or None,
                subcategory=rule.subcategory or None,
                rule_id=rule.rule_id,
                rule_order=rule.order,
                rule_name=rule.display_name,
                confidence=0.99,
            )
    return None


def clear_rule_cache() -> None:
    load_transaction_rules.cache_clear()


# --- deterministic merchant extraction -------------------------------------
#
# Fallback merchant source for descriptions whose merchant is written in the
# text itself but is absent from both the regex rules and the model's
# merchant index (e.g. "POS Purchase 3.99 Spotify 484850*3268"). Only fires
# on strict bank-statement layouts, so it stays high precision.

_POS_PURCHASE_RE = re.compile(r"(?:pos|card)\s+purchase\s+(.+)", re.IGNORECASE)
_PAYMENT_TO_RE = re.compile(
    r"(?:app|online|internet|mobile)?\s*payment\s+to\s+(.+)", re.IGNORECASE
)
_AMOUNT_TOKEN_RE = re.compile(r"^\d[\d.,]*$")
_CARD_MASK_RE = re.compile(r"^\d[\d*]+$|^\d{4,}\*")
_TRAILING_MASK_RE = re.compile(r"\*+\d+$")
_GENERIC_MERCHANTS = {
    "purchase",
    "payment",
    "transfer",
    "fee",
    "fees",
    "cash",
    "atm",
    "card",
    "account",
    "bank",
    "unknown",
    "pending",
}


def extract_merchant_candidate(description: str) -> str | None:
    """Extract a merchant/payee name from a structured bank description.

    Returns None unless the description matches a known statement layout and
    the extracted name looks like a real merchant (>=3 letters, not generic).
    """
    if not description:
        return None

    match = _POS_PURCHASE_RE.search(description) or _PAYMENT_TO_RE.search(description)
    if not match:
        return None

    kept: list[str] = []
    for i, token in enumerate(match.group(1).split()):
        if i == 0 and _AMOUNT_TOKEN_RE.match(token):
            continue  # leading transaction amount
        if _CARD_MASK_RE.match(token):
            break  # masked card / reference number ends the merchant name
        token = _TRAILING_MASK_RE.sub("", token)  # "Revolut**6242" -> "Revolut"
        if token:
            kept.append(token)
        if len(kept) >= 6:
            break

    candidate = " ".join(kept).strip(" -_.")
    letters = sum(ch.isalpha() for ch in candidate)
    if letters < 3 or len(candidate) > 40:
        return None
    if candidate.lower() in _GENERIC_MERCHANTS:
        return None
    if candidate.islower() or candidate.isupper():
        candidate = candidate.title()
    return candidate
