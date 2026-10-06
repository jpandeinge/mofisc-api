from api.app.utils.transaction_consistency import (
    ClassificationDecision,
    build_safe_classification_update,
    build_reviewed_template_overrides,
    canonical_category,
    harmonize_classification_decisions,
    transaction_template,
    transaction_template_key,
)


def _decision(
    category,
    merchant,
    *,
    category_source="local_model",
    merchant_source="local_model",
    confidence=0.8,
):
    return ClassificationDecision(
        category=category,
        confidence=confidence,
        merchant_name=merchant,
        category_source=category_source,
        merchant_source=merchant_source,
    )


def test_template_removes_purchase_amount_and_volatile_reference():
    first = {
        "transaction_description": "POS Purchase 3.99 Spotify 484850*3268",
    }
    second = {
        "transaction_description": "pos purchase 12.99 SPOTIFY 998877*1111",
    }

    assert transaction_template(first) == transaction_template(second)


def test_template_key_keeps_credit_and_debit_separate():
    transaction = {"transaction_description": "TRANSFER ACCOUNT 123456789"}

    assert transaction_template_key(
        {**transaction, "transaction_type": "credit"}
    ) != transaction_template_key({**transaction, "transaction_type": "debit"})


def test_template_preserves_namibian_carrier_but_not_subscriber_number():
    mtc_first = {
        "transaction_description": "AIRTIME PURCHASE 0811234567",
    }
    mtc_second = {
        "transaction_description": "AIRTIME PURCHASE 0819876543",
    }
    telecom = {
        "transaction_description": "AIRTIME PURCHASE 0851234567",
    }

    assert transaction_template(mtc_first) == transaction_template(mtc_second)
    assert transaction_template(mtc_first) != transaction_template(telecom)


def test_legacy_category_aliases_use_one_pfc_aligned_display_label():
    assert canonical_category("Payment") == "Payments"
    assert canonical_category("Investment") == "Investments"
    assert canonical_category("Service Station") == "Fuel/Retail"
    assert canonical_category("Clothing") == "Shopping"
    assert canonical_category("Personal / Business Loan") == "Loan"
    assert canonical_category("Healthcare") == "Healthcare"


def test_rule_decision_harmonizes_lower_priority_model_disagreement():
    transactions = [
        {
            "user_id": "user-a",
            "transaction_description": "KFC WINDHOEK 12345678",
            "transaction_type": "debit",
        },
        {
            "user_id": "user-a",
            "transaction_description": "KFC WINDHOEK 87654321",
            "transaction_type": "debit",
        },
    ]
    decisions = [
        _decision(
            "Fast Food",
            "KFC",
            category_source="rule",
            merchant_source="rule",
            confidence=0.99,
        ),
        _decision("Groceries", "Nedbank", confidence=0.95),
    ]

    resolved = harmonize_classification_decisions(transactions, decisions)

    assert {decision.category for decision in resolved} == {"Fast Food"}
    assert {decision.merchant_name for decision in resolved} == {"KFC"}
    assert {decision.category_source for decision in resolved} == {"rule"}


def test_exact_lookup_beats_model_but_not_reviewed_or_rule():
    transactions = [
        {
            "user_id": "user-a",
            "transaction_description": "KNOWN PATTERN 12345678",
            "transaction_type": "debit",
        },
        {
            "user_id": "user-a",
            "transaction_description": "KNOWN PATTERN 87654321",
            "transaction_type": "debit",
        },
    ]
    resolved = harmonize_classification_decisions(
        transactions,
        [
            _decision(
                "Groceries",
                "Known Merchant",
                category_source="exact_lookup",
                merchant_source="exact_lookup",
                confidence=0.95,
            ),
            _decision("Shopping", "Wrong Merchant", confidence=0.99),
        ],
    )

    assert {decision.category for decision in resolved} == {"Groceries"}
    assert {decision.merchant_name for decision in resolved} == {"Known Merchant"}
    assert {decision.category_source for decision in resolved} == {"exact_lookup"}


def test_latest_reviewed_edit_propagates_only_within_same_user():
    source_transactions = {
        "tx-old": {
            "id": "tx-old",
            "user_id": "user-a",
            "transaction_description": "SHOPRITE 12345678",
            "transaction_type": "debit",
        },
        "tx-new": {
            "id": "tx-new",
            "user_id": "user-a",
            "transaction_description": "SHOPRITE 87654321",
            "transaction_type": "debit",
        },
    }
    edits = [
        {
            "transaction_id": "tx-old",
            "field_name": "category",
            "new_value": "Shopping",
            "edited_at": "2026-01-01T00:00:00+00:00",
        },
        {
            "transaction_id": "tx-new",
            "field_name": "category",
            "new_value": "Groceries",
            "edited_at": "2026-02-01T00:00:00+00:00",
        },
        {
            "transaction_id": "tx-new",
            "field_name": "merchant_name",
            "new_value": "Shoprite",
            "edited_at": "2026-02-01T00:00:00+00:00",
        },
    ]
    overrides, edited_pairs = build_reviewed_template_overrides(
        edits, source_transactions
    )
    target_transactions = [
        {
            "user_id": "user-a",
            "transaction_description": "SHOPRITE 11223344",
            "transaction_type": "debit",
        },
        {
            "user_id": "user-b",
            "transaction_description": "SHOPRITE 11223344",
            "transaction_type": "debit",
        },
    ]

    resolved = harmonize_classification_decisions(
        target_transactions,
        [
            _decision("Shopping", "Wrong"),
            _decision("Shopping", "Wrong"),
        ],
        overrides,
    )

    assert resolved[0].category == "Groceries"
    assert resolved[0].merchant_name == "Shoprite"
    assert resolved[0].category_source == "reviewed"
    assert resolved[1].category == "Shopping"
    assert resolved[1].merchant_name == "Wrong"
    assert edited_pairs == {
        ("tx-old", "category"),
        ("tx-new", "category"),
        ("tx-new", "merchant_name"),
    }


