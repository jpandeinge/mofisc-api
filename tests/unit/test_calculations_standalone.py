#!/usr/bin/env python3
"""
Pytest-compatible tests for industry-standard financial calculations.
Tests key calculation logic without external dependencies.
"""

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import pytest


class TransactionCategory(Enum):
    """Industry-standard transaction categories"""

    # Essential Expenses (Non-discretionary)
    HOUSING = "housing"
    UTILITIES = "utilities"
    GROCERIES = "groceries"
    TRANSPORTATION = "transportation"
    HEALTHCARE = "healthcare"
    INSURANCE = "insurance"
    MINIMUM_DEBT_PAYMENTS = "minimum_debt_payments"
    CHILDCARE = "childcare"

    # Savings & Investments
    EMERGENCY_SAVINGS = "emergency_savings"
    RETIREMENT_SAVINGS = "retirement_savings"
    INVESTMENTS = "investments"
    GENERAL_SAVINGS = "general_savings"

    # Debt Payments
    CREDIT_CARD_PAYMENT = "credit_card_payment"
    LOAN_PAYMENT = "loan_payment"
    MORTGAGE_PAYMENT = "mortgage_payment"
    EXTRA_DEBT_PAYMENT = "extra_debt_payment"

    # Discretionary Expenses
    DINING_OUT = "dining_out"
    ENTERTAINMENT = "entertainment"
    SHOPPING = "shopping"
    SUBSCRIPTIONS = "subscriptions"
    TRAVEL = "travel"
    HOBBIES = "hobbies"

    # Income
    SALARY = "salary"
    BONUS = "bonus"
    FREELANCE = "freelance"
    INVESTMENT_INCOME = "investment_income"
    OTHER_INCOME = "other_income"

    # Other
    FEES = "fees"
    TAXES = "taxes"
    TRANSFERS = "transfers"
    UNCATEGORIZED = "uncategorized"


class FinancialHealthBenchmarks:
    """Industry-standard benchmarks for financial health metrics"""

    DTI_EXCELLENT = 0.20
    DTI_GOOD = 0.28
    DTI_FAIR = 0.36
    DTI_POOR = 0.50
    EMERGENCY_MINIMUM_MONTHS = 3
    EMERGENCY_RECOMMENDED_MONTHS = 6
    EMERGENCY_EXCELLENT_MONTHS = 12
    SAVINGS_RATE_MINIMUM = 0.05
    SAVINGS_RATE_GOOD = 0.10
    SAVINGS_RATE_EXCELLENT = 0.20


