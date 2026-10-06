"""
Hybrid bank statement extraction with OCR fallback.

This module provides a robust approach to bank statement extraction that handles
both machine-readable PDFs and scanned/image-based PDFs automatically.

The extraction process follows this workflow:
1. Run the existing bank-specific rule extractor first
2. Validate that it returned usable transaction rows
3. On failure, use Firecrawl pdf-inspector to classify the PDF for OCR routing
4. Use Tesseract OCR as the fallback for scanned images or failed text layouts

All extraction functions return standardized pandas DataFrames with consistent
column names and data types across all extraction methods.

Dependencies:
    - pdf-inspector: PDF classification and OCR routing
    - pdfplumber: Direct PDF text extraction
    - pytesseract: Python wrapper for Tesseract OCR
    - pdf2image: PDF to image conversion
    - Pillow: Image processing
    - pandas: Data manipulation
    - Tesseract OCR: System-level OCR engine (install via package manager)

Example:
    >>> from app.utils.hybrid_statement_ocr import extract_fnb_account_statement
    >>> df = extract_fnb_account_statement('path/to/statement.pdf')
    >>> print(df.head())
"""

import logging
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import pdf_inspector
import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from .statement_ocr import (
    extract_bank_windhoek_account_statement as _extract_windhoek_direct,
)
from .statement_ocr import extract_fnb_account_statement as _extract_fnb_direct
from .statement_ocr import extract_nedbank_account_statement as _extract_nedbank_direct
from .statement_ocr import (
    extract_standard_bank_account_statement as _extract_standard_bank_direct,
)

logger = logging.getLogger(__name__)

# Configuration constants
DEFAULT_TEXT_THRESHOLD = 50  # Minimum chars/page to consider PDF as machine-readable
DEFAULT_DPI = 300  # DPI for PDF to image conversion
DEFAULT_TESSERACT_CONFIG = "--oem 1 --psm 4"
REQUIRED_TRANSACTION_COLUMNS = frozenset(
    {
        "transaction_date",
        "transaction_description",
        "transaction_amount",
        "transaction_type",
        "balance",
    }
)


class PDFExtractionError(Exception):
    """Raised when PDF extraction fails."""

    pass


class OCRError(Exception):
    """Raised when OCR processing fails."""

    pass


def is_pdf_scanned(
    pdf_path: str, sample_pages: int = 2, text_threshold: int = DEFAULT_TEXT_THRESHOLD
) -> bool:
    """
    Determine if a PDF contains extractable text or is image-based.

    Uses pdf-inspector's document-wide classification and per-page OCR routing.
    The previous pdfplumber character-count heuristic remains as a safety
    fallback so a pdf-inspector failure cannot break native-text extraction.

    Args:
        pdf_path: Absolute path to the PDF file.
        sample_pages: Number of pages to analyze. Defaults to 2.
        text_threshold: Minimum average characters per page to classify as
            machine-readable. Defaults to 50.

    Returns:
        True if PDF appears to be scanned/image-based, False if machine-readable.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        PDFExtractionError: If PDF cannot be opened or analyzed.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    try:
        inspection = pdf_inspector.detect_pdf(pdf_path)
        pages_needing_ocr = list(inspection.pages_needing_ocr or [])
        has_encoding_issues = bool(getattr(inspection, "has_encoding_issues", False))
        pdf_type = str(inspection.pdf_type).lower()
        requires_ocr = bool(
            pages_needing_ocr
            or has_encoding_issues
            or pdf_type in {"scanned", "image_based", "mixed"}
        )

        logger.info(
            "PDF inspection: path=%s, type=%s, confidence=%.2f, "
            "pages_needing_ocr=%s, encoding_issues=%s, extraction_method=%s",
            pdf_path,
            pdf_type,
            float(inspection.confidence),
            pages_needing_ocr,
            has_encoding_issues,
            "OCR" if requires_ocr else "direct",
        )
        return requires_ocr
    except Exception as inspection_error:
        logger.warning(
            "pdf-inspector could not classify %s; using the legacy text heuristic: %s",
            pdf_path,
            str(inspection_error),
        )

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            if total_pages == 0:
                raise PDFExtractionError(f"PDF has no pages: {pdf_path}")

            pages_to_check = min(sample_pages, total_pages)
            total_text_length = 0

            for i in range(pages_to_check):
                text = pdf.pages[i].extract_text()
                if text:
                    total_text_length += len(text.strip())

            avg_text_per_page = total_text_length / pages_to_check
            is_scanned = avg_text_per_page < text_threshold

            logger.info(
                "PDF analysis: path=%s, avg_chars_per_page=%.0f, "
                "is_scanned=%s, extraction_method=%s",
                pdf_path,
                avg_text_per_page,
                is_scanned,
                "OCR" if is_scanned else "direct",
            )

            return is_scanned

    except Exception as e:
        logger.warning(
            "Error analyzing PDF, defaulting to direct extraction: %s",
            str(e),
            exc_info=True,
        )
        return False


def pdf_to_images(
    pdf_path: str,
    output_dir: Optional[str] = None,
    dpi: int = DEFAULT_DPI,
    page_numbers: Optional[List[int]] = None,
) -> List[str]:
    """
    Convert PDF pages to PNG images for OCR processing.

    Creates an isolated temporary directory and converts each selected page to
    a lossless PNG image suitable for OCR. Isolated directories prevent two
    concurrent uploads from overwriting or deleting each other's pages.

    Args:
        pdf_path: Absolute path to the PDF file.
        output_dir: Directory path for temporary images. A unique temporary
            directory is created when omitted.
        dpi: Resolution for image conversion. Higher values improve OCR accuracy
            but increase processing time. Defaults to 300.
        page_numbers: Optional 1-indexed page numbers to render. All pages are
            rendered when omitted.

    Returns:
        List of absolute paths to created image files.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        OCRError: If PDF to image conversion fails.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    logger.info("Converting PDF to images: path=%s, dpi=%d", pdf_path, dpi)

    owns_output_dir = output_dir is None
    try:
        if output_dir is None:
            output_dir = tempfile.mkdtemp(prefix="galahad-ocr-")
        else:
            Path(output_dir).mkdir(exist_ok=True, parents=True)

        if page_numbers:
            selected_pages = sorted(set(page_numbers))
            if selected_pages[0] < 1:
                raise ValueError("page_numbers must be 1-indexed positive integers")
            rendered_pages = []
            for page_number in selected_pages:
                page_images = convert_from_path(
                    pdf_path,
                    dpi=dpi,
                    first_page=page_number,
                    last_page=page_number,
                    fmt="png",
                )
                rendered_pages.extend((page_number, image) for image in page_images)
        else:
            images = convert_from_path(pdf_path, dpi=dpi, fmt="png")
            rendered_pages = list(enumerate(images, start=1))

        if not rendered_pages:
            raise OCRError(f"No images generated from PDF: {pdf_path}")

        logger.info("Converted %d pages to images", len(rendered_pages))

        image_paths = []
        for page_number, image in rendered_pages:
            image_path = os.path.join(output_dir, f"page_{page_number}.png")
            image.save(image_path, "PNG")
            image_paths.append(image_path)

        return image_paths

    except Exception as e:
        if owns_output_dir and output_dir and os.path.exists(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)
        logger.error("Failed to convert PDF to images: %s", str(e), exc_info=True)
        raise OCRError(f"PDF to image conversion failed: {str(e)}") from e


