"""Regression tests for pdf-inspector routing and OCR statement extraction."""

import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pdf_inspector
import pytest

from api.app.utils.bank_detection import detect_bank_from_pdf
from api.app.utils.hybrid_statement_ocr import (
    _extract_statement_period,
    _extract_fnb_transaction_summary,
    _fnb_transactions_match_summary,
    _normalize_ocr_text,
    _parse_fnb_transactions,
    extract_bank_windhoek_account_statement,
    extract_fnb_account_statement,
    extract_fnb_account_statement_ocr,
    extract_nedbank_account_statement,
    extract_standard_bank_account_statement,
    is_pdf_scanned,
)
from api.app.utils.statement_ocr import (
    extract_fnb_account_statement as extract_fnb_direct,
)


def _inspection(
    pdf_type="text_based",
    pages_needing_ocr=None,
    has_encoding_issues=False,
    markdown=None,
):
    return SimpleNamespace(
        pdf_type=pdf_type,
        confidence=1.0,
        pages_needing_ocr=pages_needing_ocr or [],
        has_encoding_issues=has_encoding_issues,
        markdown=markdown,
    )


def _valid_transactions():
    return pd.DataFrame(
        [
            {
                "transaction_date": "2025-02-01",
                "transaction_description": "Test",
                "transaction_amount": 1.0,
                "transaction_type": "debit",
                "balance": 2.0,
            }
        ]
    )


@pytest.mark.unit
def test_pdf_inspector_keeps_native_text_on_direct_path():
    with (
        patch("api.app.utils.hybrid_statement_ocr.os.path.exists", return_value=True),
        patch(
            "api.app.utils.hybrid_statement_ocr.pdf_inspector.detect_pdf",
            return_value=_inspection(),
        ),
    ):
        assert is_pdf_scanned("statement.pdf") is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("pdf_type", "pages", "encoding_issues"),
    [
        ("scanned", [1], False),
        ("image_based", [1], False),
        ("mixed", [2], False),
        ("text_based", [], True),
    ],
)
def test_pdf_inspector_routes_unsafe_text_to_ocr(
    pdf_type, pages, encoding_issues
):
    with (
        patch("api.app.utils.hybrid_statement_ocr.os.path.exists", return_value=True),
        patch(
            "api.app.utils.hybrid_statement_ocr.pdf_inspector.detect_pdf",
            return_value=_inspection(pdf_type, pages, encoding_issues),
        ),
    ):
        assert is_pdf_scanned("statement.pdf") is True


@pytest.mark.unit
def test_pdf_inspector_failure_preserves_legacy_text_heuristic():
    page = MagicMock()
    page.extract_text.return_value = "x" * 100
    pdf = MagicMock()
    pdf.pages = [page]
    pdf.__enter__.return_value = pdf
    pdf.__exit__.return_value = False

    with (
        patch("api.app.utils.hybrid_statement_ocr.os.path.exists", return_value=True),
        patch(
            "api.app.utils.hybrid_statement_ocr.pdf_inspector.detect_pdf",
            side_effect=RuntimeError("inspection failed"),
        ),
        patch("api.app.utils.hybrid_statement_ocr.pdfplumber.open", return_value=pdf),
    ):
        assert is_pdf_scanned("statement.pdf") is False


@pytest.mark.unit
def test_ocr_table_borders_are_normalized_before_fnb_parsing():
    raw_text = """
    Statement Period : 31 January 2025 to 28 February 2025
    06 Feb |Pay 2 Cell Credit Yango 1,600.00Cr| 934.86Cr
    07 Feb |Pos Purchase Checkers 125.26| 697.60Cr
    No. Credit Transactions 1 1,600.00 Cr
    No. Debit Transactions 1 125.26 Dr
    """
    text = _normalize_ocr_text(raw_text)
    result = _parse_fnb_transactions(text, _extract_statement_period(text))
    summary = _extract_fnb_transaction_summary(text)

    assert len(result) == 2
    assert result["transaction_date"].tolist() == ["2025-02-07", "2025-02-06"]
    assert result["transaction_type"].tolist() == ["debit", "credit"]
    assert result["transaction_amount"].tolist() == [125.26, 1600.0]
    assert result["balance"].tolist() == [697.60, 934.86]
    assert all(not value.startswith("|") for value in result["transaction_description"])
    assert summary is not None
    assert _fnb_transactions_match_summary(result, summary)


