# api/app/services/goals/__init__.py

"""
Goals Services Module

Services for managing user financial goals.

Services:
- GoalService - Goal CRUD and tracking operations

Usage:
    from ..services.goals import GoalService
"""

from .goal_service import GoalService

__all__ = ["GoalService"]