def _normalize_ocr_text(text: str) -> str:
    """Remove table-border artifacts without changing transaction line breaks."""
    normalized_lines = []
    for raw_line in text.splitlines():
        line = raw_line.translate(
            str.maketrans({"|": " ", "│": " ", "┃": " ", "｜": " "})
        )
        line = re.sub(r"[ \t]+", " ", line).strip()
        normalized_lines.append(line)
    return "\n".join(normalized_lines)


def extract_text_with_ocr(
    image_paths: List[str],
    lang: str = "eng",
    tesseract_config: str = DEFAULT_TESSERACT_CONFIG,
) -> str:
    """
    Extract text from images using Tesseract OCR.

    Processes each image through Tesseract OCR and combines the results into
    a single text string with page breaks.

    Args:
        image_paths: List of absolute paths to image files.
        lang: Tesseract language code. Defaults to 'eng' for English.

    Returns:
        Combined extracted text from all images, with page break markers.

    Raises:
        OCRError: If OCR processing fails for all images.
        FileNotFoundError: If any image file does not exist.
    """
    if not image_paths:
        raise ValueError("No image paths provided for OCR")

    logger.info(
        "Starting OCR extraction: num_images=%d, language=%s", len(image_paths), lang
    )

    all_text = []
    successful_extractions = 0

    for i, image_path in enumerate(image_paths, start=1):
        if not os.path.exists(image_path):
            logger.error("Image file not found: %s", image_path)
            all_text.append("")
            continue

        try:
            logger.debug("Processing image %d/%d: %s", i, len(image_paths), image_path)

            with Image.open(image_path) as image:
                text = pytesseract.image_to_string(
                    image,
                    lang=lang,
                    config=tesseract_config,
                )
            all_text.append(_normalize_ocr_text(text))
            if text.strip():
                successful_extractions += 1

            logger.debug("Extracted %d characters from page %d", len(text), i)

        except Exception as e:
            logger.error(
                "OCR failed for image %s: %s", image_path, str(e), exc_info=True
            )
            all_text.append("")

    if successful_extractions == 0:
        raise OCRError("OCR failed for all images")

    logger.info(
        "OCR extraction complete: successful_pages=%d/%d",
        successful_extractions,
        len(image_paths),
    )

    combined_text = "\n\n--- Page Break ---\n\n".join(all_text)
    return combined_text


def ocr_pdf_text(
    pdf_path: str,
    page_numbers: Optional[List[int]] = None,
    lang: str = "eng",
    dpi: int = DEFAULT_DPI,
) -> str:
    """OCR a PDF and always remove its isolated temporary render directory."""
    image_paths = pdf_to_images(pdf_path, page_numbers=page_numbers, dpi=dpi)
    try:
        return extract_text_with_ocr(image_paths, lang=lang)
    finally:
        cleanup_temp_images(os.path.dirname(image_paths[0]))


def cleanup_temp_images(temp_dir: str) -> None:
    """
    Remove temporary image directory and all contents.

    Args:
        temp_dir: Path to temporary directory to remove.
    """
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.debug("Cleaned up temporary directory: %s", temp_dir)
    except Exception as e:
        logger.warning("Failed to clean up temporary images: %s", str(e))