class StandaloneFinancialCalculator:
    """Standalone version for testing core calculation logic"""

    def __init__(self):
        self.decimal_precision = Decimal("0.01")

    def safe_decimal(
        self,
        value: Union[str, int, float, Decimal, None],
        default: Decimal = Decimal("0"),
    ) -> Decimal:
        """Safely convert value to Decimal with error handling"""
        if value is None:
            return default

        try:
            if isinstance(value, Decimal):
                return value
            elif isinstance(value, (int, float)):
                if value != value:  # NaN check
                    return default
                if value == float("inf") or value == float("-inf"):
                    return default
                return Decimal(str(value))
            elif isinstance(value, str):
                cleaned = re.sub(r"[^\d.-]", "", value.strip())
                if not cleaned or cleaned in ["-", "."]:
                    return default
                return Decimal(cleaned)
            else:
                return default
        except (InvalidOperation, ValueError, TypeError):
            return default

    def calculate_debt_to_income_ratio(
        self, monthly_debt_payments: Decimal, monthly_gross_income: Decimal
    ) -> Dict[str, Any]:
        """Calculate DTI ratio following Federal Housing Administration guidelines"""
        try:
            if monthly_gross_income <= 0:
                return {
                    "dti_ratio": Decimal("0"),
                    "dti_score": 0,
                    "dti_category": "no_income",
                    "recommendation": "Unable to calculate DTI without income data",
                }

            if monthly_debt_payments < 0:
                monthly_debt_payments = Decimal("0")

            dti_ratio = (monthly_debt_payments / monthly_gross_income).quantize(
                Decimal("0.001")
            )

            if dti_ratio <= FinancialHealthBenchmarks.DTI_EXCELLENT:
                score = 100
                category = "excellent"
                recommendation = (
                    "Excellent debt management. Consider increasing investments."
                )
            elif dti_ratio <= FinancialHealthBenchmarks.DTI_GOOD:
                score = 85
                category = "good"
                recommendation = "Good debt level. Monitor for increases."
            elif dti_ratio <= FinancialHealthBenchmarks.DTI_FAIR:
                score = 65
                category = "fair"
                recommendation = "Consider debt reduction strategies."
            elif dti_ratio <= FinancialHealthBenchmarks.DTI_POOR:
                score = 40
                category = "poor"
                recommendation = (
                    "High debt burden. Prioritize debt reduction immediately."
                )
            else:
                score = 20
                category = "critical"
                recommendation = "Critical debt situation. Seek financial counseling."

            return {
                "dti_ratio": dti_ratio,
                "dti_score": score,
                "dti_category": category,
                "recommendation": recommendation,
            }

        except Exception as e:
            return {
                "dti_ratio": Decimal("0"),
                "dti_score": 0,
                "dti_category": "error",
                "recommendation": f"Unable to calculate DTI: {str(e)}",
            }

    def calculate_emergency_fund_coverage(
        self, liquid_savings: Decimal, monthly_essential_expenses: Decimal
    ) -> Dict[str, Any]:
        """Calculate emergency fund coverage"""
        try:
            if monthly_essential_expenses <= 0:
                return {
                    "months_coverage": Decimal("0"),
                    "emergency_fund_score": 0,
                    "category": "insufficient_data",
                    "recommendation": "Unable to assess emergency fund without expense data",
                }

            if liquid_savings < 0:
                liquid_savings = Decimal("0")

            months_coverage = (liquid_savings / monthly_essential_expenses).quantize(
                self.decimal_precision
            )

            if months_coverage >= FinancialHealthBenchmarks.EMERGENCY_EXCELLENT_MONTHS:
                score = 100
                category = "excellent"
                recommendation = (
                    "Excellent emergency fund. Consider additional investments."
                )
            elif (
                months_coverage
                >= FinancialHealthBenchmarks.EMERGENCY_RECOMMENDED_MONTHS
            ):
                score = 85
                category = "good"
                recommendation = "Good emergency fund coverage."
            elif months_coverage >= FinancialHealthBenchmarks.EMERGENCY_MINIMUM_MONTHS:
                score = 65
                category = "minimum"
                recommendation = (
                    "Meet minimum emergency fund. Aim for 6 months coverage."
                )
            elif months_coverage >= 1:
                score = 40
                category = "insufficient"
                recommendation = "Build emergency fund to 3-6 months of expenses."
            else:
                score = 20
                category = "critical"
                recommendation = "Priority: Build emergency fund immediately."

            return {
                "months_coverage": months_coverage,
                "emergency_fund_score": score,
                "category": category,
                "recommendation": recommendation,
            }

        except Exception as e:
            return {
                "months_coverage": Decimal("0"),
                "emergency_fund_score": 0,
                "category": "error",
                "recommendation": f"Unable to calculate emergency fund: {str(e)}",
            }

    def calculate_savings_rate(
        self, monthly_savings: Decimal, monthly_gross_income: Decimal
    ) -> Dict[str, Any]:
        """Calculate actual savings rate"""
        try:
            if monthly_gross_income <= 0:
                return {
                    "savings_rate": Decimal("0"),
                    "savings_rate_percentage": Decimal("0"),
                    "savings_score": 0,
                    "category": "no_income",
                    "recommendation": "Unable to calculate savings rate without income data",
                }

            if monthly_savings < 0:
                monthly_savings = Decimal("0")

            savings_rate = (monthly_savings / monthly_gross_income).quantize(
                Decimal("0.001")
            )
            savings_rate_percentage = (savings_rate * 100).quantize(
                self.decimal_precision
            )

            if savings_rate >= FinancialHealthBenchmarks.SAVINGS_RATE_EXCELLENT:
                score = 100
                category = "excellent"
                recommendation = (
                    "Excellent savings rate. You're building wealth effectively."
                )
            elif savings_rate >= FinancialHealthBenchmarks.SAVINGS_RATE_GOOD:
                score = 80
                category = "good"
                recommendation = "Good savings rate. Consider increasing if possible."
            elif savings_rate >= FinancialHealthBenchmarks.SAVINGS_RATE_MINIMUM:
                score = 60
                category = "minimum"
                recommendation = "Basic savings rate. Aim for 10-20% of income."
            elif savings_rate > 0:
                score = 40
                category = "low"
                recommendation = "Low savings rate. Look for areas to reduce expenses."
            else:
                score = 20
                category = "none"
                recommendation = (
                    "No savings detected. Start with any amount, even $25/month."
                )

            return {
                "savings_rate": savings_rate,
                "savings_rate_percentage": savings_rate_percentage,
                "savings_score": score,
                "category": category,
                "recommendation": recommendation,
            }

        except Exception as e:
            return {
                "savings_rate": Decimal("0"),
                "savings_rate_percentage": Decimal("0"),
                "savings_score": 0,
                "category": "error",
                "recommendation": f"Unable to calculate savings rate: {str(e)}",
            }

    def calculate_comprehensive_financial_health_score(
        self,
        dti_score: int,
        emergency_fund_score: int,
        savings_score: int,
        additional_factors: Dict[str, int] = None,
    ) -> Dict[str, Any]:
        """Calculate comprehensive financial health score using industry-standard weighting"""
        try:
            weights = {
                "dti": 0.30,
                "emergency_fund": 0.25,
                "savings": 0.25,
                "additional": 0.20,
            }

            additional_score = 70
            if additional_factors:
                additional_score = sum(additional_factors.values()) / len(
                    additional_factors
                )

            weighted_score = (
                dti_score * weights["dti"]
                + emergency_fund_score * weights["emergency_fund"]
                + savings_score * weights["savings"]
                + additional_score * weights["additional"]
            )

            final_score = max(0, min(100, round(weighted_score)))

            if final_score >= 80:
                category = "excellent"
                recommendation = (
                    "Excellent financial health. Focus on wealth optimization."
                )
            elif final_score >= 70:
                category = "good"
                recommendation = "Good financial health. Minor improvements possible."
            elif final_score >= 60:
                category = "fair"
                recommendation = (
                    "Fair financial health. Focus on emergency fund and debt."
                )
            elif final_score >= 40:
                category = "needs_improvement"
                recommendation = (
                    "Financial health needs improvement. Address debt and savings."
                )
            else:
                category = "poor"
                recommendation = "Poor financial health. Immediate action required."

            return {
                "financial_health_score": final_score,
                "category": category,
                "recommendation": recommendation,
                "component_scores": {
                    "debt_management": dti_score,
                    "emergency_preparedness": emergency_fund_score,
                    "savings_effectiveness": savings_score,
                    "additional_factors": additional_score,
                },
                "weights_used": weights,
            }

        except Exception as e:
            return {
                "financial_health_score": 0,
                "category": "error",
                "recommendation": f"Unable to calculate score: {str(e)}",
                "component_scores": {},
                "weights_used": {},
            }


