from types import SimpleNamespace

from api.scripts import audit_transaction_consistency as audit_module


audit_rows = audit_module.audit_rows


def test_audit_reports_template_conflicts_without_exposing_rows():
    rows = [
        {
            "id": "tx-1",
            "user_id": "user-a",
            "transaction_description": "POS Purchase 10.00 Local Shop 12345678",
            "transaction_type": "debit",
            "category": "Groceries",
            "merchant_name": "Local Shop",
        },
        {
            "id": "tx-2",
            "user_id": "user-a",
            "transaction_description": "POS Purchase 25.00 Local Shop 87654321",
            "transaction_type": "debit",
            "category": "Shopping",
            "merchant_name": "Wrong Shop",
        },
        {
            "id": "tx-3",
            "user_id": "user-b",
            "transaction_description": "POS Purchase 25.00 Local Shop 87654321",
            "transaction_type": "debit",
            "category": "Shopping",
            "merchant_name": None,
        },
    ]

    audit = audit_rows(rows)

    assert audit.transactions == 3
    assert audit.users == 2
    assert audit.repeated_template_groups == 1
    assert audit.category_conflict_groups == 1
    assert audit.merchant_conflict_groups == 1
    assert audit.merchant_coverage_gap_groups == 0
    assert audit.missing_merchant_rows == 1


def test_audit_distinguishes_missing_merchant_coverage_from_conflict():
    rows = [
        {
            "id": "tx-1",
            "user_id": "user-a",
            "transaction_description": "PAYMENT TO EXAMPLE 12345678",
            "transaction_type": "debit",
            "category": "Services",
            "merchant_name": "Example",
        },
        {
            "id": "tx-2",
            "user_id": "user-a",
            "transaction_description": "PAYMENT TO EXAMPLE 87654321",
            "transaction_type": "debit",
            "category": "Services",
            "merchant_name": None,
        },
    ]

    audit = audit_rows(rows)

    assert audit.category_conflict_groups == 0
    assert audit.merchant_conflict_groups == 0
    assert audit.merchant_coverage_gap_groups == 1


def test_rule_mismatch_uses_harmonized_template_expectation(monkeypatch):
    rows = [
        {
            "id": "tx-1",
            "user_id": "user-a",
            "transaction_description": "SHOP 12345678",
            "transaction_type": "debit",
            "category": "Shopping",
            "merchant_name": "Merchant A",
        },
        {
            "id": "tx-2",
            "user_id": "user-a",
            "transaction_description": "SHOP 87654321",
            "transaction_type": "debit",
            "category": "Shopping",
            "merchant_name": "Merchant A",
        },
    ]

    def fake_rule(row):
        merchant = (
            "Merchant A"
            if "12345678" in row["transaction_description"]
            else "Merchant B"
        )
        return SimpleNamespace(
            category="Shopping",
            merchant_name=merchant,
            rule_id=merchant,
            confidence=0.99,
        )

    monkeypatch.setattr(
        audit_module,
        "classify_transaction_with_rules",
        fake_rule,
    )
    monkeypatch.setattr(
        audit_module,
        "classify_transaction_with_template_lookup",
        lambda row: None,
    )

    audit = audit_rows(rows)

    assert audit.rule_category_mismatches == 0
    assert audit.rule_merchant_mismatches == 0