def _extract_statement_period(text: str) -> Optional[Dict[str, datetime]]:
    """
    Extract statement period dates from text.

    Searches for statement period pattern in format:
    "Statement Period: DD Month YYYY to DD Month YYYY"

    Args:
        text: Extracted text to search.

    Returns:
        Dictionary with 'start' and 'end' datetime objects, or None if not found.
    """
    pattern = r"Statement Period\s*:?\s*(\d+\s+\w+\s+\d{4})\s+to\s+(\d+\s+\w+\s+\d{4})"
    match = re.search(pattern, text, re.IGNORECASE)

    if not match:
        return None

    try:
        start_date = datetime.strptime(match.group(1).strip(), "%d %B %Y")
        end_date = datetime.strptime(match.group(2).strip(), "%d %B %Y")
        return {"start": start_date, "end": end_date}
    except ValueError as e:
        logger.warning("Failed to parse statement period: %s", str(e))
        return None


def _extract_fnb_transaction_summary(
    text: str,
) -> Optional[Dict[str, Dict[str, float]]]:
    """Read FNB's printed credit/debit counts and totals for OCR validation."""
    summary: Dict[str, Dict[str, float]] = {}
    pattern = re.compile(
        r"No\.\s*(Credit|Debit)\s+Transactions\s+(\d+)\s+" r"([\d,]+\.\d{2})\s+(Cr|Dr)",
        re.IGNORECASE,
    )
    for transaction_type, count, amount, _indicator in pattern.findall(text):
        key = transaction_type.lower()
        summary[key] = {
            "count": int(count),
            "amount": float(amount.replace(",", "")),
        }
    return summary if set(summary) == {"credit", "debit"} else None


def _fnb_transactions_match_summary(
    transactions: pd.DataFrame,
    summary: Dict[str, Dict[str, float]],
) -> bool:
    """Return True only when parsed rows reconcile with the statement summary."""
    for transaction_type in ("credit", "debit"):
        rows = transactions[transactions["transaction_type"] == transaction_type]
        expected = summary[transaction_type]
        if len(rows) != int(expected["count"]):
            return False
        if round(float(rows["transaction_amount"].sum()), 2) != round(
            expected["amount"], 2
        ):
            return False
    return True


def _parse_fnb_transactions(
    text: str, statement_period: Optional[Dict[str, datetime]]
) -> pd.DataFrame:
    """
    Parse FNB bank transactions from extracted text.

    Applies regex pattern matching to identify and extract transaction data
    in FNB statement format. Handles date parsing, amount formatting, and
    transaction type classification.

    Args:
        text: Extracted text containing transactions.
        statement_period: Optional statement period for date parsing.

    Returns:
        DataFrame with standardized transaction columns:
        - transaction_date: ISO format date string (YYYY-MM-DD)
        - transaction_description: Transaction description text
        - transaction_amount: Float transaction amount
        - transaction_type: 'debit' or 'credit'
        - balance: Float account balance after transaction
        - Accrued Bank Charges: String representation of charges
    """
    # FNB transaction pattern: Date Description Amount Cr/Dr Balance Cr/Dr Charges
    pattern = (
        r"\n(\d{2} \w{3}) "  # Date: DD Mon
        r"(.*?)"  # Description (non-greedy)
        r"(\d+,\d+\.\d{2}|\d+\.\d{2})"  # Amount
        r"(Cr|Dr)?"  # Optional credit/debit indicator
        r"\s+"  # Whitespace
        r"(\d+,\d+\.\d{2}|\d+\.\d{2})"  # Balance
        r"(Cr|Dr)?"  # Optional balance type
        r"(\s+\d+\.\d{2})?"  # Optional bank charges
    )

    transaction_pattern = re.compile(pattern)
    transactions = transaction_pattern.findall(text)

    if not transactions:
        logger.warning("No transactions found in extracted text")
        return pd.DataFrame(
            columns=[
                "transaction_date",
                "transaction_description",
                "transaction_amount",
                "transaction_type",
                "balance",
                "Accrued Bank Charges",
            ]
        )

    # Create DataFrame from raw transaction data
    df = pd.DataFrame(
        transactions,
        columns=[
            "Date",
            "Description",
            "Amount",
            "Credit/Debit",
            "Balance",
            "Balance Type",
            "Accrued Bank Charges",
        ],
    )

    # Parse and apply year to dates using the same fallback as direct extraction.
    if statement_period:
        start_year = statement_period["start"].year
        end_year = statement_period["end"].year
        start_month = statement_period["start"].month
    else:
        start_year = datetime.now().year
        end_year = start_year
        start_month = 1
        logger.warning("Statement period not found, using current year: %d", start_year)

    def parse_date(date_str: str) -> Optional[datetime]:
        try:
            parsed = datetime.strptime(f"{date_str} {start_year}", "%d %b %Y")
            if statement_period and parsed.month < start_month:
                parsed = parsed.replace(year=end_year)
            return parsed
        except ValueError:
            logger.warning("Failed to parse OCR transaction date: %s", date_str)
            return None

    df["Date"] = df["Date"].apply(parse_date)
    df.dropna(subset=["Date"], inplace=True)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "transaction_date",
                "transaction_description",
                "transaction_amount",
                "transaction_type",
                "balance",
                "Accrued Bank Charges",
            ]
        )
    df.sort_values(by="Date", ascending=False, inplace=True)

    # Clean and format data
    df["Description"] = df["Description"].apply(
        lambda value: re.sub(r"^[\s|/\\]+", "", str(value)).strip() or "Bank Fees"
    )
    df["Amount"] = df["Amount"].str.replace(",", "").astype(float)
    df["Balance"] = df["Balance"].str.replace(",", "").astype(float)

    # Determine transaction type
    df["transaction_type"] = df["Credit/Debit"].apply(
        lambda x: "credit" if x == "Cr" else "debit"
    )

    # Process bank charges
    df["Accrued Bank Charges"] = df["Accrued Bank Charges"].apply(
        lambda x: (
            x.strip()
            if pd.notna(x) and x.strip().replace(",", "").replace(".", "").isdigit()
            else "0.00"
        )
    )

    # Format dates to ISO standard
    df["transaction_date"] = df["Date"].dt.strftime("%Y-%m-%d")

    # Select and rename final columns
    result = df[
        [
            "transaction_date",
            "Description",
            "Amount",
            "transaction_type",
            "Balance",
            "Accrued Bank Charges",
        ]
    ].copy()

    result.rename(
        columns={
            "Description": "transaction_description",
            "Amount": "transaction_amount",
            "Balance": "balance",
        },
        inplace=True,
    )

    result.reset_index(drop=True, inplace=True)

    logger.info("Parsed %d transactions from text", len(result))
    return result


