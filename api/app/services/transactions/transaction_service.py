# api/app/services/transactions/transaction_service.py

import datetime
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ...config.settings import settings
from ...errors.exceptions import ValidationError
from ...models.transaction import Transaction
from ...services.achievements import achievement_service
from ...utils.logging_config import get_logger
from ...utils.supabase_utils import (
    delete_transaction as delete_supabase_transaction,
    get_statement_metadata,
    get_supabase_client,
    get_transactions,
    get_transactions_by_statement_id,
    insert_transaction,
    update_transaction as update_supabase_transaction,
)
from ...utils.transaction_rule_classifier import classify_transaction_with_rules
from ...utils.transaction_template_lookup import (
    classify_transaction_with_template_lookup,
)

logger = get_logger(__name__)


class TransactionService:
    @staticmethod
    def create_transaction(transaction_data):
        return insert_transaction(transaction_data)

    @staticmethod
    def get_transactions_with_metadata(
        user_id: str,
        skip: int = 0,
        limit: int = 100,
        statement_id: Optional[str] = None,
        sort_by: str = "transaction_date",
        sort_order: str = "desc",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        # Get transactions with filtering
        transactions = get_transactions(
            user_id=user_id,
            skip=skip,
            limit=limit,
            statement_id=statement_id,
            sort_by=sort_by,
            sort_order=sort_order,
            date_from=date_from,
            date_to=date_to,
        )

        # Get statement metadata (with error handling)
        try:
            statements = get_statement_metadata(user_id)
        except Exception as e:
            logger.warning(
                f"Could not get statement metadata for user {user_id}: {str(e)}"
            )
            statements = None

        # Calculate total records for pagination
        total_records = len(transactions.data) if transactions.data else 0

        # Prepare metadata about the result
        metadata = {
            "total_records": total_records,
            "current_page": skip // limit + 1,
            "total_pages": (total_records + limit - 1) // limit,
            "has_next": total_records > skip + limit,
            "has_previous": skip > 0,
            "statements": statements.data if statements and statements.data else [],
        }

        return {
            "transactions": transactions.data,
            "metadata": metadata,
        }

    @staticmethod
    def get_statement_summary(user_id: str):
        """Get summary of all statements for a user"""
        try:
            statements = get_statement_metadata(user_id)
            return statements.data if statements.data else []
        except Exception as e:
            logger.warning(
                f"Could not get statement metadata for user {user_id}: {str(e)}"
            )
            # Return empty list if metadata function fails
            return []

    @staticmethod
    def get_transactions_by_statement_id(statement_id: str):
        return get_transactions_by_statement_id(statement_id)

    @staticmethod
    def get_transaction_for_user(user_id: str, transaction_id: str):
        return get_transactions(user_id, limit=1, transaction_id=transaction_id)

    @staticmethod
    def update_transaction(transaction_id: str, transaction_data, *, user_id: str):
        if {"id", "user_id", "statement_id"}.intersection(transaction_data):
            raise ValidationError("Transaction ownership and identifiers cannot be changed")
        return update_supabase_transaction(
            transaction_id, transaction_data, user_id=user_id
        )

    @staticmethod
    def delete_transaction(transaction_id: str, *, user_id: str):
        return delete_supabase_transaction(transaction_id, user_id=user_id)

    @staticmethod
    def bulk_create_transactions(transactions_data, user_id: str):
        """Create multiple transactions with proper data mapping."""
        cleaned_data = []
        for transaction in transactions_data:
            rule_match = classify_transaction_with_rules(transaction)
            template_match = classify_transaction_with_template_lookup(transaction)
            if rule_match and rule_match.merchant_name:
                merchant_name = rule_match.merchant_name
            elif template_match:
                merchant_name = template_match.merchant_name
            else:
                merchant_name = None

            if rule_match and rule_match.category:
                category = rule_match.category
                confidence = rule_match.confidence
            elif template_match:
                category = template_match.category
                confidence = template_match.confidence
            else:
                category = transaction.get("category", "Uncategorized")
                confidence = transaction.get("confidence")
            cleaned_transaction = {
                "id": transaction.get("id")
                or str(uuid.uuid4()),  # Use provided ID or generate new one
                "user_id": user_id,
                "statement_id": transaction.get("statement_id"),
                "bank_name": transaction.get("bank_name", "Unknown Bank"),
                "transaction_date": transaction.get("transaction_date"),
                "transaction_description": transaction.get(
                    "transaction_description", ""
                ),
                "transaction_amount": float(transaction.get("transaction_amount", 0)),
                "transaction_type": transaction.get("transaction_type", "").lower(),
                "balance": float(transaction.get("balance", 0)),
                "category": category,
                "confidence": confidence,
                "merchant_name": merchant_name,
                "original_merchant_name": merchant_name,
            }

            # Validate required fields
            if not all(
                [
                    cleaned_transaction["transaction_date"],
                    cleaned_transaction["transaction_description"],
                    cleaned_transaction["transaction_amount"],
                    cleaned_transaction["transaction_type"] in ["credit", "debit"],
                ]
            ):
                continue

            cleaned_data.append(cleaned_transaction)

        if not cleaned_data:
            raise ValidationError("No valid transactions to insert")

        return insert_transaction(cleaned_data)

    @staticmethod
    def get_all_user_transactions(
        user_id: str, max_records: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get transactions for a user without pagination.

        When max_records is provided, stops fetching once the cap is reached to
        avoid pulling unnecessary pages from Supabase.
        """
        try:
            # Backend service should always use service role to avoid RLS surprises.
            supabase = get_supabase_client(use_service_role=True)
            logger.info(f"Fetching all transactions for user: {user_id}")

            all_transactions = []
            page_size = 1000  # Supabase default limit
            offset = 0

            while True:
                # Respect the requested cap to avoid extra round trips
                if max_records is not None and len(all_transactions) >= max_records:
                    break

                # Adjust the page size when approaching the cap
                current_page_size = (
                    min(page_size, max_records - len(all_transactions))
                    if max_records is not None
                    else page_size
                )

                # Get transactions in batches to handle large datasets
                result = (
                    supabase.table(settings.SUPABASE_TABLE_NAME)
                    .select("*")
                    .eq("user_id", user_id)
                    .order("transaction_date", desc=True)
                    .range(offset, offset + current_page_size - 1)
                    .execute()
                )

                if not result.data or len(result.data) == 0:
                    break

                all_transactions.extend(result.data)
                logger.info(
                    f"Fetched {len(result.data)} transactions (total so far: {len(all_transactions)})"
                )

                # If we got less than page_size, we've reached the end
                if len(result.data) < current_page_size:
                    break

                offset += current_page_size

            logger.info(
                f"Found {len(all_transactions)} total transactions for user {user_id}"
            )
            return all_transactions
        except Exception as e:
            logger.error(f"Failed to get all transactions for user {user_id}: {str(e)}")
            return []


def create_transaction(
    db: Session, transaction_data: Dict[str, Any], user_id: str
) -> Transaction:
    """Create a new transaction"""
    # Generate a UUID for the transaction
    transaction_id = str(uuid.uuid4())

    # Create new transaction
    transaction = Transaction(
        id=transaction_id,
        user_id=user_id,
        transaction_date=transaction_data.get("transaction_date"),
        transaction_description=transaction_data.get("transaction_description"),
        transaction_amount=transaction_data.get("transaction_amount"),
        transaction_type=transaction_data.get("transaction_type"),
        transaction_category=transaction_data.get("transaction_category"),
        transaction_notes=transaction_data.get("transaction_notes", ""),
    )

    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    # Track achievement progress
    if transaction.transaction_type.lower() == "credit":
        # Track savings for credit transactions
        achievement_service.update_achievement_progress(
            db,
            user_id,
            achievement_service.EVENT_SAVINGS_ADDED,
            transaction.transaction_amount,
        )

        # Update savings streak
        achievement_service.update_streak(
            db, user_id, achievement_service.CATEGORY_SAVINGS
        )
    else:
        # Track spending for debit transactions
        achievement_service.update_achievement_progress(
            db,
            user_id,
            achievement_service.EVENT_TRANSACTION_ADDED,
            transaction.transaction_amount,
        )

    # Track category assignment if present
    if transaction.transaction_category:
        achievement_service.update_achievement_progress(
            db, user_id, achievement_service.EVENT_CATEGORY_TRACKED, 1
        )

    return transaction


def get_transaction(
    db: Session, transaction_id: str, user_id: str
) -> Optional[Transaction]:
    """Get a specific transaction"""
    return (
        db.query(Transaction)
        .filter(Transaction.id == transaction_id, Transaction.user_id == user_id)
        .first()
    )


def get_transactions_from_db(
    db: Session, user_id: str, skip: int = 0, limit: int = 100
) -> List[Transaction]:
    """Get all transactions for a user from local database"""
    return (
        db.query(Transaction)
        .filter(Transaction.user_id == user_id)
        .offset(skip)
        .limit(limit)
        .all()
    )


def update_transaction(
    db: Session, transaction_id: str, user_id: str, transaction_data: Dict[str, Any]
) -> Optional[Transaction]:
    """Update a transaction"""
    transaction = get_transaction(db, transaction_id, user_id)
    if not transaction:
        return None

    # Track if category is being added
    track_category = False
    if (
        "transaction_category" in transaction_data
        and transaction_data["transaction_category"]
        and not transaction.transaction_category
    ):
        track_category = True

    # Update fields
    for key, value in transaction_data.items():
        if hasattr(transaction, key):
            setattr(transaction, key, value)

    db.commit()
    db.refresh(transaction)

    # Track category assignment achievement if category was added
    if track_category:
        achievement_service.update_achievement_progress(
            db, user_id, achievement_service.EVENT_CATEGORY_TRACKED, 1
        )

    return transaction


def delete_transaction(db: Session, transaction_id: str, user_id: str) -> bool:
    """Delete a transaction"""
    transaction = get_transaction(db, transaction_id, user_id)
    if not transaction:
        return False

    db.delete(transaction)
    db.commit()
    return True


def get_transactions_by_category(
    db: Session, user_id: str, category: str
) -> List[Transaction]:
    """Get transactions for a user filtered by category"""
    return (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id, Transaction.transaction_category == category
        )
        .all()
    )


def get_transactions_by_date_range(
    db: Session, user_id: str, start_date: datetime.date, end_date: datetime.date
) -> List[Transaction]:
    """Get transactions for a user within a date range"""
    return (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= start_date,
            Transaction.transaction_date <= end_date,
        )
        .all()
    )


def get_transaction_stats(db: Session, user_id: str) -> Dict[str, Any]:
    """Get transaction statistics for a user"""
    transactions = get_transactions_from_db(db, user_id)

    # Calculate total income and expenses
    total_income = sum(
        t.transaction_amount
        for t in transactions
        if t.transaction_type.lower() == "credit"
    )
    total_expenses = sum(
        t.transaction_amount
        for t in transactions
        if t.transaction_type.lower() == "debit"
    )

    # Calculate net cash flow
    net_cash_flow = total_income - total_expenses

    # Get expenses by category
    expenses_by_category = {}
    for t in transactions:
        if t.transaction_type.lower() == "debit" and t.transaction_category:
            category = t.transaction_category
            if category not in expenses_by_category:
                expenses_by_category[category] = 0
            expenses_by_category[category] += t.transaction_amount

    # Sort categories by amount (descending)
    expenses_by_category = dict(
        sorted(expenses_by_category.items(), key=lambda item: item[1], reverse=True)
    )

    return {
        "total_transactions": len(transactions),
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net_cash_flow": net_cash_flow,
        "expenses_by_category": expenses_by_category,
    }