# Pytest fixtures
@pytest.fixture
def calculator():
    """Provide a calculator instance for tests"""
    return StandaloneFinancialCalculator()


# Pytest test functions
def test_dti_calculation(calculator):
    """Test DTI ratio calculation"""
    dti_result = calculator.calculate_debt_to_income_ratio(
        Decimal("200"), Decimal("5000")
    )
    expected_dti = 0.04  # 4%
    actual_dti = float(dti_result["dti_ratio"])
    assert (
        abs(actual_dti - expected_dti) < 0.001
    ), f"Expected 4%, got {actual_dti * 100:.1f}%"


def test_dti_score(calculator):
    """Test DTI score is excellent for low debt"""
    dti_result = calculator.calculate_debt_to_income_ratio(
        Decimal("200"), Decimal("5000")
    )
    assert (
        dti_result["dti_score"] >= 85
    ), f"Expected >=85, got {dti_result['dti_score']}"
    assert dti_result["dti_category"] == "excellent"


def test_emergency_fund_coverage(calculator):
    """Test emergency fund coverage calculation"""
    ef_result = calculator.calculate_emergency_fund_coverage(
        Decimal("6000"), Decimal("2000")
    )
    expected_months = 3.0
    actual_months = float(ef_result["months_coverage"])
    assert (
        abs(actual_months - expected_months) < 0.1
    ), f"Expected 3.0 months, got {actual_months:.1f} months"