def extract_fnb_account_statement_ocr(pdf_path: str) -> pd.DataFrame:
    """
    Extract FNB bank statement using OCR processing.

    This function is used when the PDF is determined to be scanned or
    image-based. It converts the PDF to images and uses Tesseract OCR
    to extract text before parsing transactions.

    Args:
        pdf_path: Absolute path to FNB PDF statement.

    Returns:
        DataFrame with standardized transaction columns.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        OCRError: If OCR processing fails.
        PDFExtractionError: If transaction parsing fails.
    """
    logger.info("Extracting FNB statement using OCR: path=%s", pdf_path)

    try:

        def extract_at_dpi(dpi: int):
            full_text = ocr_pdf_text(pdf_path, dpi=dpi)
            statement_period = _extract_statement_period(full_text)
            transactions = _parse_fnb_transactions(full_text, statement_period)
            summary = _extract_fnb_transaction_summary(full_text)
            return transactions, summary

        transactions, summary = extract_at_dpi(DEFAULT_DPI)

        if summary and not _fnb_transactions_match_summary(transactions, summary):
            retry_dpi = 400
            logger.warning(
                "FNB OCR did not reconcile with the printed transaction summary; "
                "retrying at %d DPI",
                retry_dpi,
            )
            retry_transactions, retry_summary = extract_at_dpi(retry_dpi)
            expected_summary = retry_summary or summary
            if not _fnb_transactions_match_summary(
                retry_transactions, expected_summary
            ):
                actual_counts = (
                    retry_transactions["transaction_type"].value_counts().to_dict()
                )
                raise PDFExtractionError(
                    "OCR completeness check failed: extracted transaction counts "
                    f"{actual_counts} do not reconcile with {expected_summary}"
                )
            transactions = retry_transactions

        logger.info("Successfully extracted %d transactions via OCR", len(transactions))
        return transactions

    except (FileNotFoundError, OCRError, PDFExtractionError):
        raise
    except Exception as e:
        logger.error(
            "Failed to extract FNB statement with OCR: %s", str(e), exc_info=True
        )
        raise PDFExtractionError(
            f"OCR extraction failed for {pdf_path}: {str(e)}"
        ) from e


def _parse_nedbank_transactions(text: str) -> pd.DataFrame:
    """
    Parse Nedbank transactions from extracted text.

    Nedbank format: DD-MMM-YY DD-MMM-YY REF_NO Description Amount Balance

    Args:
        text: Extracted text containing transactions.

    Returns:
        DataFrame with standardized transaction columns.
    """
    pattern = r"^(\d{2}-[A-Z]{3}-\d{2})\s+(\d{2}-[A-Z]{3}-\d{2})\s+(\S+)\s+(.*)"
    lines = text.split("\n")
    transactions = []
    previous_balance = None

    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(pattern, line)

        if match:
            trans_date, value_date, ref_no, description = match.groups()

            # Combine multi-line descriptions
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if re.match(r"^\d{2}-[A-Z]{3}-\d{2}", next_line):
                    break
                if next_line.startswith("* = inclusive of 15% VAT"):
                    break
                description += " " + next_line.strip()
                i += 1

            # Extract amounts and balance from description
            amounts = re.findall(r"([\d,]+\.\d{2})(?:\*)?", description)

            if len(amounts) >= 2:
                amount = float(amounts[-2].replace(",", ""))
                balance = float(amounts[-1].replace(",", ""))

                # Determine transaction type based on balance change
                if previous_balance is None:
                    transaction_type = (
                        "debit" if "STAMP DUTY" in description else "credit"
                    )
                else:
                    transaction_type = (
                        "credit" if balance > previous_balance else "debit"
                    )

                # Remove amounts from description
                clean_description = re.sub(
                    r"\s+([\d,]+\.\d{2})(?:\*)?", "", description
                ).strip()

                try:
                    parsed_date = datetime.strptime(trans_date, "%d-%b-%y")
                    transactions.append(
                        {
                            "transaction_date": parsed_date.strftime("%Y-%m-%d"),
                            "transaction_description": clean_description,
                            "transaction_amount": amount,
                            "transaction_type": transaction_type.lower(),
                            "balance": balance,
                            "ReferenceNo": ref_no,
                        }
                    )
                    previous_balance = balance
                except ValueError as e:
                    logger.warning(
                        "Failed to parse Nedbank date %s: %s", trans_date, str(e)
                    )

        i += 1

    if not transactions:
        logger.warning("No Nedbank transactions found in extracted text")
        return pd.DataFrame(
            columns=[
                "transaction_date",
                "transaction_description",
                "transaction_amount",
                "transaction_type",
                "balance",
            ]
        )

    df = pd.DataFrame(transactions)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df = df.sort_values("transaction_date", ascending=False).reset_index(drop=True)
    df["transaction_date"] = df["transaction_date"].dt.strftime("%Y-%m-%d")

    # Remove reference number column for final output
    df = df[
        [
            "transaction_date",
            "transaction_description",
            "transaction_amount",
            "transaction_type",
            "balance",
        ]
    ]

    logger.info("Parsed %d Nedbank transactions from text", len(df))
    return df


