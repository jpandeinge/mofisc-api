# api/app/utils/bank_detection.py

import pypdf

from ..utils.logging_config import get_logger

logger = get_logger(__name__)

# Bank display names mapping (internal_name -> display_name)
BANK_DISPLAY_NAMES = {
    "fnb": "FNB",
    "standard_bank": "Standard Bank",
    "nedbank": "Nedbank",
    "bank_windhoek": "Bank Windhoek",
    "unknown": "Unknown Bank",
}

BANK_KEYWORDS = {
    "standard_bank": ["standard bank", "provisional statement"],
    "fnb": ["fnb", "first national bank", "fnbnamibia", "statement period"],
    "nedbank": ["nedbank", "needbank (namibia) limited"],
    "bank_windhoek": ["bank windhoek", "bank windhoek ltd."],
}


def _detect_bank_from_text(first_page: str, last_page: str) -> str:
    for bank, keywords in BANK_KEYWORDS.items():
        if any(keyword in first_page for keyword in keywords) or (
            bank == "nedbank" and any(keyword in last_page for keyword in keywords)
        ):
            return bank
    return "unknown"


def format_bank_name(bank_code: str) -> str:
    """
    Convert internal bank code to proper display name.

    Args:
        bank_code: Internal bank code (e.g., 'fnb', 'standard_bank')

    Returns:
        Properly formatted bank name for display (e.g., 'FNB', 'Standard Bank')
    """
    return BANK_DISPLAY_NAMES.get(bank_code, bank_code.replace("_", " ").title())


def detect_bank_from_pdf(file_path: str) -> str:
    try:
        with open(file_path, "rb") as file:
            pdf_reader = pypdf.PdfReader(file)
            if not pdf_reader.pages:
                return "unknown"

            first_page = (pdf_reader.pages[0].extract_text() or "").lower()
            last_page = (pdf_reader.pages[-1].extract_text() or "").lower()
            detected_bank = _detect_bank_from_text(first_page, last_page)
            if detected_bank != "unknown":
                return detected_bank

            # Scanned statements have no usable pypdf text layer. Classify the
            # document before paying the OCR cost, then inspect only the pages
            # used by the existing bank-identification rules.
            from .hybrid_statement_ocr import is_pdf_scanned, ocr_pdf_text

            if not is_pdf_scanned(file_path):
                return "unknown"

            page_count = len(pdf_reader.pages)
            pages_to_ocr = [1] if page_count == 1 else [1, page_count]
            ocr_text = ocr_pdf_text(file_path, page_numbers=pages_to_ocr).lower()
            return _detect_bank_from_text(ocr_text, ocr_text)
    except Exception as e:
        logger.error(f"Error detecting bank from PDF: {str(e)}")
        return "unknown"
