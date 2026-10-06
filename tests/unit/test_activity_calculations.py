from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from api.app.services.achievements import achievement_service
from api.app.services.achievements.activity_calculations import (
    calculate_activity_points_from_components,
    calculate_level_snapshot,
    health_status,
    next_streak_milestone,
    savings_rate_percentage,
    streak_milestone_progress,
)


def test_level_snapshot_handles_boundaries_and_invalid_points():
    assert calculate_level_snapshot(-10)["current_level"] == 1
    assert calculate_level_snapshot(float("nan"))["total_points"] == 0
    assert calculate_level_snapshot(99)["current_level"] == 1

    level_two = calculate_level_snapshot(100)
    assert level_two["current_level"] == 2
    assert level_two["points_to_next_level"] == 300
    assert level_two["level_progress_percent"] == 0

    assert calculate_level_snapshot(399)["points_to_next_level"] == 1
    assert calculate_level_snapshot(400)["current_level"] == 3


def test_activity_points_are_clamped_and_composed_once():
    assert calculate_activity_points_from_components(125, 7, 83.6) == 229
    assert calculate_activity_points_from_components(-50, -2, float("inf")) == 0
    assert calculate_activity_points_from_components(10, 1, 500) == 70


def test_streak_milestones_always_move_forward():
    assert next_streak_milestone(0) == 3
    assert next_streak_milestone(90) == 365
    assert next_streak_milestone(365) == 730
    assert next_streak_milestone(800) == 1095
    assert streak_milestone_progress(365, 730) == 50


def test_savings_rate_and_health_boundaries_are_stable():
    assert savings_rate_percentage({"savings_rate": 0.25}) == 25
    assert savings_rate_percentage({"savings_rate": -0.1}) == -10
    assert (
        savings_rate_percentage({"savings_rate": 0.25, "savings_rate_percentage": 12.5})
        == 12.5
    )
    assert savings_rate_percentage({"savings_rate": float("nan")}) == 0

    assert health_status(79.9) == "Excellent"
    assert health_status(60) == "Good"
    assert health_status(40) == "Fair"
    assert health_status(float("nan")) == "Needs Work"


def test_reward_sync_never_erases_activity_bonus_points(monkeypatch):
    db = MagicMock()
    stored = SimpleNamespace(points=500, level=3)
    monkeypatch.setattr(
        achievement_service,
        "get_or_create_user_points",
        lambda *_args: stored,
    )

    result = achievement_service.sync_user_points_with_earned_points(
        db, "user-1", earned_points=125
    )

    assert result.points == 500
    assert result.level == 3
    db.commit.assert_not_called()


def test_activity_sync_uses_precomputed_points_without_requerying(monkeypatch):
    db = MagicMock()
    stored = SimpleNamespace(points=0, level=1)
    monkeypatch.setattr(
        achievement_service,
        "get_or_create_user_points",
        lambda *_args: stored,
    )
    monkeypatch.setattr(
        achievement_service,
        "calculate_activity_points",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected reward aggregation")
        ),
    )

    result = achievement_service.sync_user_points_with_activity_points(
        db, "user-1", activity_points=400
    )

    assert result.points == 400
    assert result.level == 3
    db.commit.assert_called_once()


def test_recent_unlock_sort_key_accepts_mixed_timezone_values():
    records = [
        {"completed_at": None},
        {"completed_at": datetime(2026, 7, 23, 12, 0)},
        {"completed_at": datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)},
        {"completed_at": "2026-07-22T12:00:00Z"},
    ]

    records.sort(key=achievement_service._completed_at_sort_key, reverse=True)

    assert records[0]["completed_at"] == datetime(
        2026, 7, 24, 12, 0, tzinfo=timezone.utc
    )
    assert records[-1]["completed_at"] is None
