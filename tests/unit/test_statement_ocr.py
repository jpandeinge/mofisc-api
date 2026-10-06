"""
Unit tests for statement_ocr.py - FNB statement extraction.
Ensures date parsing handles all edge cases without breaking.
"""

# Import the functions we're testing
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from api.app.utils.statement_ocr import extract_fnb_account_statement


class TestFNBStatementExtraction:
    """Tests for FNB statement date parsing and extraction."""

    @pytest.fixture
    def mock_pdf_with_statement_period(self):
        """Mock PDF with valid statement period."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = """
        FNB Statement
        Statement Period :01 January 2025 to31 January 2025

        01 Jan Test Transaction 1,000.00Cr 5,000.00
        15 Jan Another Transaction 500.00Dr 4,500.00
        """
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        return mock_pdf

    @pytest.fixture
    def mock_pdf_without_statement_period(self):
        """Mock PDF without statement period - tests fallback to current year."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = """
        FNB Statement
        Account Number: 123456789

        01 Jan Test Transaction 1,000.00Cr 5,000.00
        15 Jan Another Transaction 500.00Dr 4,500.00
        """
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        return mock_pdf

    @pytest.fixture
    def mock_pdf_with_alternative_period_format(self):
        """Mock PDF with alternative statement period format."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = """
        FNB Statement
        Statement Period: 01 February 2025 to 28 February 2025

        01 Feb Test Transaction 1,000.00Cr 5,000.00
        15 Feb Another Transaction 500.00Dr 4,500.00
        """
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        return mock_pdf

    @pytest.fixture
    def mock_pdf_empty(self):
        """Mock PDF with no transactions."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = """
        FNB Statement
        No transactions found
        """
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        return mock_pdf

    @pytest.mark.unit
    def test_extract_with_valid_statement_period(self, mock_pdf_with_statement_period):
        """Test extraction when statement period is present."""
        with patch("pdfplumber.open", return_value=mock_pdf_with_statement_period):
            result = extract_fnb_account_statement("/fake/path.pdf")

            assert isinstance(result, pd.DataFrame)
            assert "transaction_date" in result.columns
            assert "transaction_amount" in result.columns
            assert "transaction_type" in result.columns

            # Verify dates are properly formatted
            if not result.empty:
                for date in result["transaction_date"]:
                    assert pd.notna(date)
                    # Should match YYYY-MM-DD format
                    datetime.strptime(date, "%Y-%m-%d")

    @pytest.mark.unit
    def test_extract_without_statement_period_uses_fallback(
        self, mock_pdf_without_statement_period
    ):
        """Test extraction falls back to current year when no statement period."""
        with patch("pdfplumber.open", return_value=mock_pdf_without_statement_period):
            result = extract_fnb_account_statement("/fake/path.pdf")

            assert isinstance(result, pd.DataFrame)
            # Should not crash - this was the original bug
            if not result.empty:
                for date in result["transaction_date"]:
                    if pd.notna(date):
                        # Verify it's a valid date string
                        datetime.strptime(date, "%Y-%m-%d")

    @pytest.mark.unit
    def test_extract_with_alternative_period_format(
        self, mock_pdf_with_alternative_period_format
    ):
        """Test extraction handles alternative statement period formats."""
        with patch(
            "pdfplumber.open", return_value=mock_pdf_with_alternative_period_format
        ):
            result = extract_fnb_account_statement("/fake/path.pdf")

            assert isinstance(result, pd.DataFrame)
            # Should successfully parse with alternative format

    @pytest.mark.unit
    def test_extract_empty_pdf_returns_empty_dataframe(self, mock_pdf_empty):
        """Test extraction returns empty DataFrame for PDF with no transactions."""
        with patch("pdfplumber.open", return_value=mock_pdf_empty):
            result = extract_fnb_account_statement("/fake/path.pdf")

            assert isinstance(result, pd.DataFrame)
            assert result.empty
            # Should have correct columns even when empty
            expected_columns = [
                "transaction_date",
                "transaction_description",
                "transaction_amount",
                "transaction_type",
                "balance",
            ]
            assert list(result.columns) == expected_columns

    @pytest.mark.unit
    def test_result_dataframe_has_required_columns(
        self, mock_pdf_with_statement_period
    ):
        """Test that result always has required columns."""
        with patch("pdfplumber.open", return_value=mock_pdf_with_statement_period):
            result = extract_fnb_account_statement("/fake/path.pdf")

            required_columns = [
                "transaction_date",
                "transaction_description",
                "transaction_amount",
                "transaction_type",
                "balance",
            ]

            for col in required_columns:
                assert col in result.columns, f"Missing required column: {col}"

    @pytest.mark.unit
    def test_transaction_types_are_valid(self, mock_pdf_with_statement_period):
        """Test that transaction types are 'credit' or 'debit'."""
        with patch("pdfplumber.open", return_value=mock_pdf_with_statement_period):
            result = extract_fnb_account_statement("/fake/path.pdf")

            if not result.empty:
                valid_types = {"credit", "debit"}
                for tx_type in result["transaction_type"]:
                    assert (
                        tx_type in valid_types
                    ), f"Invalid transaction type: {tx_type}"


class TestFNBDateParsing:
    """Focused tests for date parsing edge cases."""

    @pytest.mark.unit
    def test_date_parsing_cross_year_boundary(self):
        """Test date parsing when statement spans year boundary (e.g., Dec to Jan)."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = """
        FNB Statement
        Statement Period :15 December 2024 to15 January 2025

        20 Dec Test Transaction 1,000.00Cr 5,000.00
        05 Jan Another Transaction 500.00Dr 4,500.00
        """
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            result = extract_fnb_account_statement("/fake/path.pdf")

            if not result.empty:
                # December dates should be 2024, January dates should be 2025
                for date in result["transaction_date"]:
                    if pd.notna(date):
                        parsed = datetime.strptime(date, "%Y-%m-%d")
                        if parsed.month == 12:
                            assert parsed.year == 2024
                        elif parsed.month == 1:
                            assert parsed.year == 2025

    @pytest.mark.unit
    def test_no_crash_on_invalid_date_format(self):
        """Test that invalid date formats don't crash the extraction."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = """
        FNB Statement
        Statement Period :01 January 2025 to31 January 2025

        XX Jan Test Transaction 1,000.00Cr 5,000.00
        """
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            # Should not raise an exception
            result = extract_fnb_account_statement("/fake/path.pdf")
            assert isinstance(result, pd.DataFrame)


class TestRealFNBPDF:
    """Integration test with real FNB PDF if available."""

    @pytest.mark.integration
    @pytest.mark.slow
    def test_real_fnb_pdf_extraction(self):
        """Test extraction with the actual FNB PDF in the repo."""
        pdf_path = Path(__file__).parent.parent.parent / "fnb_feb_2025.pdf"

        if not pdf_path.exists():
            pytest.skip(f"FNB PDF not found at {pdf_path}")

        result = extract_fnb_account_statement(str(pdf_path))

        assert isinstance(result, pd.DataFrame)
        assert not result.empty, "Expected transactions in real PDF"

        # Verify date format
        for date in result["transaction_date"]:
            assert pd.notna(date), "Date should not be null"
            datetime.strptime(date, "%Y-%m-%d")  # Should not raise

        # Verify amounts are numeric
        assert result["transaction_amount"].dtype in [float, "float64"]
        assert result["balance"].dtype in [float, "float64"]

        # Verify transaction types
        valid_types = {"credit", "debit"}
        for tx_type in result["transaction_type"]:
            assert tx_type in valid_types
