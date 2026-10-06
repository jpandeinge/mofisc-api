# api/app/utils/async_classification.py
"""
Background classification for Cloud Run without Redis.
Uses FastAPI BackgroundTasks with aggressive retries and extended timeouts.

Production Features:
- Automatic HF endpoint wake-up before classification
- Smart retry logic with exponential backoff
- Distinguishes between cold-start (503) and offline endpoint
- Tracks retry attempts to prevent infinite loops
- Sends appropriate user notifications based on failure type
"""

import asyncio
import os
import time
from typing import List

import pandas as pd
import requests
import sentry_sdk

from ..config.settings import settings
from ..sentry import set_sentry_context, set_sentry_tag
from ..services.budgets.budget_alert_service import budget_alert_service
from ..services.notifications.base import NotificationPriority, NotificationType
from ..services.notifications.notification_service import notification_service
from ..utils.category_correction import correct_predictions
from ..utils.inference import unified_preprocess
from ..utils.logging_config import get_logger
from ..utils.supabase_utils import get_supabase_client
from ..utils.transaction_consistency import (
    ClassificationDecision,
    build_safe_classification_update,
    fetch_reviewed_template_overrides,
    harmonize_classification_decisions,
)
from ..utils.transaction_model_endpoint import (
    classify_with_transaction_model,
    transaction_model_backend,
    transaction_model_required,
)
from ..utils.transaction_rule_classifier import (
    classify_transaction_with_rules,
    extract_merchant_candidate,
    transaction_description,
)
from ..utils.transaction_template_lookup import (
    classify_transaction_with_template_lookup,
)

logger = get_logger(__name__)

# HF Endpoint configuration
HF_ENDPOINT_URL = os.environ.get("HF_ENDPOINT_URL")
HF_TOKEN = os.environ.get("HF_TOKEN")


def wake_up_hf_endpoint() -> bool:
    """
    Send a wake-up request to the HF endpoint to start it if it's cold.

    HuggingFace Inference Endpoints automatically pause after inactivity (usually 15 mins).
    This function sends a minimal request to wake them up proactively.

    Returns:
        bool: True if endpoint responded or is waking up, False on error
    """
    if not HF_ENDPOINT_URL or not HF_TOKEN:
        logger.warning("HF_ENDPOINT_URL or HF_TOKEN not configured")
        return False

    try:
        logger.info("Sending wake-up request to HF classification endpoint...")

        headers = {
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "application/json",
        }

        # Send minimal payload to wake up endpoint
        # We don't wait for full classification, just trigger the cold start
        response = requests.post(
            HF_ENDPOINT_URL,
            headers=headers,
            json={"inputs": ["wake up"]},
            timeout=10,  # Short timeout, we just want to trigger wake-up
        )

        # 503 is expected during cold start - endpoint is waking up (good!)
        # 200 means endpoint is already warm (great!)
        if response.status_code in [200, 503]:
            logger.info(
                f"HF endpoint wake-up request successful (status: {response.status_code})"
            )
            return True
        else:
            logger.warning(
                f"HF endpoint wake-up returned unexpected status: {response.status_code}"
            )
            return False

    except requests.exceptions.Timeout:
        # Timeout during wake-up is actually expected and fine
        # It means the endpoint is starting up
        logger.info(
            "HF endpoint wake-up request timed out (expected during cold start)"
        )
        return True
    except requests.exceptions.ConnectionError:
        logger.error("HF endpoint connection error - endpoint may be offline")
        return False
    except Exception as e:
        logger.error(f"Failed to wake up HF endpoint: {str(e)}")
        sentry_sdk.capture_exception(e)
        return False