def _parse_bank_windhoek_transactions(text: str) -> pd.DataFrame:
    """
    Parse Bank Windhoek transactions from extracted text.

    Bank Windhoek format (table-based):
    DD-MM-YYYY ... Description Debit Credit Balance

    Args:
        text: Extracted text containing transactions.

    Returns:
        DataFrame with standardized transaction columns.
    """

    def clean_amount(amount_str: str) -> float:
        """Convert amount string to float, handling comma decimals and brackets."""
        if not amount_str or amount_str.strip() == "":
            return 0.00

        amount_str = amount_str.strip()

        # Handle negative numbers in brackets
        is_negative = False
        if amount_str.startswith("(") and amount_str.endswith(")"):
            is_negative = True
            amount_str = amount_str[1:-1]

        try:
            value = float(amount_str.replace(" ", "").replace(",", "."))
            if is_negative:
                value = -value
            return value
        except ValueError:
            return 0.00

    transactions = []
    lines = text.split("\n")

    for line in lines:
        # Try to match date at start of line
        date_match = re.match(r"^(\d{2}-\d{2}-\d{4})", line.strip())
        if not date_match:
            continue

        # Split line by whitespace and extract fields
        parts = line.split()
        if len(parts) < 4:
            continue

        try:
            date = parts[0]

            # Last 3 parts should be debit, credit, balance
            balance_str = parts[-1]
            credit_str = parts[-2]
            debit_str = parts[-3]

            # Everything between date and amounts is description
            description = " ".join(parts[1:-3])

            # Clean amounts
            debit_amount = abs(clean_amount(debit_str))
            credit_amount = clean_amount(credit_str)
            balance_amount = abs(clean_amount(balance_str))

            # Determine transaction type
            transaction_amount = debit_amount if debit_amount > 0 else credit_amount
            transaction_type = "debit" if debit_amount > 0 else "credit"

            # Parse date
            parsed_date = datetime.strptime(date, "%d-%m-%Y")

            transactions.append(
                {
                    "transaction_date": parsed_date.strftime("%Y-%m-%d"),
                    "transaction_description": description,
                    "transaction_amount": round(transaction_amount, 2),
                    "transaction_type": transaction_type,
                    "balance": round(balance_amount, 2),
                }
            )

        except (ValueError, IndexError) as e:
            logger.debug("Failed to parse Bank Windhoek line: %s", str(e))
            continue

    if not transactions:
        logger.warning("No Bank Windhoek transactions found in extracted text")
        return pd.DataFrame(
            columns=[
                "transaction_date",
                "transaction_description",
                "transaction_amount",
                "transaction_type",
                "balance",
            ]
        )

    df = pd.DataFrame(transactions)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df = df.sort_values("transaction_date", ascending=False).reset_index(drop=True)
    df["transaction_date"] = df["transaction_date"].dt.strftime("%Y-%m-%d")

    logger.info("Parsed %d Bank Windhoek transactions from text", len(df))
    return df


