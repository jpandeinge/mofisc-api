import json

import pandas as pd

from ml_model.scripts.build_transaction_training_corpus import (
    app_category_labels,
    assign_splits,
    build_input_text,
)
from ml_model.scripts.build_pragma_tokenizer_corpus import (
    stable_score,
    transaction_fields,
)


def _taxonomy():
    return {
        "app_display": {
            "detailed": {"GROCERIES": "Groceries"},
            "primary": {"FOOD_AND_DRINK": "Eating Out", "BANK_FEES": "Bank Fees"},
        }
    }


def test_detailed_label_wins_over_primary_display_category():
    frame = pd.DataFrame(
        {
            "pfc_primary": ["FOOD_AND_DRINK", "BANK_FEES"],
            "pfc_detailed": ["GROCERIES", "OTHER_BANK_FEES"],
        }
    )

    assert app_category_labels(frame, _taxonomy()).tolist() == [
        "Groceries",
        "Bank Fees",
    ]


def test_training_serialization_matches_serving_contract():
    frame = pd.DataFrame(
        {
            "TransactionParticulars": ["POS SHOPRITE"],
            "TransactionTypeDebitCredit": ["d"],
            "PaymentType": [None],
            "TransactionAmount": [0],
        }
    )

    assert build_input_text(frame).iloc[0] == (
        "transaction: pos shoprite | type: debit "
        "| payment: unknown | amount_bucket: lt_10"
    )


def test_merchant_holdout_never_leaks_into_train():
    rows = []
    for merchant in ("A", "B", "C"):
        for index in range(3):
            rows.append(
                {
                    "merchant_v3": merchant,
                    "template": f"{merchant}-{index}",
                    "TransactionDate": "2023-05-01",
                }
            )
    frame, _, held_out = assign_splits(
        pd.DataFrame(rows),
        seed=42,
        holdout_merchants=1,
        merchant_min_templates=2,
        merchant_max_templates=10,
    )

    assert len(held_out) == 1
    assert set(frame.loc[frame["merchant_v3"] == held_out[0], "split"]) == {
        "merchant_holdout_test"
    }


def test_dominant_merchant_is_not_used_as_open_world_holdout():
    rows = [
        {
            "merchant_v3": "Dominant Bank",
            "template": f"bank-{index}",
            "TransactionDate": "2023-05-01",
        }
        for index in range(20)
    ]
    rows.extend(
        {
            "merchant_v3": "Local Shop",
            "template": f"shop-{index}",
            "TransactionDate": "2023-05-01",
        }
        for index in range(3)
    )

    frame, _, held_out = assign_splits(
        pd.DataFrame(rows),
        seed=42,
        holdout_merchants=1,
        merchant_min_templates=2,
        merchant_max_templates=10,
    )

    assert held_out == ["Local Shop"]
    assert "merchant_holdout_test" not in set(
        frame.loc[frame["merchant_v3"] == "Dominant Bank", "split"]
    )


def test_tokenizer_corpus_uses_no_labels_and_stable_hash():
    row = {
        "TransactionParticulars": "POS SHOPRITE",
        "TransactionAmount": 50,
        "TransactionTypeDebitCredit": "d",
        "PaymentType": "card",
        "Channel": "pos",
        "pfc_primary": "FOOD_AND_DRINK",
        "merchant_v3": "Shoprite",
    }

    fields = transaction_fields(row)
    assert "pfc_primary" not in fields
    assert "merchant_v3" not in fields
    assert stable_score("same-template", 42) == stable_score("same-template", 42)
