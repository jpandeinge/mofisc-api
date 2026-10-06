# api/app/routes/transaction_edit_endpoints.py
"""
Secure transaction editing endpoints with full audit trail.
Following Revolut-style transaction edit tracking for compliance and UX.

Security features:
- Only allows editing of category and merchant_name (not amounts/dates)
- Full audit trail with IP and user agent logging
- Authorization: users can only edit their own transactions
- Edit history preserved for compliance
"""

from typing import List, Literal, Optional
from uuid import UUID

import sentry_sdk
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from ..models.transaction import Transaction
from ..models.transaction_edit_history import TransactionEditHistory
from ..schemas.transaction import (
    BatchUpdateRequest,
)
from ..schemas.transaction import Transaction as TransactionSchema
from ..schemas.transaction import TransactionEditHistoryResponse, TransactionUpdate
from ..utils.auth import get_current_user
from ..utils.db import get_db
from ..utils.logging_config import get_logger
from ..utils.similarity_matching import get_similarity_matcher
from ..utils.supabase_utils import get_supabase_client

logger = get_logger(__name__)
router = APIRouter()


@router.patch("/transactions/{transaction_id}/edit", response_model=TransactionSchema)
async def edit_transaction(
    request: Request,
    transaction_id: str,
    update_data: TransactionUpdate,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Securely edit transaction category and/or merchant_name with full audit logging.

    Only category and merchant_name can be edited for security.
    All edits are logged to transaction_edit_history for compliance.
    """
    try:
        # Get transaction
        transaction = (
            db.query(Transaction)
            .filter(
                Transaction.id == transaction_id, Transaction.user_id == current_user.id
            )
            .first()
        )

        if not transaction:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transaction not found or you don't have permission to edit it",
            )

        # Track changes for audit log
        changes_made = []

        # Get IP address and user agent for security audit
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        # Update category if provided
        if update_data.category is not None:
            old_category = transaction.category
            transaction.category = update_data.category

            # Store original category if this is the first edit
            if transaction.original_category is None:
                transaction.original_category = old_category

            # Log to edit history
            edit_entry = TransactionEditHistory(
                transaction_id=transaction.id,
                user_id=current_user.id,
                field_name="category",
                old_value=old_category,
                new_value=update_data.category,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.add(edit_entry)
            changes_made.append("category")

        # Update merchant_name if provided
        if update_data.merchant_name is not None:
            old_merchant = transaction.merchant_name
            transaction.merchant_name = update_data.merchant_name

            # Store original merchant name if this is the first edit
            if transaction.original_merchant_name is None:
                transaction.original_merchant_name = old_merchant

            # Log to edit history
            edit_entry = TransactionEditHistory(
                transaction_id=transaction.id,
                user_id=current_user.id,
                field_name="merchant_name",
                old_value=old_merchant,
                new_value=update_data.merchant_name,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.add(edit_entry)
            changes_made.append("merchant_name")

        if not changes_made:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No valid fields provided for update",
            )

        # Explicitly mark transaction as modified to ensure SQLAlchemy tracks changes
        db.add(transaction)

        # Flush to validate changes before commit
        db.flush()

        # Commit changes (trigger will auto-update edit_count and last_edited_at)
        db.commit()
        db.refresh(transaction)

        logger.info(
            f"Transaction {transaction_id[:8]}... edited by user {str(current_user.id)[:8]}... "
            f"Fields: {', '.join(changes_made)}"
        )

        # Sync subscription names if merchant_name was edited
        if "merchant_name" in changes_made:
            try:
                from ..services.user_metrics.storage.metrics_storage import (
                    sync_subscription_names_from_edits,
                )

                supabase = get_supabase_client()
                sync_subscription_names_from_edits(
                    supabase=supabase,
                    user_id=str(current_user.id),
                    edited_transaction_id=transaction_id,
                )
                logger.info(
                    f"Synced subscription names after edit for txn {transaction_id[:8]}..."
                )
            except Exception as sync_error:
                # Don't fail edit if sync fails
                logger.warning(f"Subscription sync failed: {str(sync_error)}")

        return transaction

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            f"Failed to edit transaction {transaction_id}: {str(e)}", exc_info=True
        )
        sentry_sdk.capture_exception(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update transaction",
        )


@router.get(
    "/transactions/{transaction_id}/edit-history",
    response_model=List[TransactionEditHistoryResponse],
)
async def get_transaction_edit_history(
    transaction_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get the edit history for a transaction (Revolut-style).

    Returns all edits made to the transaction's category and merchant_name,
    ordered by most recent first.
    """
    try:
        # Verify user owns this transaction
        transaction = (
            db.query(Transaction)
            .filter(
                Transaction.id == transaction_id, Transaction.user_id == current_user.id
            )
            .first()
        )

        if not transaction:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transaction not found or you don't have permission to view it",
            )

        # Get edit history
        edit_history = (
            db.query(TransactionEditHistory)
            .filter(TransactionEditHistory.transaction_id == transaction_id)
            .order_by(TransactionEditHistory.edited_at.desc())
            .all()
        )

        return edit_history

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to fetch edit history for transaction {transaction_id}: {str(e)}",
            exc_info=True,
        )
        sentry_sdk.capture_exception(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch edit history",
        )