@pytest.mark.unit
def test_fnb_ocr_retries_before_returning_a_partial_statement():
    incomplete = _normalize_ocr_text("""
    Statement Period : 31 January 2025 to 28 February 2025
    06 Feb Pay 2 Cell Credit Yango 1,600.00Cr 934.86Cr
    No. Credit Transactions 1 1,600.00 Cr
    No. Debit Transactions 1 125.26 Dr
    """)
    complete = incomplete.replace(
        "No. Credit Transactions",
        "07 Feb Pos Purchase Checkers 125.26 697.60Cr\n"
        "No. Credit Transactions",
    )

    with patch(
        "api.app.utils.hybrid_statement_ocr.ocr_pdf_text",
        side_effect=[incomplete, complete],
    ) as ocr:
        result = extract_fnb_account_statement_ocr("statement.pdf")

    assert len(result) == 2
    assert ocr.call_args_list[0].kwargs == {"dpi": 300}
    assert ocr.call_args_list[1].kwargs == {"dpi": 400}


@pytest.mark.unit
def test_hybrid_fnb_wrapper_preserves_direct_extractor_contract():
    expected = _valid_transactions()

    with (
        patch(
            "api.app.utils.hybrid_statement_ocr._extract_fnb_direct",
            return_value=expected,
        ) as direct,
        patch(
            "api.app.utils.hybrid_statement_ocr.pdf_inspector.process_pdf"
        ) as inspector,
        patch(
            "api.app.utils.hybrid_statement_ocr.extract_fnb_account_statement_ocr"
        ) as ocr,
    ):
        result = extract_fnb_account_statement("statement.pdf")

    pd.testing.assert_frame_equal(result, expected)
    direct.assert_called_once_with("statement.pdf")
    inspector.assert_not_called()
    ocr.assert_not_called()


@pytest.mark.unit
def test_force_ocr_bypasses_rules_and_pdf_inspector():
    expected = _valid_transactions()

    with (
        patch("api.app.utils.hybrid_statement_ocr._extract_fnb_direct") as direct,
        patch(
            "api.app.utils.hybrid_statement_ocr.pdf_inspector.process_pdf"
        ) as inspector,
        patch(
            "api.app.utils.hybrid_statement_ocr.extract_fnb_account_statement_ocr",
            return_value=expected,
        ) as ocr,
    ):
        result = extract_fnb_account_statement("statement.pdf", force_ocr=True)

    pd.testing.assert_frame_equal(result, expected)
    direct.assert_not_called()
    inspector.assert_not_called()
    ocr.assert_called_once_with("statement.pdf")


@pytest.mark.unit
def test_empty_rule_result_uses_pdf_inspector_then_ocr():
    expected = _valid_transactions()

    with (
        patch(
            "api.app.utils.hybrid_statement_ocr._extract_fnb_direct",
            return_value=pd.DataFrame(),
        ),
        patch(
            "api.app.utils.hybrid_statement_ocr.pdf_inspector.process_pdf",
            return_value=_inspection("scanned", [1]),
        ) as inspector,
        patch(
            "api.app.utils.hybrid_statement_ocr.extract_fnb_account_statement_ocr",
            return_value=expected,
        ) as ocr,
    ):
        result = extract_fnb_account_statement("statement.pdf")

    pd.testing.assert_frame_equal(result, expected)
    inspector.assert_called_once_with("statement.pdf")
    ocr.assert_called_once_with("statement.pdf")


@pytest.mark.unit
def test_pdf_inspector_text_is_the_first_fallback_after_rules_fail():
    expected = _valid_transactions()
    inspector_text = "01 Feb Test 1.00 2.00"

    with (
        patch(
            "api.app.utils.hybrid_statement_ocr._extract_fnb_direct",
            return_value=pd.DataFrame(),
        ),
        patch(
            "api.app.utils.hybrid_statement_ocr.pdf_inspector.process_pdf",
            return_value=_inspection("text_based", markdown=inspector_text),
        ) as inspector,
        patch(
            "api.app.utils.hybrid_statement_ocr._parse_fnb_transactions",
            return_value=expected,
        ) as parser,
        patch(
            "api.app.utils.hybrid_statement_ocr.extract_fnb_account_statement_ocr"
        ) as ocr,
    ):
        result = extract_fnb_account_statement("statement.pdf")

    pd.testing.assert_frame_equal(result, expected)
    inspector.assert_called_once_with("statement.pdf")
    parser.assert_called_once()
    ocr.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("extractor", "direct_target", "ocr_target", "kwargs"),
    [
        (
            extract_fnb_account_statement,
            "api.app.utils.hybrid_statement_ocr._extract_fnb_direct",
            "api.app.utils.hybrid_statement_ocr.extract_fnb_account_statement_ocr",
            {},
        ),
        (
            extract_standard_bank_account_statement,
            "api.app.utils.hybrid_statement_ocr._extract_standard_bank_direct",
            "api.app.utils.hybrid_statement_ocr.extract_standard_bank_account_statement_ocr",
            {"bank_config": {"column_names": []}},
        ),
        (
            extract_nedbank_account_statement,
            "api.app.utils.hybrid_statement_ocr._extract_nedbank_direct",
            "api.app.utils.hybrid_statement_ocr.extract_nedbank_account_statement_ocr",
            {},
        ),
        (
            extract_bank_windhoek_account_statement,
            "api.app.utils.hybrid_statement_ocr._extract_windhoek_direct",
            "api.app.utils.hybrid_statement_ocr.extract_bank_windhoek_account_statement_ocr",
            {},
        ),
    ],
)
def test_rule_exceptions_route_every_bank_through_pdf_inspector_ocr_fallback(
    extractor, direct_target, ocr_target, kwargs
):
    expected = _valid_transactions()

    with (
        patch(direct_target, side_effect=ValueError("unsupported layout")) as direct,
        patch(
            "api.app.utils.hybrid_statement_ocr.pdf_inspector.process_pdf",
            return_value=_inspection("text_based"),
        ) as inspector,
        patch(ocr_target, return_value=expected) as ocr,
    ):
        result = extractor("statement.pdf", **kwargs)

    pd.testing.assert_frame_equal(result, expected)
    direct.assert_called_once()
    inspector.assert_called_once_with("statement.pdf")
    ocr.assert_called_once_with("statement.pdf")


