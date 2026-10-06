# api/app/services/calculators/calculator_service.py

from typing import Any, Dict, Optional

from ...utils.supabase_utils import get_supabase_client
from ..base_service import BaseService


class CalculatorService(BaseService):
    """
    Service for managing calculator history and statistics.

    Handles saving calculator usage, retrieving history, and generating usage statistics.
    Follows instance-based pattern for better testability and dependency injection.
    """

    def __init__(self):
        """Initialize calculator service with Supabase client."""
        super().__init__()
        self.supabase = get_supabase_client()
        self.supabase_service_role = get_supabase_client(use_service_role=True)

    def save_calculator_history(
        self,
        user_id: str,
        calculator_type: str,
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Save calculator usage history.

        Args:
            user_id: User identifier
            calculator_type: Type of calculator used
            inputs: Calculator input parameters
            outputs: Calculator output results
            metadata: Optional additional metadata

        Returns:
            Saved history record or None if save failed
        """
        try:
            history_record = {
                "user_id": user_id,
                "calculator_type": calculator_type,
                "inputs": inputs,
                "outputs": outputs,
                "metadata": metadata or {},
            }

            result = (
                self.supabase_service_role.table("calculator_history")
                .insert(history_record)
                .execute()
            )

            if not result.data:
                self.logger.warning(
                    f"Failed to save calculator history for {calculator_type}"
                )
                return None

            return result.data[0]
        except Exception as e:
            self._log_error(
                "Error saving calculator history", e, calculator_type=calculator_type
            )
            # We don't want to throw errors to the user for history tracking
            return None

    def get_calculator_history(
        self,
        user_id: str,
        calculator_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ):
        """
        Get calculator history for a user.

        Args:
            user_id: User identifier
            calculator_type: Optional filter by calculator type
            limit: Maximum number of records to return
            offset: Number of records to skip

        Returns:
            List of calculator history records

        Raises:
            Exception: If retrieval fails
        """
        try:
            query = (
                self.supabase.table("calculator_history")
                .select("*")
                .eq("user_id", user_id)
            )

            if calculator_type:
                query = query.eq("calculator_type", calculator_type)

            query = query.order("created_at", option="desc").range(
                offset, offset + limit - 1
            )

            result = query.execute()

            return result.data
        except Exception as e:
            self._log_error("Error retrieving calculator history", e, user_id=user_id)
            raise

    def get_calculator_statistics(
        self, user_id: str, calculator_type: Optional[str] = None
    ):
        """
        Get usage statistics for calculators.

        Args:
            user_id: User identifier
            calculator_type: Optional filter by calculator type

        Returns:
            Calculator usage statistics

        Raises:
            Exception: If retrieval fails
        """
        try:
            result = self.supabase.rpc(
                "get_calculator_usage_stats",
                {"p_user_id": user_id, "p_calculator_type": calculator_type},
            ).execute()

            return result.data
        except Exception as e:
            self._log_error(
                "Error retrieving calculator statistics", e, user_id=user_id
            )
            raise
