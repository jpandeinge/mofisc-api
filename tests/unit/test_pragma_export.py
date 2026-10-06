from api.scripts.export_pragma_dataset import (
    _derived_subscription_event,
    _feedback_rows,
    _goal_history_event,
    _profile_rows,
    _transaction_event,
    day_to_us,
    pseudonymize_user_id,
)


def test_pseudonymized_ids_are_stable_and_keyed():
    first = pseudonymize_user_id("user-123", b"key-a")

    assert first == pseudonymize_user_id("user-123", b"key-a")
    assert first != pseudonymize_user_id("user-123", b"key-b")
    assert first != pseudonymize_user_id("user-456", b"key-a")
    assert "user-123" not in first


def test_day_to_us_uses_utc_midnight():
    assert day_to_us("1970-01-02") == 86_400_000_000


def test_transaction_event_excludes_predicted_labels():
    event = _transaction_event(
        {
            "id": "tx-1",
            "user_id": "user-1",
            "transaction_date": "2026-01-02",
            "transaction_description": "POS SHOPRITE",
            "transaction_amount": 50,
            "transaction_type": "debit",
            "bank_name": "Example Bank",
            "balance": 200,
            "category": "Eating Out",
            "merchant_name": "Wrong Merchant",
            "confidence": 0.99,
        }
    )

    assert event.source == "transaction"
    assert event.fields["description"] == "pos shoprite"
    assert "category" not in event.fields
    assert "merchant_name" not in event.fields
    assert "confidence" not in event.fields


def test_goal_event_minimises_personal_text():
    event = _goal_history_event(
        {
            "id": "history-1",
            "user_id": "user-1",
            "event_timestamp": "2026-01-03T10:00:00+00:00",
            "event_type": "created",
            "name": "Private family goal",
            "metadata": {"note": "sensitive"},
            "category": "Emergency",
            "target_amount": 1000,
            "current_amount": 10,
            "status": "active",
        }
    )

    assert event.fields["event_type"] == "goal_created"
    assert "name" not in event.fields
    assert "metadata" not in event.fields


def test_derived_subscription_omits_current_name_and_category_outputs():
    event = _derived_subscription_event(
        {
            "id": "subscription-1",
            "user_id": "user-1",
            "first_detected": "2026-01-03",
            "amount": 9.99,
            "frequency": "monthly",
            "transaction_count": 4,
            "confidence_score": 0.8,
            "is_active": True,
            "name": "Wrong Name",
            "category": "Wrong Category",
            "description_pattern": "private-pattern",
        }
    )

    assert event.fields["event_type"] == "recurring_candidate_detected"
    assert "name" not in event.fields
    assert "category" not in event.fields
    assert "description_pattern" not in event.fields


def test_profiles_exclude_pii_and_keep_real_milestones():
    events = [
        _transaction_event(
            {
                "id": "tx-1",
                "user_id": "user-1",
                "transaction_date": "2026-01-02",
                "transaction_description": "test",
                "transaction_amount": 1,
                "transaction_type": "debit",
                "bank_name": "bank",
                "balance": 2,
            }
        )
    ]
    profiles = _profile_rows(
        [
            {
                "id": "user-1",
                "user_type": "individual",
                "is_active": True,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-03T00:00:00+00:00",
                "full_name": "Do Not Export",
                "email": "private@example.com",
            }
        ],
        events,
        b"secret",
        False,
    )

    attributes = dict(profiles[0]["attributes"])
    milestones = {item["key"] for item in profiles[0]["lifelong"]}
    assert attributes == {"user_type": "individual", "is_active": "true"}
    assert {"profile_created", "first_transaction"} <= milestones


def test_user_edits_become_supervised_feedback_not_events():
    feedback = _feedback_rows(
        [
            {
                "transaction_id": "tx-1",
                "user_id": "user-1",
                "field_name": "merchant_name",
                "old_value": "Wrong",
                "new_value": "Correct",
                "edited_at": "2026-01-04T00:00:00+00:00",
            }
        ],
        {
            "tx-1": {
                "transaction_description": "POS CORRECT",
                "transaction_amount": 5,
                "transaction_type": "debit",
                "bank_name": "bank",
            }
        },
        b"secret",
        False,
    )

    assert len(feedback) == 1
    assert feedback[0]["corrected_value"] == "Correct"
    assert feedback[0]["transaction_ref"].startswith("txn_")
