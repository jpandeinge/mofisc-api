from pathlib import Path

from api.app.utils.transaction_rule_classifier import (
    classify_transaction_with_rules,
    clear_rule_cache,
    extract_merchant_candidate,
    load_transaction_rules,
)


def test_loads_packaged_transaction_rules():
    clear_rule_cache()

    rules = load_transaction_rules()

    assert len(rules) > 100
    assert rules[0].order <= rules[-1].order


def test_matches_category_and_merchant_from_packaged_rules():
    result = classify_transaction_with_rules(
        {"transaction_description": "POS PURCHASE SHOPRITE WINDHOEK"}
    )

    assert result is not None
    assert result.category == "Groceries"
    assert result.merchant_name == "Shoprite"
    assert result.subcategory == "Supermarket"
    assert result.confidence == 0.99


def test_maps_rule_taxonomy_to_app_category():
    result = classify_transaction_with_rules({"transaction_description": "KFC WINDHOEK"})

    assert result is not None
    assert result.category == "Fast Food"
    assert result.merchant_name == "KFC"


def test_revolut_card_top_up_has_canonical_merchant_and_transfer_category():
    result = classify_transaction_with_rules(
        {
            "transaction_description": (
                "POS Purchase 10.22 Revolut**6242 484850*3268 30 Sep"
            )
        }
    )

    assert result is not None
    assert result.category == "Transfers"
    assert result.merchant_name == "Revolut"


def test_returns_none_when_no_rule_matches():
    result = classify_transaction_with_rules(
        {"transaction_description": "unrecognised transfer text"}
    )

    assert result is None


def test_can_load_rules_from_custom_csv(tmp_path: Path):
    rules_path = tmp_path / "rules.csv"
    rules_path.write_text(
        "rules_id,order,display_name,display_name_detail,regex_include,"
        "merchant_name,category,subcategory,matched_rows\n"
        "1,1,Test,Test,test merchant,Test Merchant,Shopping,Store,10\n",
        encoding="utf-8",
    )

    result = classify_transaction_with_rules(
        {"transaction_description": "Payment to TEST MERCHANT"},
        rules_path=rules_path,
    )

    assert result is not None
    assert result.rule_id == "1"
    assert result.category == "Shopping"
    assert result.merchant_name == "Test Merchant"


def test_extracts_merchant_from_structured_purchase_description():
    assert (
        extract_merchant_candidate("POS Purchase 3.99 Spotify 484850*3268")
        == "Spotify"
    )


def test_extracts_payee_and_strips_trailing_card_mask():
    assert extract_merchant_candidate("Payment to Revolut**6242") == "Revolut"


def test_rejects_opaque_or_generic_merchant_candidates():
    assert extract_merchant_candidate("991002884411") is None
    assert extract_merchant_candidate("Payment to transfer") is None