def _parse_standard_bank_transactions(text: str) -> pd.DataFrame:
    """
    Parse Standard Bank transactions from extracted text.

    Standard Bank format (table-based):
    Date | Description | Debit | Credit | Balance
    DD/MM/YYYY | Description text | amount | amount | balance

    Args:
        text: Extracted text containing transactions.

    Returns:
        DataFrame with standardized transaction columns.
    """
    transactions = []
    lines = text.split("\n")

    # Pattern to match Standard Bank transaction lines
    # Format: DD/MM/YYYY Description Debit Credit Balance
    date_pattern = r"(\d{2}/\d{2}/\d{4})"

    for line in lines:
        # Check if line starts with a date
        date_match = re.match(date_pattern, line.strip())
        if not date_match:
            continue

        # Split line by whitespace
        parts = line.split()
        if len(parts) < 4:
            continue

        try:
            date = parts[0]

            # Try to find amounts (numbers with commas and decimals)
            amounts = []
            amount_indices = []

            for i, part in enumerate(parts):
                # Match amounts: 1,234.56 or 1234.56
                if re.match(r"^[\d,]+\.\d{2}$", part):
                    try:
                        clean_amount = float(part.replace(",", ""))
                        amounts.append(clean_amount)
                        amount_indices.append(i)
                    except ValueError:
                        continue

            # We need at least 3 amounts: debit, credit, balance
            # Or 2 if one column is empty
            if len(amounts) < 2:
                continue

            # Last amount is always balance
            balance = amounts[-1]

            # If we have 3 amounts: debit, credit, balance
            # If we have 2 amounts: either (debit, balance) or (credit, balance)
            if len(amounts) >= 3:
                debit_amount = amounts[-3]
                credit_amount = amounts[-2]
            elif len(amounts) == 2:
                # Need to determine if it's debit or credit
                # Typically, if only one amount before balance, check description
                debit_amount = 0.0
                credit_amount = amounts[-2]

            # Description is everything between date and first amount
            if amount_indices:
                desc_parts = parts[1 : amount_indices[0]]
                description = " ".join(desc_parts)
            else:
                description = " ".join(parts[1:])

            # Determine transaction type
            if debit_amount > 0:
                transaction_amount = debit_amount
                transaction_type = "debit"
            elif credit_amount > 0:
                transaction_amount = credit_amount
                transaction_type = "credit"
            else:
                continue

            # Parse date
            parsed_date = datetime.strptime(date, "%d/%m/%Y")

            transactions.append(
                {
                    "transaction_date": parsed_date.strftime("%Y-%m-%d"),
                    "transaction_description": description.strip(),
                    "transaction_amount": round(transaction_amount, 2),
                    "transaction_type": transaction_type.lower(),
                    "balance": round(balance, 2),
                }
            )

        except (ValueError, IndexError) as e:
            logger.debug("Failed to parse Standard Bank line: %s", str(e))
            continue

    if not transactions:
        logger.warning("No Standard Bank transactions found in extracted text")
        return pd.DataFrame(
            columns=[
                "transaction_date",
                "transaction_description",
                "transaction_amount",
                "transaction_type",
                "balance",
            ]
        )

    df = pd.DataFrame(transactions)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df = df.sort_values("transaction_date", ascending=False).reset_index(drop=True)
    df["transaction_date"] = df["transaction_date"].dt.strftime("%Y-%m-%d")

    logger.info("Parsed %d Standard Bank transactions from text", len(df))
    return df


def extract_standard_bank_account_statement_ocr(pdf_path: str) -> pd.DataFrame:
    """
    Extract Standard Bank statement using OCR processing.

    Args:
        pdf_path: Absolute path to Standard Bank PDF statement.

    Returns:
        DataFrame with standardized transaction columns.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        OCRError: If OCR processing fails.
        PDFExtractionError: If transaction parsing fails.
    """
    logger.info("Extracting Standard Bank statement using OCR: path=%s", pdf_path)

    try:
        full_text = ocr_pdf_text(pdf_path)
        transactions = _parse_standard_bank_transactions(full_text)

        logger.info("Successfully extracted %d transactions via OCR", len(transactions))
        return transactions

    except (FileNotFoundError, OCRError):
        raise
    except Exception as e:
        logger.error(
            "Failed to extract Standard Bank statement with OCR: %s",
            str(e),
            exc_info=True,
        )
        raise PDFExtractionError(
            f"OCR extraction failed for {pdf_path}: {str(e)}"
        ) from e


def extract_nedbank_account_statement_ocr(pdf_path: str) -> pd.DataFrame:
    """
    Extract Nedbank statement using OCR processing.

    Args:
        pdf_path: Absolute path to Nedbank PDF statement.

    Returns:
        DataFrame with standardized transaction columns.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        OCRError: If OCR processing fails.
        PDFExtractionError: If transaction parsing fails.
    """
    logger.info("Extracting Nedbank statement using OCR: path=%s", pdf_path)

    try:
        full_text = ocr_pdf_text(pdf_path)
        transactions = _parse_nedbank_transactions(full_text)

        logger.info("Successfully extracted %d transactions via OCR", len(transactions))
        return transactions

    except (FileNotFoundError, OCRError):
        raise
    except Exception as e:
        logger.error(
            "Failed to extract Nedbank statement with OCR: %s", str(e), exc_info=True
        )
        raise PDFExtractionError(
            f"OCR extraction failed for {pdf_path}: {str(e)}"
        ) from e


def extract_bank_windhoek_account_statement_ocr(pdf_path: str) -> pd.DataFrame:
    """
    Extract Bank Windhoek statement using OCR processing.

    Args:
        pdf_path: Absolute path to Bank Windhoek PDF statement.

    Returns:
        DataFrame with standardized transaction columns.

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        OCRError: If OCR processing fails.
        PDFExtractionError: If transaction parsing fails.
    """
    logger.info("Extracting Bank Windhoek statement using OCR: path=%s", pdf_path)

    try:
        full_text = ocr_pdf_text(pdf_path)
        transactions = _parse_bank_windhoek_transactions(full_text)

        logger.info("Successfully extracted %d transactions via OCR", len(transactions))
        return transactions

    except (FileNotFoundError, OCRError):
        raise
    except Exception as e:
        logger.error(
            "Failed to extract Bank Windhoek statement with OCR: %s",
            str(e),
            exc_info=True,
        )
        raise PDFExtractionError(
            f"OCR extraction failed for {pdf_path}: {str(e)}"
        ) from e


