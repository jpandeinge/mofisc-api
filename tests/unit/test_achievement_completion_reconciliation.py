from types import SimpleNamespace
from unittest.mock import MagicMock

from api.app.services.achievements import achievement_service
from api.app.services.user_metrics.storage.metrics_storage import (
    reconcile_completed_achievements as reconcile_supabase_achievements,
    update_user_achievements,
)


class _FakeUserAchievementsTable:
    def __init__(self, rows):
        self.rows = rows
        self.payload = None
        self.filters = []

    def update(self, payload):
        self.payload = payload
        self.filters = []
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def gte(self, field, value):
        self.filters.append(("gte", field, value))
        return self

    def execute(self):
        updated = []
        for row in self.rows:
            matches = all(
                (
                    row.get(field) == value
                    if operation == "eq"
                    else row.get(field, 0) >= value
                )
                for operation, field, value in self.filters
            )
            if matches:
                row.update(self.payload)
                updated.append(dict(row))
        return SimpleNamespace(data=updated)


class _FakeSupabase:
    def __init__(self, rows):
        self.user_achievements = _FakeUserAchievementsTable(rows)

    def table(self, name):
        assert name == "user_achievements"
        return self.user_achievements


def test_supabase_reconciliation_finalizes_only_100_percent_rows_once():
    rows = [
        {
            "id": "complete-but-locked",
            "user_id": "user-1",
            "progress": 100,
            "is_completed": False,
        },
        {
            "id": "in-progress",
            "user_id": "user-1",
            "progress": 99,
            "is_completed": False,
        },
        {
            "id": "other-user",
            "user_id": "user-2",
            "progress": 100,
            "is_completed": False,
        },
    ]
    supabase = _FakeSupabase(rows)

    assert reconcile_supabase_achievements(supabase, "user-1") == 1
    assert reconcile_supabase_achievements(supabase, "user-1") == 0

    assert rows[0]["is_completed"] is True
    assert rows[0]["completed_at"].endswith("+00:00")
    assert rows[1]["is_completed"] is False
    assert rows[2]["is_completed"] is False


def test_metrics_storage_does_not_manufacture_achievement_records():
    supabase = _FakeSupabase([])

    repaired = update_user_achievements(
        supabase,
        "user-1",
        {"real_achievements": {"transaction_milestones": 3}},
    )

    assert repaired == 0
    assert supabase.user_achievements.payload["is_completed"] is True


def test_sqlalchemy_reconciliation_commits_only_when_rows_transition():
    db = MagicMock()
    update = db.query.return_value.filter.return_value.update
    update.side_effect = [2, 0]

    assert achievement_service.reconcile_completed_achievements(db, "user-1") == 2
    assert achievement_service.reconcile_completed_achievements(db, "user-1") == 0

    db.commit.assert_called_once()
    db.rollback.assert_not_called()