def test_model_tie_break_is_deterministic():
    transactions = [
        {
            "user_id": "user-a",
            "transaction_description": "UNKNOWN SHOP 12345678",
            "transaction_type": "debit",
        },
        {
            "user_id": "user-a",
            "transaction_description": "UNKNOWN SHOP 87654321",
            "transaction_type": "debit",
        },
    ]

    resolved = harmonize_classification_decisions(
        transactions,
        [
            _decision("Shopping", "Merchant B", confidence=0.8),
            _decision("Groceries", "Merchant A", confidence=0.8),
        ],
    )

    assert {decision.category for decision in resolved} == {"Groceries"}
    assert {decision.merchant_name for decision in resolved} == {"Merchant A"}


def test_unanimous_stored_category_rescues_abstained_template():
    transactions = [
        {
            "user_id": "user-a",
            "transaction_description": "UNKNOWN SHOP 12345678",
            "transaction_type": "debit",
            "category": "Groceries",
            "confidence": 0.91,
        },
        {
            "user_id": "user-a",
            "transaction_description": "UNKNOWN SHOP 87654321",
            "transaction_type": "debit",
            "category": "Uncategorized",
            "confidence": 0.0,
        },
    ]

    resolved = harmonize_classification_decisions(
        transactions,
        [
            _decision(
                "Uncategorized",
                None,
                category_source="fallback",
                merchant_source="unknown",
                confidence=0.0,
            ),
            _decision(
                "Uncategorized",
                None,
                category_source="fallback",
                merchant_source="unknown",
                confidence=0.0,
            ),
        ],
    )

    assert {decision.category for decision in resolved} == {"Groceries"}
    assert {decision.category_source for decision in resolved} == {
        "stored_consensus"
    }
    assert {decision.confidence for decision in resolved} == {0.91}


def test_conflicting_stored_categories_are_not_used_as_truth():
    transactions = [
        {
            "user_id": "user-a",
            "transaction_description": "UNKNOWN SHOP 12345678",
            "transaction_type": "debit",
            "category": "Groceries",
        },
        {
            "user_id": "user-a",
            "transaction_description": "UNKNOWN SHOP 87654321",
            "transaction_type": "debit",
            "category": "Shopping",
        },
    ]

    resolved = harmonize_classification_decisions(
        transactions,
        [
            _decision(
                "Uncategorized",
                None,
                category_source="fallback",
                merchant_source="unknown",
                confidence=0.0,
            ),
            _decision(
                "Uncategorized",
                None,
                category_source="fallback",
                merchant_source="unknown",
                confidence=0.0,
            ),
        ],
    )

    assert {decision.category for decision in resolved} == {"Uncategorized"}
    assert {decision.category_source for decision in resolved} == {"fallback"}


def test_safe_update_never_overwrites_reviewed_fields_or_downgrades():
    transaction = {
        "id": "tx-1",
        "category": "Groceries",
        "merchant_name": "Correct Merchant",
        "original_merchant_name": None,
    }
    payload = build_safe_classification_update(
        transaction,
        _decision(
            "Uncategorized",
            "Wrong Merchant",
            category_source="fallback",
            merchant_source="rule",
        ),
        edited_pairs={
            ("tx-1", "category"),
            ("tx-1", "merchant_name"),
        },
        review_history_available=True,
    )

    assert payload == {}


def test_safe_update_repairs_old_merchant_from_exact_lookup():
    transaction = {
        "id": "tx-1",
        "category": "Fast Food",
        "merchant_name": "Wrong Merchant",
        "original_merchant_name": None,
    }
    payload = build_safe_classification_update(
        transaction,
        _decision(
            "Fast Food",
            "Hungry Lion",
            category_source="exact_lookup",
            merchant_source="exact_lookup",
            confidence=0.95,
        ),
        edited_pairs=set(),
        review_history_available=True,
    )

    assert payload == {
        "merchant_name": "Hungry Lion",
        "original_merchant_name": "Wrong Merchant",
    }


def test_safe_update_fails_closed_when_review_history_is_unavailable():
    transaction = {
        "id": "tx-1",
        "category": "Shopping",
        "merchant_name": "Existing Merchant",
    }
    payload = build_safe_classification_update(
        transaction,
        _decision(
            "Groceries",
            "Replacement",
            category_source="rule",
            merchant_source="rule",
        ),
        edited_pairs=set(),
        review_history_available=False,
    )

    assert payload == {}