@pytest.mark.unit
def test_pdf_inspector_failure_does_not_block_ocr_after_rule_failure():
    expected = _valid_transactions()

    with (
        patch(
            "api.app.utils.hybrid_statement_ocr._extract_fnb_direct",
            side_effect=ValueError("unsupported layout"),
        ),
        patch(
            "api.app.utils.hybrid_statement_ocr.pdf_inspector.process_pdf",
            side_effect=RuntimeError("inspection failed"),
        ),
        patch(
            "api.app.utils.hybrid_statement_ocr.extract_fnb_account_statement_ocr",
            return_value=expected,
        ) as ocr,
    ):
        result = extract_fnb_account_statement("statement.pdf")

    pd.testing.assert_frame_equal(result, expected)
    ocr.assert_called_once_with("statement.pdf")


@pytest.mark.unit
def test_scanned_bank_detection_uses_ocr_for_first_and_last_pages(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"placeholder")
    page = MagicMock()
    page.extract_text.return_value = None
    reader = MagicMock()
    reader.pages = [page, page, page]

    with (
        patch("api.app.utils.bank_detection.pypdf.PdfReader", return_value=reader),
        patch(
            "api.app.utils.hybrid_statement_ocr.is_pdf_scanned", return_value=True
        ),
        patch(
            "api.app.utils.hybrid_statement_ocr.ocr_pdf_text",
            return_value="First National Bank Namibia FNB",
        ) as ocr,
    ):
        assert detect_bank_from_pdf(str(pdf_path)) == "fnb"

    ocr.assert_called_once_with(str(pdf_path), page_numbers=[1, 3])


class TestRealFNBHybridExtraction:
    @pytest.mark.integration
    @pytest.mark.slow
    def test_failed_rules_fall_back_to_pdf_inspector_and_reconciled_ocr(self):
        pdf_path = Path(__file__).parent.parent.parent / "fnb_feb_2025.pdf"
        if not pdf_path.exists():
            pytest.skip(f"FNB PDF not found at {pdf_path}")
        if shutil.which("tesseract") is None or shutil.which("pdftoppm") is None:
            pytest.skip("Tesseract and Poppler are required for OCR integration test")

        native = extract_fnb_direct(str(pdf_path))
        with (
            patch(
                "api.app.utils.hybrid_statement_ocr._extract_fnb_direct",
                return_value=pd.DataFrame(),
            ),
            patch(
                "api.app.utils.hybrid_statement_ocr.pdf_inspector.process_pdf",
                wraps=pdf_inspector.process_pdf,
            ) as inspector,
        ):
            ocr = extract_fnb_account_statement(str(pdf_path))
        financial_fields = [
            "transaction_date",
            "transaction_amount",
            "transaction_type",
            "balance",
        ]
        comparison_fields = financial_fields + ["Accrued Bank Charges"]
        sort_fields = financial_fields

        assert len(native) == len(ocr) == 82
        inspector.assert_called_once_with(str(pdf_path))
        assert ocr["transaction_type"].value_counts().to_dict() == {
            "debit": 73,
            "credit": 9,
        }
        assert round(ocr["transaction_amount"].sum(), 2) == 78444.02
        assert ocr["transaction_description"].str.strip().ne("").all()
        assert ocr["Accrued Bank Charges"].astype(float).sum() == 24.0

        native_financials = native.sort_values(sort_fields)[
            comparison_fields
        ].reset_index(drop=True)
        ocr_financials = ocr.sort_values(sort_fields)[comparison_fields].reset_index(
            drop=True
        )
        native_financials["Accrued Bank Charges"] = native_financials[
            "Accrued Bank Charges"
        ].astype(float)
        ocr_financials["Accrued Bank Charges"] = ocr_financials[
            "Accrued Bank Charges"
        ].astype(float)
        pd.testing.assert_frame_equal(ocr_financials, native_financials)
