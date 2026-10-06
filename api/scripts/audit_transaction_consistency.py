"""Read-only audit of stored category and merchant consistency.

The audit never prints transaction descriptions, user IDs, or transaction IDs.
It groups normalized near-identical descriptions within each user and reports
only aggregate counts.

Usage (from galahad-api/api):
    ../venv/bin/python scripts/audit_transaction_consistency.py
    ../venv/bin/python scripts/audit_transaction_consistency.py --user-id UUID
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../api

from app.config.settings import settings  # noqa: E402
from app.utils.supabase_utils import get_supabase_client  # noqa: E402
from app.utils.transaction_consistency import (  # noqa: E402
    ClassificationDecision,
    harmonize_classification_decisions,
    is_fallback_category,
    scoped_template_key,
    transaction_template_key,
)
from app.utils.transaction_rule_classifier import (  # noqa: E402
    classify_transaction_with_rules,
)
from app.utils.transaction_template_lookup import (  # noqa: E402
    classify_transaction_with_template_lookup,
)

FETCH_COLUMNS = (
    "id,user_id,statement_id,transaction_description,transaction_type,"
    "category,merchant_name"
)


@dataclass(frozen=True)
class ConsistencyAudit:
    transactions: int
    users: int
    normalized_template_groups: int
    repeated_template_groups: int
    repeated_transactions: int
    category_conflict_groups: int
    merchant_conflict_groups: int
    merchant_coverage_gap_groups: int
    fallback_category_rows: int
    missing_merchant_rows: int
    rule_category_mismatches: int
    rule_merchant_mismatches: int
    lookup_category_mismatches: int
    lookup_merchant_mismatches: int
    category_counts: Counter[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user-id", help="restrict to one user")
    parser.add_argument("--statement-id", help="restrict to one statement")
    parser.add_argument("--limit", type=int, help="stop after N transactions")
    parser.add_argument("--batch-size", type=int, default=1_000)
    parser.add_argument(
        "--fail-on-conflicts",
        action="store_true",
        help="exit 2 when repeated-template category or merchant conflicts remain",
    )
    parser.add_argument(
        "--show-conflicts",
        action="store_true",
        help=(
            "print conflict templates and label values; never prints transaction, "
            "statement, or user IDs"
        ),
    )
    return parser.parse_args()


def fetch_rows(supabase: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page_size = args.batch_size
        if args.limit is not None:
            page_size = min(page_size, args.limit - len(rows))
            if page_size <= 0:
                break
        query = (
            supabase.table(settings.SUPABASE_TABLE_NAME)
            .select(FETCH_COLUMNS)
            .order("id")
            .range(offset, offset + page_size - 1)
        )
        if args.user_id:
            query = query.eq("user_id", args.user_id)
        if args.statement_id:
            query = query.eq("statement_id", args.statement_id)
        page = query.execute().data or []
        if not page:
            break
        rows.extend(page)
        offset += len(page)
        if len(page) < page_size:
            break
    return rows


def _clean(value: Any) -> str:
    return str(value or "").strip().casefold()


def audit_rows(rows: Iterable[Mapping[str, Any]]) -> ConsistencyAudit:
    materialized = list(rows)
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    category_counts: Counter[str] = Counter()
    fallback_rows = 0
    missing_merchant_rows = 0
    evidence_decisions: list[ClassificationDecision] = []

    for row in materialized:
        groups[scoped_template_key(row)].append(row)
        category = str(row.get("category") or "").strip() or "(blank)"
        category_counts[category] += 1
        fallback_rows += int(is_fallback_category(category))
        missing_merchant_rows += int(not _clean(row.get("merchant_name")))

        rule = classify_transaction_with_rules(row)
        lookup = classify_transaction_with_template_lookup(row)
        if rule and rule.category:
            category = rule.category
            category_source = "rule"
            confidence = rule.confidence
        elif lookup:
            category = lookup.category
            category_source = "exact_lookup"
            confidence = lookup.confidence
        else:
            category = "Uncategorized"
            category_source = "fallback"
            confidence = 0.0
        if rule and rule.merchant_name:
            merchant = rule.merchant_name
            merchant_source = "rule"
        elif lookup and lookup.merchant_name:
            merchant = lookup.merchant_name
            merchant_source = "exact_lookup"
        else:
            merchant = None
            merchant_source = "unknown"
        evidence_decisions.append(
            ClassificationDecision(
                category=category,
                confidence=float(confidence),
                merchant_name=merchant,
                category_source=category_source,
                merchant_source=merchant_source,
            )
        )

    resolved_evidence = harmonize_classification_decisions(
        materialized,
        evidence_decisions,
    )
    rule_category_mismatches = 0
    rule_merchant_mismatches = 0
    lookup_category_mismatches = 0
    lookup_merchant_mismatches = 0
    for row, decision in zip(materialized, resolved_evidence):
        if decision.category_source == "rule":
            rule_category_mismatches += int(
                _clean(decision.category) != _clean(row.get("category"))
            )
        elif decision.category_source == "exact_lookup":
            lookup_category_mismatches += int(
                _clean(decision.category) != _clean(row.get("category"))
            )
        if decision.merchant_source == "rule" and decision.merchant_name:
            rule_merchant_mismatches += int(
                _clean(decision.merchant_name) != _clean(row.get("merchant_name"))
            )
        elif decision.merchant_source == "exact_lookup" and decision.merchant_name:
            lookup_merchant_mismatches += int(
                _clean(decision.merchant_name) != _clean(row.get("merchant_name"))
            )

    repeated = [group for group in groups.values() if len(group) > 1]
    category_conflicts = 0
    merchant_conflicts = 0
    merchant_coverage_gaps = 0
    for group in repeated:
        categories = {_clean(row.get("category")) for row in group}
        merchants = {
            _clean(row.get("merchant_name"))
            for row in group
            if _clean(row.get("merchant_name"))
        }
        has_blank_merchant = any(not _clean(row.get("merchant_name")) for row in group)
        category_conflicts += int(len(categories) > 1)
        merchant_conflicts += int(len(merchants) > 1)
        merchant_coverage_gaps += int(bool(merchants) and has_blank_merchant)

    return ConsistencyAudit(
        transactions=len(materialized),
        users=len({str(row.get("user_id") or "") for row in materialized}),
        normalized_template_groups=len(groups),
        repeated_template_groups=len(repeated),
        repeated_transactions=sum(len(group) for group in repeated),
        category_conflict_groups=category_conflicts,
        merchant_conflict_groups=merchant_conflicts,
        merchant_coverage_gap_groups=merchant_coverage_gaps,
        fallback_category_rows=fallback_rows,
        missing_merchant_rows=missing_merchant_rows,
        rule_category_mismatches=rule_category_mismatches,
        rule_merchant_mismatches=rule_merchant_mismatches,
        lookup_category_mismatches=lookup_category_mismatches,
        lookup_merchant_mismatches=lookup_merchant_mismatches,
        category_counts=category_counts,
    )


def print_audit(audit: ConsistencyAudit) -> None:
    print("[READ ONLY] transaction consistency audit")
    print(f"  transactions:                 {audit.transactions}")
    print(f"  users:                        {audit.users}")
    print(f"  normalized template groups:   {audit.normalized_template_groups}")
    print(f"  repeated template groups:     {audit.repeated_template_groups}")
    print(f"  repeated transactions:        {audit.repeated_transactions}")
    print(f"  category conflict groups:     {audit.category_conflict_groups}")
    print(f"  merchant conflict groups:     {audit.merchant_conflict_groups}")
    print(f"  merchant coverage-gap groups: {audit.merchant_coverage_gap_groups}")
    print(f"  fallback category rows:       {audit.fallback_category_rows}")
    print(f"  missing merchant rows:        {audit.missing_merchant_rows}")
    print(f"  rule category mismatches:     {audit.rule_category_mismatches}")
    print(f"  rule merchant mismatches:     {audit.rule_merchant_mismatches}")
    print(f"  lookup category mismatches:   {audit.lookup_category_mismatches}")
    print(f"  lookup merchant mismatches:   {audit.lookup_merchant_mismatches}")
    print("  category distribution:")
    for category, count in audit.category_counts.most_common():
        print(f"    {category}: {count}")


def _display_counts(values: Iterable[Any], *, include_blank: bool = True) -> str:
    counts = Counter(str(value or "").strip() or "(blank)" for value in values)
    if not include_blank:
        counts.pop("(blank)", None)
    return ", ".join(
        f"{value!r} × {count}"
        for value, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )
    )


def print_conflict_details(rows: Iterable[Mapping[str, Any]]) -> None:
    """Print bounded diagnostic details without database identifiers."""
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[scoped_template_key(row)].append(row)

    conflict_groups: list[tuple[str, list[Mapping[str, Any]]]] = []
    for scoped_key, group in groups.items():
        if len(group) < 2:
            continue
        categories = {_clean(row.get("category")) for row in group}
        merchants = {
            _clean(row.get("merchant_name"))
            for row in group
            if _clean(row.get("merchant_name"))
        }
        if len(categories) <= 1 and len(merchants) <= 1:
            continue
        fingerprint = hashlib.sha256(
            f"{scoped_key[0]}|{scoped_key[1]}".encode("utf-8")
        ).hexdigest()[:12]
        conflict_groups.append((fingerprint, group))

    print("  conflict details (no database IDs):")
    if not conflict_groups:
        print("    none")
    for fingerprint, group in sorted(conflict_groups):
        template = transaction_template_key(group[0])
        print(f"    [{fingerprint}] {template!r}")
        print(
            "      categories: " + _display_counts(row.get("category") for row in group)
        )
        print(
            "      merchants:  "
            + _display_counts(row.get("merchant_name") for row in group)
        )
        rule_values = Counter(
            (
                rule.category or "(blank)",
                rule.merchant_name or "(blank)",
                rule.rule_id,
            )
            for row in group
            if (rule := classify_transaction_with_rules(row)) is not None
        )
        if rule_values:
            formatted = ", ".join(
                f"{category!r} / {merchant!r} / rule {rule_id} × {count}"
                for (category, merchant, rule_id), count in sorted(
                    rule_values.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            )
            print(f"      rule evidence: {formatted}")
        lookup_values = Counter(
            (lookup.category, lookup.merchant_name or "(blank)")
            for row in group
            if (lookup := classify_transaction_with_template_lookup(row)) is not None
        )
        if lookup_values:
            formatted = ", ".join(
                f"{category!r} / {merchant!r} × {count}"
                for (category, merchant), count in sorted(
                    lookup_values.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            )
            print(f"      lookup evidence: {formatted}")

    rule_merchant_mismatches: Counter[tuple[str, str, str]] = Counter()
    for row in rows:
        rule = classify_transaction_with_rules(row)
        if (
            rule
            and rule.merchant_name
            and _clean(rule.merchant_name) != _clean(row.get("merchant_name"))
        ):
            rule_merchant_mismatches[
                (
                    str(row.get("merchant_name") or "").strip() or "(blank)",
                    rule.merchant_name,
                    rule.rule_id,
                )
            ] += 1
    print("  raw rule merchant mismatches (before template harmonization):")
    if not rule_merchant_mismatches:
        print("    none")
    for (stored, expected, rule_id), count in sorted(
        rule_merchant_mismatches.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        print(
            f"    stored {stored!r} -> rule {expected!r} " f"(rule {rule_id}) × {count}"
        )


def main() -> int:
    args = parse_args()
    if args.batch_size <= 0:
        print("ERROR: --batch-size must be positive")
        return 2
    if args.limit is not None and args.limit <= 0:
        print("ERROR: --limit must be positive")
        return 2

    rows = fetch_rows(get_supabase_client(use_service_role=True), args)
    audit = audit_rows(rows)
    print_audit(audit)
    if args.show_conflicts:
        print_conflict_details(rows)
    if args.fail_on_conflicts and (
        audit.category_conflict_groups or audit.merchant_conflict_groups
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