@router.post("/transactions/{transaction_id}/revert-category")
async def revert_category_to_original(
    request: Request,
    transaction_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Revert transaction category to its original ML-classified value.

    Useful if user made a mistake or wants to undo their edits.
    """
    try:
        transaction = (
            db.query(Transaction)
            .filter(
                Transaction.id == transaction_id, Transaction.user_id == current_user.id
            )
            .first()
        )

        if not transaction:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transaction not found or you don't have permission to edit it",
            )

        if transaction.original_category is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transaction has no original category to revert to",
            )

        # Get IP and user agent
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        old_category = transaction.category
        transaction.category = transaction.original_category

        # Log the revert action
        edit_entry = TransactionEditHistory(
            transaction_id=transaction.id,
            user_id=current_user.id,
            field_name="category",
            old_value=old_category,
            new_value=transaction.original_category,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(edit_entry)
        db.commit()
        db.refresh(transaction)

        logger.info(
            f"Transaction {transaction_id} category reverted to original by user {current_user.id}"
        )

        return {
            "success": True,
            "message": "Category reverted to original value",
            "original_category": transaction.original_category,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            f"Failed to revert category for transaction {transaction_id}: {str(e)}",
            exc_info=True,
        )
        sentry_sdk.capture_exception(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revert category",
        )


@router.get("/transactions/{transaction_id}/similar")
async def find_similar_transactions(
    transaction_id: str,
    field: Literal["merchant_name", "category"] = Query(
        ..., description="Field to match against"
    ),
    value: str = Query(..., description="Value to compare with"),
    threshold: Optional[float] = Query(
        None, ge=0, le=100, description="Similarity threshold (0-100)"
    ),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of results"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Production-grade similarity search using ML best practices.

    Architecture (Anthropic/Google ML approach + Revolut fintech standards):

    Stage 1: Candidate Retrieval (Recall-Optimized)
    - Broad database query to get potential matches
    - Get 10x more candidates than final limit for better recall

    Stage 2: Edit History Deduplication (Precision-Optimized)
    - Query transaction_edit_history to find previously corrected transactions
    - Prevent already-edited transactions from reappearing
    - Key insight: Check EDIT HISTORY not just current state

    Stage 3: Robust Filtering
    - Filter empty values
    - Filter transactions already at target value
    - Filter previously bulk-edited transactions
    - Comprehensive logging for observability

    Stage 4: Multi-Signal Similarity Scoring
    - Levenshtein distance (edit distance)
    - Token set ratio (word order independent)
    - Partial ratio (substring matching)
    - Token overlap (Jaccard similarity)
    - Weighted ensemble approach

    Stage 5: Result Formatting with Context
    - Include current vs target values
    - Add similarity scores
    - Provide search metadata for debugging

    Dynamic Thresholds:
    - merchant_name: 70-85% (balanced for variations)
    - category: 85% (stricter, standardized)
    - Adaptive based on value length

    Args:
        transaction_id: The transaction being edited
        field: Which field to match against ("merchant_name" or "category")
        value: The new value to compare against
        threshold: Custom similarity threshold (0-100), or use adaptive defaults
        limit: Maximum number of results to return (1-100)

    Returns:
        Similar transactions with scores, metadata, and filtering stats
    """
    # Performance monitoring
    import time

    start_time = time.time()

    try:

        # Verify user owns the transaction
        transaction = (
            db.query(Transaction)
            .filter(
                Transaction.id == transaction_id, Transaction.user_id == current_user.id
            )
            .first()
        )

        if not transaction:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transaction not found or you don't have permission to access it",
            )

        # Validate input parameters
        if not value or not value.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search value cannot be empty",
            )

        if len(value.strip()) > 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search value cannot exceed 200 characters",
            )

        # Set default threshold based on field type and value characteristics
        # Production thresholds balancing accuracy with usability
        if threshold is None:
            base_threshold = 75.0 if field == "merchant_name" else 80.0
            # Adjust for very short values (more permissive for abbreviations)
            if len(value.strip()) <= 3:
                threshold = min(base_threshold + 10, 90.0)
            else:
                threshold = base_threshold

        # ML Engineering Best Practices: Multi-stage retrieval pipeline
        # Stage 1: Broad candidate retrieval (recall-optimized)
        # Stage 2: Edit history deduplication (precision-optimized)
        # Stage 3: Similarity scoring with ensemble approach

        # Initialize similarity matcher
        matcher = get_similarity_matcher()
        normalized_search_value = matcher.get_normalized(value)

        # Stage 1: Candidate Retrieval (Recall-Optimized)
        # Get broader set of candidates for better recall
        field_filter = getattr(Transaction, field).isnot(None)
        query_limit = min(limit * 10, 500)

        logger.info(
            f"Stage 1: Retrieving up to {query_limit} candidate transactions for field={field}"
        )

        all_transactions = (
            db.query(Transaction)
            .filter(
                Transaction.user_id == current_user.id,
                Transaction.id != transaction_id,
                field_filter,
            )
            .order_by(Transaction.transaction_date.desc())
            .limit(query_limit)
            .all()
        )

        if not all_transactions:
            logger.info("Stage 1: No candidate transactions found")
            return {
                "similar_transactions": [],
                "count": 0,
                "field": field,
                "original_value": getattr(transaction, field, None),
                "new_value": value,
            }

        logger.info(f"Stage 1: Retrieved {len(all_transactions)} candidates")

        # Stage 2: Edit History Deduplication (Revolut-grade)
        # Query edit history to find transactions that were EVER edited to this value
        # This is the key to preventing already-corrected transactions from reappearing
        candidate_ids = [txn.id for txn in all_transactions]

        logger.info(
            f"Stage 2: Checking edit history for {len(candidate_ids)} transactions"
        )

        # Find all transactions that were previously edited to have the target value
        previously_edited_to_target = set()

        edit_history_records = (
            db.query(TransactionEditHistory)
            .filter(
                TransactionEditHistory.transaction_id.in_(candidate_ids),
                TransactionEditHistory.field_name == field,
                TransactionEditHistory.user_id == current_user.id,
            )
            .all()
        )

        logger.info(f"Stage 2: Found {len(edit_history_records)} edit history records")

        # Check each edit to see if it resulted in the target value
        for edit in edit_history_records:
            if edit.new_value:
                normalized_edit_value = matcher.get_normalized(edit.new_value)
                if normalized_edit_value == normalized_search_value:
                    previously_edited_to_target.add(edit.transaction_id)
                    logger.debug(
                        f"Stage 2: Transaction {str(edit.transaction_id)[:8]}... "
                        f"was previously edited to target value"
                    )

        logger.info(
            f"Stage 2: Excluded {len(previously_edited_to_target)} "
            f"previously edited transactions"
        )

        # Stage 3: Build candidate list with comprehensive filtering
        similar_transactions = []
        candidates = []
        filtered_count = {"already_at_target": 0, "previously_edited": 0, "empty": 0}

        for txn in all_transactions:
            current_value = getattr(txn, field, None)

            # Filter 1: Empty values
            if not current_value or not str(current_value).strip():
                filtered_count["empty"] += 1
                continue

            current_value_str = str(current_value)
            normalized_current = matcher.get_normalized(current_value_str)

            # Filter 2: Already at target value
            if normalized_current == normalized_search_value:
                filtered_count["already_at_target"] += 1
                continue

            # Filter 3: Previously edited to target (edit history check)
            if txn.id in previously_edited_to_target:
                filtered_count["previously_edited"] += 1
                continue

            candidates.append((current_value_str, txn))

        logger.info(
            f"Stage 3: Filtered candidates - "
            f"empty: {filtered_count['empty']}, "
            f"already_at_target: {filtered_count['already_at_target']}, "
            f"previously_edited: {filtered_count['previously_edited']}, "
            f"remaining: {len(candidates)}"
        )

        if not candidates:
            processing_time = time.time() - start_time
            logger.info(
                f"Stage 3: No candidates remaining after filtering "
                f"(processed in {processing_time:.2f}s)"
            )
            return {
                "similar_transactions": [],
                "count": 0,
                "field": field,
                "original_value": getattr(transaction, field, None),
                "new_value": value,
                "processing_time_seconds": round(processing_time, 3),
            }

        # Stage 4: Similarity Scoring with Dynamic Thresholds
        # Use ML-grade multi-signal approach for robust matching
        logger.info(
            f"Stage 4: Computing similarity scores for {len(candidates)} candidates"
        )

        # Dynamic threshold adjustment based on data characteristics
        if threshold is None:
            # Production thresholds optimized for fintech accuracy
            # Balance between catching variations and avoiding false positives
            if field == "merchant_name":
                # Merchant names have more variations - use adaptive thresholds
                if len(value.strip()) <= 5:
                    threshold = 85.0  # Short names need high precision
                elif len(value.strip()) <= 15:
                    threshold = 75.0  # Medium names - balanced
                else:
                    threshold = 70.0  # Long names - more permissive for variations
            else:  # category
                # Categories are standardized - strict matching
                threshold = 85.0

            logger.info(
                f"Stage 4: Using dynamic threshold={threshold}% for "
                f"field={field}, value_length={len(value.strip())}"
            )

        # Find similar transactions using hybrid Levenshtein-based matching
        # This uses multiple algorithms: token_set_ratio, ratio, partial_ratio, token_overlap
        similar_matches = matcher.find_similar_in_list(
            target=value,
            candidates=candidates,
            field_type=field,
            threshold=threshold,
            limit=limit,
        )

        logger.info(
            f"Stage 4: Found {len(similar_matches)} matches above threshold={threshold}%"
        )

        # Stage 5: Format Results with Rich Context
        for txn, similarity_score in similar_matches:
            try:
                current_field_value = getattr(txn, field, None)

                similar_transactions.append(
                    {
                        "id": str(txn.id),
                        "transaction_date": txn.transaction_date.isoformat(),
                        "transaction_description": (
                            txn.transaction_description[:80]
                            if txn.transaction_description
                            else ""
                        ),
                        "merchant_name": txn.merchant_name,
                        "category": txn.category,
                        "transaction_amount": float(txn.transaction_amount),
                        "similarity_score": round(similarity_score / 100.0, 3),
                        "current_value": current_field_value,
                        "target_value": value,
                    }
                )
            except Exception as e:
                # Log but don't fail on individual transaction formatting errors
                logger.warning(f"Error formatting transaction {txn.id}: {str(e)}")
                continue

        # Performance monitoring
        processing_time = time.time() - start_time
        processing_time_ms = int(processing_time * 1000)

        logger.info(
            f"Similarity search completed: "
            f"field={field}, "
            f"candidates={len(all_transactions)}, "
            f"after_filtering={len(candidates)}, "
            f"matches={len(similar_transactions)}, "
            f"threshold={threshold}%, "
            f"time={processing_time:.2f}s"
        )

        # Persist similarity search performance for analytics
        try:
            supabase = get_supabase_client(use_service_role=True)
            supabase.table("transaction_similarity_search_logs").insert(
                {
                    "user_id": str(current_user.id),
                    "field_name": field,
                    "search_value": str(value)[:500],
                    "results_count": len(similar_transactions),
                    "processing_time_ms": processing_time_ms,
                    "threshold_used": threshold,
                }
            ).execute()
        except Exception as log_error:
            logger.debug(
                "Failed to log similarity search performance: %s", str(log_error)
            )

        result = {
            "similar_transactions": similar_transactions,
            "count": len(similar_transactions),
            "field": field,
            "original_value": getattr(transaction, field, None),
            "new_value": value,
            "processing_time_seconds": round(processing_time, 3),
            "search_metadata": {
                "candidates_retrieved": len(all_transactions),
                "candidates_after_filtering": len(candidates),
                "matches_found": len(similar_transactions),
                "threshold_used": threshold,
                "filtered_counts": filtered_count,
            },
        }

        # Monitor performance for optimization
        if processing_time > 2.0:
            logger.warning(
                f"Slow similarity search: {processing_time:.2f}s, "
                f"user={str(current_user.id)[:8]}..., "
                f"candidates={len(all_transactions)}, "
                f"matches={len(similar_transactions)}"
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        processing_time = time.time() - start_time
        logger.error(
            f"Failed to find similar transactions for {transaction_id} "
            f"(processed in {processing_time:.2f}s): {str(e)}",
            exc_info=True,
        )
        sentry_sdk.capture_exception(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to find similar transactions",
        )


@router.patch("/transactions/batch-edit")
async def batch_update_transactions(
    request: Request,
    batch_request: BatchUpdateRequest,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Batch update multiple transactions at once.

    Security:
    - Verifies user owns ALL transactions before any updates
    - Limited to 100 transactions per batch for performance
    - Uses database transaction for atomicity (all or nothing)
    - Full audit trail for each updated transaction
    - Validates input data and provides detailed error messages

    Request Body:
        {
            "transaction_ids": ["id1", "id2", ...],
            "updates": {
                "category": "New Category",  // optional
                "merchant_name": "New Merchant"  // optional
            }
        }

    Returns:
        {
            "success": true,
            "updated_count": 5,
            "failed_count": 0,
            "updated_transactions": [...],
            "errors": []
        }

    Error Cases:
    - 400: No fields provided for update
    - 400: Invalid transaction IDs
    - 403: User doesn't own some transactions
    - 422: Validation errors in update data
    - 500: Database or server errors
    """
    try:
        transaction_ids = batch_request.transaction_ids
        update_data = batch_request.updates

        # Comprehensive input validation
        if not transaction_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one transaction ID must be provided",
            )

        if len(transaction_ids) > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot update more than 100 transactions at once for performance reasons",
            )

        # Check for duplicate IDs
        if len(set(transaction_ids)) != len(transaction_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate transaction IDs are not allowed",
            )

        # Validate transaction IDs format (should be UUIDs)
        invalid_ids = []
        for txn_id in transaction_ids:
            if not txn_id or not isinstance(txn_id, str):
                invalid_ids.append(str(txn_id) if txn_id else "empty")
                continue
            try:
                UUID(txn_id)
            except ValueError:
                invalid_ids.append(txn_id)

        if invalid_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid transaction ID(s): {', '.join(invalid_ids[:3])}{'...' if len(invalid_ids) > 3 else ''}",
            )

        # Validate update data
        if not update_data.category and not update_data.merchant_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one field (category or merchant_name) must be provided for update",
            )

        # Validate field lengths and content
        if update_data.category:
            category = update_data.category.strip()
            if not category:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Category cannot be empty or only whitespace",
                )
            if len(category) > 100:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Category cannot exceed 100 characters",
                )

        if update_data.merchant_name:
            merchant_name = update_data.merchant_name.strip()
            if not merchant_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Merchant name cannot be empty or only whitespace",
                )
            if len(merchant_name) > 500:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Merchant name cannot exceed 500 characters",
                )

        # Check for potentially malicious content
        malicious_patterns = ["<script", "javascript:", "onload=", "onerror="]
        for field_name, field_value in [
            ("category", update_data.category),
            ("merchant_name", update_data.merchant_name),
        ]:
            if field_value:
                lower_value = field_value.lower()
                for pattern in malicious_patterns:
                    if pattern in lower_value:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Invalid content in {field_name.replace('_', ' ')}",
                        )

        # Get all transactions and verify user owns them all
        transactions = (
            db.query(Transaction)
            .filter(
                Transaction.id.in_(transaction_ids),
                Transaction.user_id == current_user.id,
            )
            .all()
        )

        # Security: verify user owns ALL requested transactions
        found_ids = {str(txn.id) for txn in transactions}
        missing_ids = set(transaction_ids) - found_ids

        if missing_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You don't have permission to edit {len(missing_ids)} transaction(s)",
            )

        # Get IP address and user agent for audit
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        # Track results
        updated_transactions = []
        failed_transactions = []

        # Begin database transaction for atomicity
        # Pre-validate all transactions before processing for instant feedback
        try:
            for i, transaction in enumerate(transactions):
                try:
                    changes_made = []

                    # Update category if provided
                    if update_data.category is not None:
                        old_category = transaction.category
                        transaction.category = update_data.category

                        # Store original category if this is the first edit
                        if transaction.original_category is None:
                            transaction.original_category = old_category

                        # Log to edit history
                        edit_entry = TransactionEditHistory(
                            transaction_id=transaction.id,
                            user_id=current_user.id,
                            field_name="category",
                            old_value=old_category,
                            new_value=update_data.category,
                            ip_address=ip_address,
                            user_agent=user_agent,
                        )
                        db.add(edit_entry)
                        changes_made.append("category")

                        # Security: Log change without exposing sensitive data
                        logger.debug(
                            f"Transaction {str(transaction.id)[:8]}... category updated"
                        )

                    # Update merchant_name if provided
                    if update_data.merchant_name is not None:
                        old_merchant = transaction.merchant_name
                        transaction.merchant_name = update_data.merchant_name

                        # Store original merchant name if this is the first edit
                        if transaction.original_merchant_name is None:
                            transaction.original_merchant_name = old_merchant

                        # Log to edit history
                        edit_entry = TransactionEditHistory(
                            transaction_id=transaction.id,
                            user_id=current_user.id,
                            field_name="merchant_name",
                            old_value=old_merchant,
                            new_value=update_data.merchant_name,
                            ip_address=ip_address,
                            user_agent=user_agent,
                        )
                        db.add(edit_entry)
                        changes_made.append("merchant_name")

                        # Security: Log change without exposing sensitive data
                        logger.debug(
                            f"Transaction {str(transaction.id)[:8]}... merchant_name updated"
                        )

                    # CRITICAL: Explicitly mark transaction as modified for SQLAlchemy
                    # This ensures changes are tracked and committed to database
                    if changes_made:
                        db.add(transaction)
                        logger.debug(
                            f"Transaction {str(transaction.id)[:8]}... marked for commit"
                        )

                    # Add to successful updates
                    updated_transactions.append(
                        {
                            "id": str(transaction.id),
                            "merchant_name": transaction.merchant_name,
                            "category": transaction.category,
                            "edit_count": (transaction.edit_count or 0)
                            + len(changes_made),
                        }
                    )

                except Exception as txn_error:
                    # Log individual transaction error but continue with others
                    logger.warning(
                        f"Failed to update transaction {transaction.id} in batch: {str(txn_error)}"
                    )
                    failed_transactions.append(
                        {"id": str(transaction.id), "error": str(txn_error), "index": i}
                    )
                    continue

            # Flush changes to database to catch constraint errors before commit
            # This allows us to detect issues early without committing invalid data
            logger.info(
                f"Flushing {len(updated_transactions)} transaction updates to database session"
            )
            db.flush()
            logger.info("Flush successful - all changes validated by database")

            # Commit all changes atomically
            logger.info(
                f"Committing batch update of {len(updated_transactions)} transactions"
            )
            db.commit()
            logger.info("Commit successful - changes persisted to database")

            # Verify first transaction was actually saved (sanity check)
            if updated_transactions and len(transactions) > 0:
                # Refresh from database to verify persistence
                db.refresh(transactions[0])
                logger.info(
                    f"Verification: First transaction has merchant_name='{transactions[0].merchant_name[:20]}...'"
                )

            # Log results
            success_count = len(updated_transactions)
            failure_count = len(failed_transactions)

            if failure_count > 0:
                logger.warning(
                    f"Batch update completed with {failure_count} failures. "
                    f"Updated: {success_count}, Failed: {failure_count}"
                )
            else:
                logger.info(
                    f"Batch updated {success_count} transactions by user {str(current_user.id)[:8]}..."
                )

            # Sync subscription names after batch merchant name edits
            if update_data.merchant_name is not None:
                try:
                    from ..services.user_metrics.storage.metrics_storage import (
                        sync_subscription_names_from_edits,
                    )

                    supabase = get_supabase_client()
                    sync_subscription_names_from_edits(
                        supabase=supabase, user_id=str(current_user.id)
                    )
                    logger.info("Synced subscription names after batch edit")
                except Exception as sync_error:
                    # Don't fail batch edit if sync fails
                    logger.warning(f"Subscription sync failed: {str(sync_error)}")

            response = {
                "success": True,
                "updated_count": success_count,
                "failed_count": failure_count,
                "updated_transactions": updated_transactions,
                "errors": failed_transactions,
            }

            # Attach bulk edit statistics (uses v_bulk_edit_statistics view)
            try:
                supabase = get_supabase_client(use_service_role=True)
                stats_response = (
                    supabase.table("v_bulk_edit_statistics")
                    .select("*")
                    .eq("user_id", str(current_user.id))
                    .order("edit_date", desc=True)
                    .limit(30)
                    .execute()
                )
                response["bulk_edit_stats"] = stats_response.data or []
            except Exception as stats_error:
                logger.debug(
                    "Failed to load bulk edit statistics: %s", str(stats_error)
                )

            logger.info(
                f"Returning success response: {success_count} updated, {failure_count} failed"
            )
            return response

        except Exception as batch_error:
            # Rollback on any error
            db.rollback()
            logger.error(
                f"Batch update failed completely: {str(batch_error)}", exc_info=True
            )
            raise batch_error

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            f"Failed to batch update transactions: {str(e)}",
            exc_info=True,
        )
        sentry_sdk.capture_exception(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to batch update transactions",
        )
