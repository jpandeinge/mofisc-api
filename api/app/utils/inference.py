# api/app/utils/inference.py

import logging
import os
import time

import pandas as pd
import requests
import sentry_sdk

from ..sentry import set_sentry_context, set_sentry_tag

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.95

# Hugging Face Endpoint configuration
HF_ENDPOINT_URL = os.environ.get("HF_ENDPOINT_URL")
HF_TOKEN = os.environ.get("HF_TOKEN")


def unified_preprocess(data):
    """
    Preprocess data to create combined_text field for classification.
    """
    data = data.copy()
    data.columns = [col.lower() for col in data.columns]

    if "combined_text" not in data.columns:
        if "transactionparticulars" in data.columns:
            data["transactionparticulars"] = (
                data["transactionparticulars"].astype(str).str.lower()
            )
            data["transactiontypedebitcredit"] = data[
                "transactiontypedebitcredit"
            ].replace({"c": "credit", "d": "debit"})
            data["combined_text"] = (
                data["transactionparticulars"]
                + " [SEP] "
                + data["transactiontypedebitcredit"]
            )
        elif (
            "transaction_description" in data.columns
            and "transaction_type" in data.columns
        ):
            data["transaction_description"] = (
                data["transaction_description"].astype(str).str.lower()
            )
            data["transaction_type"] = data["transaction_type"].astype(str).str.lower()
            data["combined_text"] = (
                data["transaction_description"] + " [SEP] " + data["transaction_type"]
            )
        else:
            data["combined_text"] = "unknown transaction [SEP] unknown type"

    return data["combined_text"].tolist()


def classify_with_endpoint(texts, batch_size=32):
    """
    Classify texts using the Hugging Face Inference Endpoint.

    Args:
        texts: List of text strings to classify
        batch_size: Number of texts to send per API request

    Returns:
        Tuple of (categories, scores)
    """
    if not HF_ENDPOINT_URL:
        raise ValueError("HF_ENDPOINT_URL environment variable is not set")

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

        # Retry logic for cold starts (503 errors)
        # CRITICAL: Must stay under Cloud Run 300s timeout even with large statements
        # Assuming: 30s PDF extraction + 270s for classification
        # For 5 batches (350 txns): 270s / 5 = 54s per batch max
        # Only retry first batch (cold start), subsequent batches should be warm
        max_retries = 2 if i == 0 else 1  # Retry only first batch
        retry_delay = 2  # seconds
        success = False

        for attempt in range(max_retries):
            try:
                # Balanced timeouts: handle cold starts while staying under 300s limit
                # First batch: 90s (with retry = 182s max, handles slow cold starts)
                # Subsequent: 15s (endpoint warm, 5s typical response)
                # 5 batches: 182s + 15s×4 = 242s classification
                # Total with overhead: 30s PDF + 242s + 20s DB = 292s << 300s
                timeout = 90 if i == 0 else 15

                response = requests.post(
                    HF_ENDPOINT_URL, headers=headers, json=payload, timeout=timeout
                )
                response.raise_for_status()

                batch_results = response.json()

                # Extract predictions - format: [{"label": "category_name", "score": 0.95}, ...]
                for result in batch_results:
                    all_categories.append(result["label"])
                    all_scores.append(result["score"])

                success = True
                break  # Success, exit retry loop

            except requests.exceptions.HTTPError as e:
                # Check if it's a 503 (service unavailable - likely cold start)
                if e.response is not None and e.response.status_code == 503:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"HF endpoint unavailable (batch {i//batch_size}, attempt {attempt + 1}/{max_retries}). "
                            f"Endpoint may be cold-starting. Retrying in {retry_delay}s..."
                        )
                        time.sleep(retry_delay)
                        continue
                # Re-raise if not 503 or last attempt
                raise

            except requests.exceptions.RequestException:
                # For non-HTTP errors (timeout, connection), retry once
                if attempt < max_retries - 1:
                    logger.warning(
                        f"HF endpoint connection error (batch {i//batch_size}, attempt {attempt + 1}/{max_retries}). "
                        f"Retrying in {retry_delay}s..."
                    )
                    time.sleep(retry_delay)
                    continue
                # Re-raise if last attempt
                raise

        # If all retries failed, handle the error
        if not success:
            try:
                # This will re-raise the last exception caught
                raise
            except requests.exceptions.RequestException as e:
                # Log error with Sentry context (without exposing sensitive data)
                set_sentry_tag("hf_classification_failed", "true")
                set_sentry_context(
                    "hf_error",
                    {
                        "batch_number": i // batch_size,
                        "batch_size": len(batch_texts),
                        "error_type": type(e).__name__,
                        "has_response": hasattr(e, "response")
                        and e.response is not None,
                    },
                )

                if hasattr(e, "response") and e.response is not None:
                    set_sentry_context(
                        "hf_response",
                        {
                            "status_code": e.response.status_code,
                            "response_length": (
                                len(e.response.text) if e.response.text else 0
                            ),
                        },
                    )
                    # Only log error message, not full response body (may contain sensitive data)
                    logger.error(
                        f"HF endpoint error (batch {i//batch_size}): {e.response.status_code}"
                    )
                else:
                    logger.error(
                        f"HF endpoint connection error (batch {i//batch_size}): {str(e)}"
                    )

                # Capture exception in Sentry
                sentry_sdk.capture_exception(e)

                # Return "Uncategorized" for failed batches
                all_categories.extend(["Uncategorized"] * len(batch_texts))
                all_scores.extend([0.0] * len(batch_texts))

    return all_categories, all_scores


def process_data_in_chunks(dataframe, chunksize=70):
    """
    Process dataframe in chunks using the HF endpoint for classification.

    Args:
        dataframe: Input pandas DataFrame
        chunksize: Number of rows to process per chunk

    Returns:
        DataFrame with Category and Confidence columns added
    """
    processed_results = []
    logger.info(
        f"Processing {len(dataframe)} transactions in chunks using HF endpoint..."
    )

    num_chunks = len(dataframe) // chunksize + (1 if len(dataframe) % chunksize else 0)

    for i in range(num_chunks):
        chunk_start = i * chunksize
        chunk_end = (i + 1) * chunksize
        chunk = dataframe.iloc[chunk_start:chunk_end]

        try:
            # Preprocess to get combined text
            texts = unified_preprocess(chunk)

            # Call the endpoint for classification
            categories, scores = classify_with_endpoint(texts, batch_size=32)

            # Add predictions to chunk
            chunk = chunk.copy()
            chunk.loc[:, "Category"] = categories
            chunk.loc[:, "Confidence"] = scores
            processed_results.append(chunk)

            logger.info(
                f"Processed chunk {i+1}/{num_chunks} ({len(chunk)} transactions)"
            )

        except Exception as e:
            # Log error with Sentry context
            set_sentry_tag("chunk_processing_failed", "true")
            set_sentry_context(
                "chunk_error",
                {
                    "chunk_number": i + 1,
                    "total_chunks": num_chunks,
                    "chunk_size": len(chunk),
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                },
            )

            logger.error(
                f"Error processing chunk {i+1}/{num_chunks}: {type(e).__name__}: {str(e)}"
            )

            # Capture exception in Sentry
            sentry_sdk.capture_exception(e)

            chunk = chunk.copy()
            chunk["Category"] = "Uncategorized"
            chunk["Confidence"] = 0.0
            processed_results.append(chunk)

    if not processed_results:
        raise ValueError("No data was successfully processed")

    return pd.concat(processed_results, ignore_index=True)
