# api/app/services/goals/goal_service.py

from typing import Any, Dict
from uuid import uuid4

from ...errors.exceptions import DatabaseError, NotFoundError, ValidationError
from ...utils.logging_config import get_logger
from ...utils.supabase_utils import get_supabase_client
from ..notifications.notification_service import notification_service

logger = get_logger(__name__)


class GoalService:
    @staticmethod
    def create_goal(user_id: str, goal_data: Dict[str, Any]):
        """Create a new financial goal."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Prepare goal data
            goal = {
                "id": str(uuid4()),
                "user_id": user_id,
                "name": goal_data.get("name"),
                "target_amount": goal_data.get("targetAmount"),
                "current_amount": goal_data.get("currentAmount", 0),
                "deadline": goal_data.get("deadline"),
                "category": goal_data.get("category"),
                "color": goal_data.get("color"),
                "status": "active",
            }

            # Validate required fields
            required_fields = ["name", "target_amount", "deadline", "category", "color"]
            for field in required_fields:
                if not goal.get(field):
                    raise ValidationError(f"Missing required field: {field}")

            # Insert into database
            result = supabase.table("goals").insert(goal).execute()

            if not result.data:
                raise DatabaseError("Failed to create goal")

            return result.data[0]
        except Exception:
            logger.warning("Goal creation failed")
            raise

    @staticmethod
    def get_goals(user_id: str, include_deleted: bool = False):
        """Get all goals for a user."""
        try:
            supabase = get_supabase_client()

            query = supabase.table("goals").select("*").eq("user_id", user_id)

            if not include_deleted:
                query = query.neq("status", "deleted")

            result = query.execute()

            return result.data
        except Exception:
            logger.warning("Failed to retrieve goals")
            raise

    @staticmethod
    def get_goal(goal_id: str, user_id: str):
        """Get a specific goal."""
        try:
            supabase = get_supabase_client()

            result = (
                supabase.table("goals")
                .select("*")
                .eq("id", goal_id)
                .eq("user_id", user_id)
                .execute()
            )

            if not result.data:
                raise NotFoundError("Goal", goal_id)

            return result.data[0]
        except Exception:
            logger.warning("Failed to retrieve goal")
            raise

    @staticmethod
    def update_goal(goal_id: str, user_id: str, updates: Dict[str, Any]):
        """Update a goal."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Verify goal exists and belongs to user
            goal = GoalService.get_goal(goal_id, user_id)

            if goal.get("status") == "deleted":
                raise ValidationError("Cannot update a deleted goal")

            # Apply updates
            result = (
                supabase.table("goals")
                .update(updates)
                .eq("id", goal_id)
                .eq("user_id", user_id)
                .execute()
            )

            if not result.data:
                raise DatabaseError("Failed to update goal")

            return result.data[0]
        except Exception:
            logger.warning("Failed to update goal")
            raise

    @staticmethod
    def delete_goal(goal_id: str, user_id: str):
        """Soft delete a goal."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Verify goal exists and belongs to user
            GoalService.get_goal(goal_id, user_id)

            # Mark as deleted (soft delete)
            result = (
                supabase.table("goals")
                .update({"status": "deleted"})
                .eq("id", goal_id)
                .eq("user_id", user_id)
                .execute()
            )

            if not result.data:
                raise DatabaseError("Failed to delete goal")

            return result.data[0]
        except Exception:
            logger.warning("Failed to delete goal")
            raise

    @staticmethod
    def add_contribution(goal_id: str, user_id: str, amount: float):
        """Add a contribution to a goal."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Verify goal exists and belongs to user
            goal = GoalService.get_goal(goal_id, user_id)

            if goal.get("status") == "deleted":
                raise ValidationError("Cannot contribute to a deleted goal")

            if amount <= 0:
                raise ValidationError("Contribution amount must be positive")

            # Calculate progress before contribution
            previous_amount = float(goal.get("current_amount", 0))
            target_amount = float(goal.get("target_amount", 1))
            previous_percentage = (previous_amount / target_amount) * 100

            # Call the database function to add contribution
            result = supabase.rpc(
                "add_goal_contribution", {"p_goal_id": goal_id, "p_amount": amount}
            ).execute()

            if not result.data:
                raise DatabaseError("Failed to add contribution")

            # Calculate new progress
            new_amount = previous_amount + amount
            new_percentage = (new_amount / target_amount) * 100

            # Check for milestones (25%, 50%, 75%, 100%)
            milestones = [25, 50, 75, 100]
            for milestone in milestones:
                if previous_percentage < milestone <= new_percentage:
                    if milestone == 100:
                        # Goal completed!
                        import asyncio

                        asyncio.create_task(
                            notification_service.send_goal_notification(
                                user_id=user_id,
                                goal_data={
                                    "goal_id": goal_id,
                                    "goal_name": goal.get("name"),
                                    "target_amount": target_amount,
                                    "final_amount": new_amount,
                                    "duration": "Goal achieved!",
                                    "percentage": 100,
                                },
                                completed=True,
                            )
                        )
                    else:
                        # Milestone reached
                        import asyncio

                        asyncio.create_task(
                            notification_service.send_goal_notification(
                                user_id=user_id,
                                goal_data={
                                    "goal_id": goal_id,
                                    "goal_name": goal.get("name"),
                                    "target_amount": target_amount,
                                    "current_amount": new_amount,
                                    "percentage": int(new_percentage),
                                },
                                completed=False,
                            )
                        )
                    break  # Only send one notification per contribution

            return result.data
        except Exception:
            logger.warning("Failed to add contribution to goal")
            raise

    @staticmethod
    def archive_goal(goal_id: str, user_id: str):
        """Archive a goal."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Verify goal exists and belongs to user
            goal = GoalService.get_goal(goal_id, user_id)

            if goal.get("status") == "deleted":
                raise ValidationError("Cannot archive a deleted goal")

            if goal.get("status") == "archived":
                raise ValidationError("Goal is already archived")

            # Mark as archived
            result = (
                supabase.table("goals")
                .update({"status": "archived"})
                .eq("id", goal_id)
                .eq("user_id", user_id)
                .execute()
            )

            if not result.data:
                raise DatabaseError("Failed to archive goal")

            return result.data[0]
        except Exception:
            logger.warning("Failed to archive goal")
            raise

    @staticmethod
    def unarchive_goal(goal_id: str, user_id: str):
        """Unarchive a goal."""
        try:
            supabase = get_supabase_client(use_service_role=True)

            # Verify goal exists and belongs to user
            goal = GoalService.get_goal(goal_id, user_id)

            if goal.get("status") != "archived":
                raise ValidationError("Only archived goals can be unarchived")

            # Mark as active
            result = (
                supabase.table("goals")
                .update({"status": "active"})
                .eq("id", goal_id)
                .eq("user_id", user_id)
                .execute()
            )

            if not result.data:
                raise DatabaseError("Failed to unarchive goal")

            return result.data[0]
        except Exception:
            logger.warning("Failed to unarchive goal")
            raise

    @staticmethod
    def get_goal_history(goal_id: str, user_id: str):
        """Get the history of a goal."""
        try:
            supabase = get_supabase_client()

            # Verify goal exists and belongs to user
            goal = GoalService.get_goal(goal_id, user_id)

            # Get history records
            result = (
                supabase.table("goals_history")
                .select("*")
                .eq("goal_id", goal_id)
                .order("event_timestamp", option="asc")
                .execute()
            )

            return {"currentGoal": goal, "histories": result.data}
        except Exception:
            logger.warning("Failed to retrieve goal history")
            raise