def test_emergency_fund_score(calculator):
    """Test emergency fund score for minimum coverage"""
    ef_result = calculator.calculate_emergency_fund_coverage(
        Decimal("6000"), Decimal("2000")
    )
    assert (
        ef_result["emergency_fund_score"] >= 60
    ), f"Expected >=60, got {ef_result['emergency_fund_score']}"
    assert ef_result["category"] == "minimum"


def test_savings_rate_calculation(calculator):
    """Test savings rate calculation"""
    savings_result = calculator.calculate_savings_rate(Decimal("500"), Decimal("5000"))
    expected_rate = 0.10  # 10%
    actual_rate = float(savings_result["savings_rate"])
    assert (
        abs(actual_rate - expected_rate) < 0.001
    ), f"Expected 10%, got {actual_rate * 100:.1f}%"


def test_savings_score(calculator):
    """Test savings score meets minimum threshold"""
    savings_result = calculator.calculate_savings_rate(Decimal("500"), Decimal("5000"))
    # 10% savings rate meets minimum savings threshold
    assert (
        savings_result["savings_score"] >= 60
    ), f"Expected >=60, got {savings_result['savings_score']}"
    # Category should be at least 'minimum' or better
    assert savings_result["category"] in [
        "minimum",
        "good",
        "excellent",
    ], f"Unexpected category: '{savings_result['category']}'"


def test_comprehensive_health_score(calculator):
    """Test comprehensive financial health score"""
    dti_result = calculator.calculate_debt_to_income_ratio(
        Decimal("200"), Decimal("5000")
    )
    ef_result = calculator.calculate_emergency_fund_coverage(
        Decimal("6000"), Decimal("2000")
    )
    savings_result = calculator.calculate_savings_rate(Decimal("500"), Decimal("5000"))

    health_result = calculator.calculate_comprehensive_financial_health_score(
        dti_result["dti_score"],
        ef_result["emergency_fund_score"],
        savings_result["savings_score"],
    )

    # Actual calculation: (100*.30) + (65*.25) + (savings_score*.25) + (70*.20)
    # With savings at 60 (minimum): 30 + 16.25 + 15 + 14 = 75.25 ≈ 75
    # With savings at 80 (good): 30 + 16.25 + 20 + 14 = 80.25 ≈ 80
    actual_score = health_result["financial_health_score"]
    assert 70 <= actual_score <= 85, f"Expected score between 70-85, got {actual_score}"


def test_health_category(calculator):
    """Test health category is good or excellent"""
    dti_result = calculator.calculate_debt_to_income_ratio(
        Decimal("200"), Decimal("5000")
    )
    ef_result = calculator.calculate_emergency_fund_coverage(
        Decimal("6000"), Decimal("2000")
    )
    savings_result = calculator.calculate_savings_rate(Decimal("500"), Decimal("5000"))

    health_result = calculator.calculate_comprehensive_financial_health_score(
        dti_result["dti_score"],
        ef_result["emergency_fund_score"],
        savings_result["savings_score"],
    )

    assert health_result["category"] in [
        "good",
        "excellent",
    ], f"Expected 'good' or 'excellent', got '{health_result['category']}'"


def test_zero_income_dti(calculator):
    """Test DTI with zero income edge case"""
    zero_income_dti = calculator.calculate_debt_to_income_ratio(
        Decimal("100"), Decimal("0")
    )
    assert (
        zero_income_dti["dti_category"] == "no_income"
    ), "Did not handle zero income correctly"


def test_negative_savings(calculator):
    """Test handling of negative savings values"""
    negative_savings = calculator.calculate_savings_rate(
        Decimal("-100"), Decimal("1000")
    )
    assert negative_savings["savings_rate"] == Decimal(
        "0"
    ), "Did not handle negative savings correctly"


def test_safe_decimal_invalid_input(calculator):
    """Test safe decimal conversion with invalid input"""
    safe_result = calculator.safe_decimal("invalid", Decimal("42"))
    assert safe_result == Decimal("42"), "Did not handle invalid input correctly"


def test_safe_decimal_none_input(calculator):
    """Test safe decimal conversion with None input"""
    safe_result = calculator.safe_decimal(None, Decimal("100"))
    assert safe_result == Decimal("100"), "Did not handle None input correctly"


def test_safe_decimal_valid_string(calculator):
    """Test safe decimal conversion with valid string"""
    safe_result = calculator.safe_decimal("$1,234.56")
    assert safe_result == Decimal("1234.56"), "Did not parse currency string correctly"