def _transaction_result_failure_reason(result: Any) -> Optional[str]:
    """Explain why a rule-based extraction result is unsafe to accept."""
    if not isinstance(result, pd.DataFrame):
        return f"unexpected result type {type(result).__name__}"
    if result.empty:
        return "no transaction rows"

    missing_columns = REQUIRED_TRANSACTION_COLUMNS.difference(result.columns)
    if missing_columns:
        return f"missing required columns {sorted(missing_columns)}"

    required_data = result[list(REQUIRED_TRANSACTION_COLUMNS)]
    if required_data.isnull().any().any():
        return "null values in required transaction fields"

    descriptions = result["transaction_description"].astype(str).str.strip()
    if descriptions.eq("").any():
        return "blank transaction descriptions"

    transaction_types = result["transaction_type"].astype(str).str.lower()
    if not transaction_types.isin({"credit", "debit"}).all():
        return "invalid transaction types"

    for column in ("transaction_amount", "balance"):
        if pd.to_numeric(result[column], errors="coerce").isnull().any():
            return f"non-numeric values in {column}"

    return None


def _extract_with_pdf_inspector(
    pdf_path: str,
    bank_name: str,
    text_parser: Callable[[str], pd.DataFrame],
    result_validator: Optional[Callable[[pd.DataFrame, str], Optional[str]]] = None,
) -> Optional[pd.DataFrame]:
    """Try pdf-inspector's layout-aware text before paying the OCR cost."""
    try:
        inspection = pdf_inspector.process_pdf(pdf_path)
        pages_needing_ocr = list(inspection.pages_needing_ocr or [])
        has_encoding_issues = bool(getattr(inspection, "has_encoding_issues", False))
        pdf_type = str(inspection.pdf_type).lower()
        logger.info(
            "pdf-inspector fallback: bank=%s, path=%s, type=%s, "
            "confidence=%.2f, pages_needing_ocr=%s, encoding_issues=%s",
            bank_name,
            pdf_path,
            pdf_type,
            float(inspection.confidence),
            pages_needing_ocr,
            has_encoding_issues,
        )

        if (
            pages_needing_ocr
            or has_encoding_issues
            or pdf_type in {"scanned", "image_based", "mixed"}
        ):
            return None

        text = _normalize_ocr_text(inspection.markdown or "")
        if not text.strip():
            logger.warning(
                "pdf-inspector returned no usable text for the %s statement",
                bank_name,
            )
            return None

        result = text_parser(text)
        failure_reason = _transaction_result_failure_reason(result)
        if failure_reason is None and result_validator is not None:
            failure_reason = result_validator(result, text)
        if failure_reason is not None:
            logger.warning(
                "pdf-inspector %s extraction was unusable (%s)",
                bank_name,
                failure_reason,
            )
            return None

        logger.info(
            "pdf-inspector recovered %d %s transactions without OCR",
            len(result),
            bank_name,
        )
        return result
    except Exception as inspection_error:
        # A Firecrawl extraction problem must not prevent the OCR fallback.
        logger.warning(
            "pdf-inspector could not extract the %s statement %s; "
            "continuing with OCR: %s",
            bank_name,
            pdf_path,
            str(inspection_error),
        )
        return None


def _validate_fnb_fallback(transactions: pd.DataFrame, text: str) -> Optional[str]:
    """Reject partial Firecrawl FNB results when printed totals are available."""
    summary = _extract_fnb_transaction_summary(text)
    if summary and not _fnb_transactions_match_summary(transactions, summary):
        return "transactions do not reconcile with the printed FNB summary"
    return None


def _extract_with_rule_fallback(
    pdf_path: str,
    bank_name: str,
    rule_extractor: Callable[[], pd.DataFrame],
    inspector_text_parser: Callable[[str], pd.DataFrame],
    ocr_extractor: Callable[[str], pd.DataFrame],
    inspector_result_validator: Optional[
        Callable[[pd.DataFrame, str], Optional[str]]
    ] = None,
    force_ocr: bool = False,
) -> pd.DataFrame:
    """Run rules, then pdf-inspector text recovery, then OCR on failure."""
    if force_ocr:
        logger.info("OCR explicitly requested for %s statement", bank_name)
        return ocr_extractor(pdf_path)

    try:
        rule_result = rule_extractor()
        failure_reason = _transaction_result_failure_reason(rule_result)
        if failure_reason is None:
            logger.info(
                "Rule-based %s extraction succeeded with %d transactions",
                bank_name,
                len(rule_result),
            )
            return rule_result
    except Exception as rule_error:
        failure_reason = f"extractor raised {type(rule_error).__name__}: {rule_error}"

    logger.warning(
        "Rule-based %s extraction failed (%s); invoking pdf-inspector fallback",
        bank_name,
        failure_reason,
    )
    inspector_result = _extract_with_pdf_inspector(
        pdf_path=pdf_path,
        bank_name=bank_name,
        text_parser=inspector_text_parser,
        result_validator=inspector_result_validator,
    )
    if inspector_result is not None:
        return inspector_result

    logger.info("Using Tesseract OCR fallback for the %s statement", bank_name)
    return ocr_extractor(pdf_path)


def extract_fnb_account_statement(
    pdf_path: str, force_ocr: bool = False
) -> pd.DataFrame:
    """
    Extract FNB bank statement with automatic method selection.

    Runs the bank-specific rule extractor first. If it raises an error or
    returns invalid/empty transaction data, pdf-inspector attempts layout-aware
    recovery; scanned or still-unusable results continue through OCR.

    Args:
        pdf_path: Absolute path to FNB PDF statement.
        force_ocr: If True, skip analysis and force OCR extraction.
            Defaults to False.

    Returns:
        DataFrame with standardized transaction columns:
        - transaction_date: ISO format date string (YYYY-MM-DD)
        - transaction_description: Transaction description text
        - transaction_amount: Float transaction amount
        - transaction_type: 'debit' or 'credit'
        - balance: Float account balance after transaction
        - Accrued Bank Charges: String representation of charges

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        PDFExtractionError: If extraction fails.
        OCRError: If OCR processing fails (when using OCR method).
    """
    logger.info("Extracting FNB statement: path=%s, force_ocr=%s", pdf_path, force_ocr)
    return _extract_with_rule_fallback(
        pdf_path=pdf_path,
        bank_name="FNB",
        rule_extractor=lambda: _extract_fnb_direct(pdf_path),
        inspector_text_parser=lambda text: _parse_fnb_transactions(
            text, _extract_statement_period(text)
        ),
        ocr_extractor=extract_fnb_account_statement_ocr,
        inspector_result_validator=_validate_fnb_fallback,
        force_ocr=force_ocr,
    )


