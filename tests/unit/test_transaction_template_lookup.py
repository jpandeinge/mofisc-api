import json
import struct

from api.app.utils.transaction_template_lookup import (
    classify_transaction_with_template_lookup,
    corpus_transaction_template,
    template_digest,
)


def test_corpus_template_matches_training_normalization_examples():
    assert (
        corpus_transaction_template("RVP 27068529 27Hosken Consolidated Investments LI")
        == "rvp # #hosken consolidated investments li"
    )
    assert (
        corpus_transaction_template("ATM RT Cash Deposit Salary/Wages")
        == "atm rt cash deposit salary wages"
    )


def test_hashed_lookup_matches_description_and_direction(tmp_path):
    artifact = tmp_path / "lookup.bin"
    digest = template_digest("pos purchase spotify #", "debit")
    metadata = json.dumps(
        {
            "record_count": 1,
            "categories": ["Subscriptions"],
            "merchants": ["Spotify"],
        },
        separators=(",", ":"),
    ).encode()
    artifact.write_bytes(
        struct.pack(">5sI", b"GLTL1", len(metadata))
        + metadata
        + struct.pack(">16sBBB", bytes.fromhex(digest), 0, 1, 95)
    )

    result = classify_transaction_with_template_lookup(
        {
            "transaction_description": "POS Purchase Spotify 484850",
            "transaction_type": "debit",
        },
        lookup_path=artifact,
    )
    wrong_direction = classify_transaction_with_template_lookup(
        {
            "transaction_description": "POS Purchase Spotify 484850",
            "transaction_type": "credit",
        },
        lookup_path=artifact,
    )

    assert result is not None
    assert result.category == "Subscriptions"
    assert result.merchant_name == "Spotify"
    assert result.confidence == 0.95
    assert wrong_direction is None