def classify_transactions_background(
    user_id: str,
    statement_id: str,
    transaction_ids: List[str],
) -> None:
    """
    Background task to classify transactions asynchronously.

    This runs in a FastAPI BackgroundTask, which means:
    - User already got their response (fast UX)
    - We have up to 10 minutes to complete (Cloud Run limit)
    - Multiple retries with exponential backoff for HF cold starts

    Args:
        user_id: User ID who uploaded the statement
        statement_id: Statement ID being processed
        transaction_ids: List of transaction IDs to classify
    """
    try:
        logger.info(
            "Starting background classification",
            extra={
                "user_id": user_id,
                "statement_id": statement_id,
                "transaction_count": len(transaction_ids),
                "transaction_ids": (
                    transaction_ids[:5] if len(transaction_ids) > 5 else transaction_ids
                ),  # Log first 5 IDs
            },
        )

        # Small delay to ensure database commit completes (Cloud Run async timing)
        time.sleep(0.5)

        # Fetch transactions
        supabase = get_supabase_client(use_service_role=True)
        response = (
            supabase.table(settings.SUPABASE_TABLE_NAME)
            .select("*")
            .eq("user_id", user_id)
            .eq("statement_id", statement_id)
            .in_("id", transaction_ids)
            .execute()
        )

        if not response.data:
            logger.warning(
                "No transactions found for classification",
                extra={
                    "user_id": user_id,
                    "statement_id": statement_id,
                    "transaction_ids_searched": (
                        transaction_ids[:5]
                        if len(transaction_ids) > 5
                        else transaction_ids
                    ),
                    "table_name": settings.SUPABASE_TABLE_NAME,
                },
            )
            return

        # Primary path: the selected PFC model backend. It can be the packaged
        # local model or the private HF custom endpoint; both return the same
        # category + merchant + abstention contract.
        local_predictions = None
        try:
            local_predictions = classify_with_transaction_model(response.data)
            if local_predictions:
                logger.info(
                    "Classified with production transaction model",
                    extra={
                        "backend": transaction_model_backend(),
                        "transaction_count": len(local_predictions),
                        "model_version": local_predictions[0].model_version,
                        "taxonomy": local_predictions[0].taxonomy,
                        "vetoed_count": sum(p.vetoed for p in local_predictions),
                        "merchant_unknown_count": sum(
                            p.merchant_unknown for p in local_predictions
                        ),
                    },
                )
        except Exception as exc:
            logger.error(
                "Production transaction model classification failed",
                extra={
                    "backend": transaction_model_backend(),
                    "error_type": type(exc).__name__,
                },
            )
            sentry_sdk.capture_exception(exc)
            local_predictions = None

        if local_predictions is None and transaction_model_required():
            raise RuntimeError(
                "Required Plaid-PFC-aligned transaction model did not produce "
                "predictions; refusing the incompatible legacy classifier fallback"
            )

        if local_predictions is None:
            # Convert to DataFrame and prepare texts
            df = pd.DataFrame(response.data)
            texts = unified_preprocess(df)

            logger.info(
                f"Prepared {len(texts)} transactions for classification",
                extra={"transaction_count": len(texts)},
            )

            # Wake up HF endpoint proactively before classification
            # This gives the endpoint time to cold-start while we prepare
            wake_up_success = wake_up_hf_endpoint()
            if wake_up_success:
                # Give endpoint a moment to start warming up
                time.sleep(2)
            else:
                logger.warning(
                    "HF endpoint wake-up failed - classification may take longer or fail"
                )

            # Classify with extended timeout and retries
            # This handles HF cold starts which can take 60-90 seconds
            categories, scores = classify_with_endpoint_background(texts, batch_size=32)

        # Propose all decisions first, then harmonize by a user-scoped,
        # normalized template. This prevents amount/reference variations in
        # the same merchant description from receiving different labels.
        updated_count = 0
        failed_count = 0
        rule_matches = [
            classify_transaction_with_rules(transaction)
            for transaction in response.data
        ]
        template_matches = [
            classify_transaction_with_template_lookup(transaction)
            for transaction in response.data
        ]
        proposed_decisions: list[ClassificationDecision] = []

        for i, (transaction, rule_match, template_match) in enumerate(
            zip(response.data, rule_matches, template_matches)
        ):
            if local_predictions is not None:
                prediction = local_predictions[i]
                corrected_category = prediction.category
                confidence = prediction.confidence
                category_source = "local_model"
                if prediction.vetoed:
                    confidence = 0.0
                    category_source = "fallback"
                    logger.info(
                        "Merchant-unknown veto applied",
                        extra={
                            "transaction_id": transaction["id"],
                            "model_category": prediction.model_category,
                            "model_version": prediction.model_version,
                            "taxonomy": prediction.taxonomy,
                            "model_confidence": prediction.confidence,
                            "merchant_similarity": prediction.merchant_similarity,
                        },
                    )
                model_merchant = (
                    None if prediction.merchant_unknown else prediction.merchant_name
                )
            else:
                category = categories[i] if i < len(categories) else "Uncategorized"
                confidence = scores[i] if i < len(scores) else 0.0
                correction_row = {
                    "Category": category,
                    "TransactionParticulars": transaction.get(
                        "transaction_description", ""
                    ),
                    "TransactionDescription": transaction.get(
                        "transaction_description", ""
                    ),
                    "TransactionTypeDebitCredit": transaction.get(
                        "transaction_type", ""
                    ),
                    "Channel": transaction.get("channel", ""),
                    "Amount": transaction.get("transaction_amount", 0.0),
                }
                corrected_category = correct_predictions(correction_row)
                category_source = (
                    "rule" if corrected_category != category else "hf_model"
                )
                model_merchant = None

            if template_match and template_match.category:
                corrected_category = template_match.category
                confidence = max(float(confidence), template_match.confidence)
                category_source = "exact_lookup"
            if rule_match and rule_match.category:
                corrected_category = rule_match.category
                confidence = max(float(confidence), rule_match.confidence)
                category_source = "rule"

            extracted_merchant = extract_merchant_candidate(
                transaction_description(transaction)
            )
            if rule_match and rule_match.merchant_name:
                merchant_name = rule_match.merchant_name
                merchant_source = "rule"
            elif template_match and template_match.merchant_name:
                merchant_name = template_match.merchant_name
                merchant_source = "exact_lookup"
            elif model_merchant:
                merchant_name = model_merchant
                merchant_source = "local_model"
            else:
                merchant_name = extracted_merchant
                merchant_source = "extracted" if extracted_merchant else "unknown"

            proposed_decisions.append(
                ClassificationDecision(
                    category=corrected_category,
                    confidence=float(confidence),
                    merchant_name=merchant_name,
                    category_source=category_source,
                    merchant_source=merchant_source,
                )
            )

        try:
            reviewed_overrides, edited_pairs = fetch_reviewed_template_overrides(
                supabase, user_id=user_id
            )
            review_history_available = True
        except Exception as correction_error:
            reviewed_overrides = {}
            edited_pairs = set()
            review_history_available = False
            logger.warning(
                "Could not load reviewed transaction corrections; "
                "existing classified values will be preserved for safety: "
                f"{correction_error}"
            )
        decisions = harmonize_classification_decisions(
            response.data, proposed_decisions, reviewed_overrides
        )

        for transaction, decision in zip(response.data, decisions):
            try:
                old_category = transaction.get("category")
                existing_merchant = transaction.get("merchant_name")
                update_payload = build_safe_classification_update(
                    transaction,
                    decision,
                    edited_pairs=edited_pairs,
                    review_history_available=review_history_available,
                )

                if not update_payload:
                    continue

                # Compare-and-set protects an edit made after the initial fetch:
                # if either old value changed, the whole automatic update is
                # skipped and the user's newer value wins.
                update_query = (
                    supabase.table(settings.SUPABASE_TABLE_NAME)
                    .update(update_payload)
                    .eq("id", transaction["id"])
                    .eq("user_id", user_id)
                )
                if "category" in update_payload:
                    if old_category is None:
                        update_query = update_query.is_("category", "null")
                    else:
                        update_query = update_query.eq("category", old_category)
                if "merchant_name" in update_payload:
                    if existing_merchant is None:
                        update_query = update_query.is_("merchant_name", "null")
                    else:
                        update_query = update_query.eq(
                            "merchant_name", existing_merchant
                        )
                update_query.execute()
                updated_count += 1
            except Exception as e:
                logger.error(
                    f"Failed to update transaction {transaction['id']}: {e}",
                    extra={
                        "transaction_id": transaction["id"],
                        "category_source": decision.category_source,
                        "merchant_source": decision.merchant_source,
                    },
                )
                failed_count += 1

        logger.info(
            "Background classification completed",
            extra={
                "user_id": user_id,
                "statement_id": statement_id,
                "updated_count": updated_count,
                "failed_count": failed_count,
                "total_count": len(transaction_ids),
            },
        )

        # Update Sentry
        set_sentry_context(
            "background_classification",
            {
                "user_id": user_id,
                "statement_id": statement_id,
                "updated_count": updated_count,
                "failed_count": failed_count,
            },
        )

        # Send push notification to user that classification is complete
        try:
            asyncio.run(
                notification_service.send_notification(
                    user_id=user_id,
                    notification_type=NotificationType.TRANSACTION_UPDATED,
                    data={
                        "statement_id": statement_id,
                        "classified_count": updated_count,
                        "total_count": len(transaction_ids),
                        "description": f"Classified {updated_count} of {len(transaction_ids)} transactions",
                    },
                    priority=NotificationPriority.MEDIUM,
                )
            )
        except Exception as e:
            logger.error(f"Failed to send classification notification: {e}")

        # Re-check budgets after categories are finalized
        try:
            asyncio.run(budget_alert_service.check_and_notify_for_user(user_id))
        except Exception as e:
            logger.error(f"Failed to check budget alerts after classification: {e}")

    except Exception as exc:
        logger.error(
            "Background classification failed",
            extra={
                "user_id": user_id,
                "statement_id": statement_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )

        # Complete failure: resolve the stuck "Processing..." rows with the
        # rule classifier (local, cannot fail) so no transaction is ever left
        # in a "Classification Failed" state — unmatched rows become
        # "Uncategorized" and stay eligible for a later retry.
        try:
            supabase = get_supabase_client(use_service_role=True)
            stuck = (
                supabase.table(settings.SUPABASE_TABLE_NAME)
                .select("*")
                .eq("user_id", user_id)
                .eq("statement_id", statement_id)
                .in_("id", transaction_ids)
                .eq("category", "Processing...")
                .execute()
            )
            for transaction in stuck.data or []:
                fallback_payload = {"category": "Uncategorized", "confidence": 0.0}
                try:
                    rule_match = classify_transaction_with_rules(transaction)
                    if rule_match and rule_match.category:
                        fallback_payload = {
                            "category": rule_match.category,
                            "confidence": rule_match.confidence,
                        }
                        if rule_match.merchant_name and not transaction.get(
                            "merchant_name"
                        ):
                            fallback_payload["merchant_name"] = rule_match.merchant_name
                            fallback_payload["original_merchant_name"] = (
                                rule_match.merchant_name
                            )
                except Exception:
                    pass  # keep the Uncategorized fallback
                supabase.table(settings.SUPABASE_TABLE_NAME).update(
                    fallback_payload
                ).eq("id", transaction["id"]).execute()
        except Exception as e:
            logger.error(f"Failed to resolve stuck transactions after failure: {e}")

        # Report to Sentry
        set_sentry_tag("background_classification_failed", "true")
        set_sentry_context(
            "background_classification_error",
            {
                "user_id": user_id,
                "statement_id": statement_id,
                "error": str(exc),
            },
        )
        sentry_sdk.capture_exception(exc)

        # Send push notification about failure
        try:
            asyncio.run(
                notification_service.send_notification(
                    user_id=user_id,
                    notification_type=NotificationType.SYSTEM_ALERT,
                    data={
                        "statement_id": statement_id,
                        "description": "Transaction classification failed. Please try uploading again.",
                        "error": "Classification service unavailable",
                    },
                    priority=NotificationPriority.HIGH,
                )
            )
        except Exception as e:
            logger.error(f"Failed to send failure notification: {e}")


def classify_with_endpoint_background(texts: List[str], batch_size: int = 32):
    """
    Classify texts using HF endpoint with background-optimized settings.

    Differences from regular classification:
    - Automatic wake-up requests on retry attempts
    - Longer timeouts (120s for first batch to handle cold starts)
    - More retries (up to 5 attempts)
    - Exponential backoff (2s → 4s → 8s → 16s → 32s)
    - Total max wait: ~3 minutes for cold start

    This is safe because:
    - User already got their response
    - We have 10 minutes total in Cloud Run
    - Better to wait than return "Uncategorized"
    """
    if not HF_ENDPOINT_URL:
        logger.error("HF_ENDPOINT_URL not set - cannot classify")
        return ["Uncategorized"] * len(texts), [0.0] * len(texts)

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }

    all_categories = []
    all_scores = []

    # Process in batches
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i : i + batch_size]
        payload = {"inputs": batch_texts}

        # More aggressive retry for background processing
        max_retries = 5 if i == 0 else 2  # First batch gets more retries
        retry_delay = 2  # Start with 2 seconds
        success = False

        for attempt in range(max_retries):
            try:
                # Much longer timeout for background (we have time to wait)
                # First batch: 120s (handles 90s+ cold starts)
                # Subsequent: 30s (endpoint should be warm)
                timeout = 120 if i == 0 else 30

                logger.info(
                    f"Attempting HF classification (batch {i//batch_size + 1}, attempt {attempt + 1}/{max_retries}, timeout={timeout}s)"
                )

                response = requests.post(
                    HF_ENDPOINT_URL, headers=headers, json=payload, timeout=timeout
                )
                response.raise_for_status()

                batch_results = response.json()

                # Extract predictions
                for result in batch_results:
                    all_categories.append(result["label"])
                    all_scores.append(result["score"])

                success = True
                logger.info(f"Batch {i//batch_size + 1} classified successfully")
                break  # Success, exit retry loop

            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 503:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"HF endpoint cold-starting (batch {i//batch_size + 1}, attempt {attempt + 1}/{max_retries}). "
                            f"Sending wake-up request and retrying in {retry_delay}s..."
                        )
                        # Send another wake-up request to help endpoint start faster
                        wake_up_hf_endpoint()
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        continue
                raise

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"HF endpoint timeout (batch {i//batch_size + 1}, attempt {attempt + 1}/{max_retries}). "
                        f"Sending wake-up request and retrying in {retry_delay}s..."
                    )
                    # Endpoint might be cold - send wake-up
                    wake_up_hf_endpoint()
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                raise

            except requests.exceptions.ConnectionError:
                if attempt < max_retries - 1:
                    logger.error(
                        f"HF endpoint connection error (batch {i//batch_size + 1}, attempt {attempt + 1}/{max_retries}). "
                        f"Endpoint may be offline. Retrying in {retry_delay}s..."
                    )
                    # Still try wake-up in case it helps
                    wake_up_hf_endpoint()
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                raise

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"HF request error (batch {i//batch_size + 1}, attempt {attempt + 1}/{max_retries}): {str(e)}. "
                        f"Retrying in {retry_delay}s..."
                    )
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                raise

        # If all retries failed, mark as Uncategorized
        if not success:
            logger.error(
                f"All retries failed for batch {i//batch_size + 1} - marking as Uncategorized"
            )
            all_categories.extend(["Uncategorized"] * len(batch_texts))
            all_scores.extend([0.0] * len(batch_texts))

    return all_categories, all_scores
