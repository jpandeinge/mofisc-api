"""Pure, shared calculations for the Activity experience.

Keeping these functions independent from database and HTTP concerns makes the
backend contract deterministic and lets the frontend mirror the same boundary
rules without triggering expensive integration tests.
"""

import math
from typing import Any, Dict

LEVEL_CALCULATION_BASE = 100
STREAK_MILESTONES = (3, 7, 14, 30, 60, 90, 365)


def finite_float(value: Any, default: float = 0.0) -> float:
    """Return a finite float, rejecting NaN/Infinity and malformed values."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def non_negative_int(value: Any, default: int = 0) -> int:
    """Coerce a value to a non-negative integer without raising."""
    try:
        return max(0, int(finite_float(value, float(default))))
    except (OverflowError, ValueError):
        return max(0, default)


def calculate_level_from_points(total_points: Any) -> int:
    """Calculate level using level = floor(sqrt(points / 100)) + 1."""
    points = non_negative_int(total_points)
    return max(1, int(math.sqrt(points / LEVEL_CALCULATION_BASE)) + 1)


def calculate_level_snapshot(total_points: Any) -> Dict[str, Any]:
    """Return every XP value required by the Activity level card."""
    points = non_negative_int(total_points)
    current_level = calculate_level_from_points(points)
    next_level_points = current_level**2 * LEVEL_CALCULATION_BASE
    current_level_start_points = (current_level - 1) ** 2 * LEVEL_CALCULATION_BASE
    points_within_level = max(0, points - current_level_start_points)
    points_needed_this_level = max(1, next_level_points - current_level_start_points)
    progress = min(
        100.0, max(0.0, points_within_level / points_needed_this_level * 100)
    )

    return {
        "total_points": points,
        "current_level": current_level,
        "points_to_next_level": max(0, next_level_points - points),
        "next_level_points": next_level_points,
        "current_level_start_points": current_level_start_points,
        "points_within_level": points_within_level,
        "points_needed_this_level": points_needed_this_level,
        "level_progress_percent": round(progress, 2),
    }


def calculate_activity_points_from_components(
    reward_points: Any,
    current_savings_streak: Any = 0,
    financial_score: Any = 0,
) -> int:
    """Calculate XP from durable rewards plus Activity-specific bonuses."""
    rewards = non_negative_int(reward_points)
    streak_bonus = non_negative_int(current_savings_streak) * 10
    score = min(100, non_negative_int(round(finite_float(financial_score))))
    financial_bonus = max(0, score - 50)
    return rewards + streak_bonus + financial_bonus


def next_streak_milestone(current_streak: Any) -> int:
    """Return a milestone that is always strictly ahead of the current streak."""
    streak = non_negative_int(current_streak)
    for milestone in STREAK_MILESTONES:
        if milestone > streak:
            return milestone

    # Continue with annual milestones after the first year instead of returning
    # an already-completed 365-day target.
    return ((streak // 365) + 1) * 365


def streak_milestone_progress(current_streak: Any, milestone: Any) -> float:
    """Return a safe 0-100 progress percentage for the next milestone."""
    streak = non_negative_int(current_streak)
    target = max(1, non_negative_int(milestone, 1))
    return round(min(100.0, max(0.0, streak / target * 100)), 2)


def savings_rate_percentage(metrics: Dict[str, Any]) -> float:
    """Normalise fractional and percentage savings-rate API variants."""
    explicit = metrics.get("savings_rate_percentage")
    if explicit is not None:
        return finite_float(explicit)
    rate = finite_float(metrics.get("savings_rate"))
    return rate * 100 if abs(rate) <= 1 else rate


def health_status(financial_score: Any) -> str:
    """Map the clamped financial score to a stable display band."""
    score = min(100, non_negative_int(round(finite_float(financial_score))))
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Good"
    if score >= 40:
        return "Fair"
    return "Needs Work"