def extract_standard_bank_account_statement(
    pdf_path: str, bank_config: Optional[Dict[str, Any]] = None, force_ocr: bool = False
) -> pd.DataFrame:
    """
    Extract Standard Bank statement with automatic method selection.

    Runs the bank-specific rule extractor first. If it raises an error or
    returns invalid/empty transaction data, pdf-inspector attempts layout-aware
    recovery; scanned or still-unusable results continue through OCR.

    Args:
        pdf_path: Absolute path to Standard Bank PDF statement.
        bank_config: Optional configuration dict for direct extraction.
            Only used when direct extraction is selected.
        force_ocr: If True, skip analysis and force OCR extraction.
            Defaults to False.

    Returns:
        DataFrame with standardized transaction columns:
        - transaction_date: ISO format date string (YYYY-MM-DD)
        - transaction_description: Transaction description text
        - transaction_amount: Float transaction amount
        - transaction_type: 'debit' or 'credit'
        - balance: Float account balance after transaction

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        PDFExtractionError: If extraction fails.
        OCRError: If OCR processing fails (when using OCR method).
    """
    logger.info(
        "Extracting Standard Bank statement: path=%s, force_ocr=%s", pdf_path, force_ocr
    )

    def extract_with_rules() -> pd.DataFrame:
        if bank_config is None:
            raise ValueError("bank_config is required for rule-based extraction")
        return _extract_standard_bank_direct(pdf_path, bank_config)

    return _extract_with_rule_fallback(
        pdf_path=pdf_path,
        bank_name="Standard Bank",
        rule_extractor=extract_with_rules,
        inspector_text_parser=_parse_standard_bank_transactions,
        ocr_extractor=extract_standard_bank_account_statement_ocr,
        force_ocr=force_ocr,
    )


def extract_nedbank_account_statement(
    pdf_path: str, force_ocr: bool = False
) -> pd.DataFrame:
    """
    Extract Nedbank statement with automatic method selection.

    Runs the bank-specific rule extractor first. If it raises an error or
    returns invalid/empty transaction data, pdf-inspector attempts layout-aware
    recovery; scanned or still-unusable results continue through OCR.

    Args:
        pdf_path: Absolute path to Nedbank PDF statement.
        force_ocr: If True, skip analysis and force OCR extraction.
            Defaults to False.

    Returns:
        DataFrame with standardized transaction columns:
        - transaction_date: ISO format date string (YYYY-MM-DD)
        - transaction_description: Transaction description text
        - transaction_amount: Float transaction amount
        - transaction_type: 'debit' or 'credit'
        - balance: Float account balance after transaction

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        PDFExtractionError: If extraction fails.
        OCRError: If OCR processing fails (when using OCR method).
    """
    logger.info(
        "Extracting Nedbank statement: path=%s, force_ocr=%s", pdf_path, force_ocr
    )

    return _extract_with_rule_fallback(
        pdf_path=pdf_path,
        bank_name="Nedbank",
        rule_extractor=lambda: _extract_nedbank_direct(pdf_path),
        inspector_text_parser=_parse_nedbank_transactions,
        ocr_extractor=extract_nedbank_account_statement_ocr,
        force_ocr=force_ocr,
    )


def extract_bank_windhoek_account_statement(
    pdf_path: str, force_ocr: bool = False
) -> pd.DataFrame:
    """
    Extract Bank Windhoek statement with automatic method selection.

    Runs the bank-specific rule extractor first. If it raises an error or
    returns invalid/empty transaction data, pdf-inspector attempts layout-aware
    recovery; scanned or still-unusable results continue through OCR.

    Args:
        pdf_path: Absolute path to Bank Windhoek PDF statement.
        force_ocr: If True, skip analysis and force OCR extraction.
            Defaults to False.

    Returns:
        DataFrame with standardized transaction columns:
        - transaction_date: ISO format date string (YYYY-MM-DD)
        - transaction_description: Transaction description text
        - transaction_amount: Float transaction amount
        - transaction_type: 'debit' or 'credit'
        - balance: Float account balance after transaction

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        PDFExtractionError: If extraction fails.
        OCRError: If OCR processing fails (when using OCR method).
    """
    logger.info(
        "Extracting Bank Windhoek statement: path=%s, force_ocr=%s", pdf_path, force_ocr
    )

    return _extract_with_rule_fallback(
        pdf_path=pdf_path,
        bank_name="Bank Windhoek",
        rule_extractor=lambda: _extract_windhoek_direct(pdf_path),
        inspector_text_parser=_parse_bank_windhoek_transactions,
        ocr_extractor=extract_bank_windhoek_account_statement_ocr,
        force_ocr=force_ocr,
    )
