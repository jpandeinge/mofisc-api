# api/app/services/financial/industry_standard_calculator.py

import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Union

from ...utils.logging_config import get_logger

logger = get_logger(__name__)


class TransactionCategory(Enum):
    """Industry-standard transaction categories"""

    # Essential Expenses (Non-discretionary)
    HOUSING = "housing"  # Rent, mortgage, property tax, HOA
    UTILITIES = "utilities"  # Electric, gas, water, internet, phone
    GROCERIES = "groceries"  # Essential food purchases
    TRANSPORTATION = "transportation"  # Car payment, gas, public transit, insurance
    HEALTHCARE = "healthcare"  # Medical expenses, prescriptions, insurance
    INSURANCE = "insurance"  # Life, disability, health insurance premiums
    MINIMUM_DEBT_PAYMENTS = "minimum_debt_payments"  # Required debt payments
    CHILDCARE = "childcare"  # Essential childcare costs

    # Savings & Investments
    EMERGENCY_SAVINGS = "emergency_savings"  # Emergency fund contributions
    RETIREMENT_SAVINGS = "retirement_savings"  # 401k, IRA contributions
    INVESTMENTS = "investments"  # Investment account transfers
    GENERAL_SAVINGS = "general_savings"  # Regular savings account transfers

    # Debt Payments
    CREDIT_CARD_PAYMENT = "credit_card_payment"
    LOAN_PAYMENT = "loan_payment"  # Personal, auto, student loans
    MORTGAGE_PAYMENT = "mortgage_payment"
    EXTRA_DEBT_PAYMENT = "extra_debt_payment"  # Above minimum payments

    # Discretionary Expenses
    DINING_OUT = "dining_out"
    ENTERTAINMENT = "entertainment"
    SHOPPING = "shopping"  # Non-essential purchases
    SUBSCRIPTIONS = "subscriptions"  # Netflix, Spotify, etc.
    TRAVEL = "travel"
    HOBBIES = "hobbies"

    # Income
    SALARY = "salary"
    BONUS = "bonus"
    FREELANCE = "freelance"
    INVESTMENT_INCOME = "investment_income"
    OTHER_INCOME = "other_income"

    # Other
    FEES = "fees"  # Bank fees, ATM fees
    TAXES = "taxes"  # Tax payments
    TRANSFERS = "transfers"  # Between own accounts
    UNCATEGORIZED = "uncategorized"


class AccountType(Enum):
    """Account types for proper asset classification"""

    CHECKING = "checking"
    SAVINGS = "savings"
    MONEY_MARKET = "money_market"
    CD = "certificate_of_deposit"
    CREDIT_CARD = "credit_card"
    INVESTMENT = "investment_account"
    RETIREMENT_401K = "retirement_401k"
    RETIREMENT_IRA = "retirement_ira"
    MORTGAGE = "mortgage"
    LOAN = "loan"
    OTHER = "other"


class FinancialHealthBenchmarks:
    """Industry-standard benchmarks for financial health metrics"""

    # Debt-to-Income Ratio Benchmarks (Federal Housing Administration & CFPB)
    DTI_EXCELLENT = 0.20  # 20% or less
    DTI_GOOD = 0.28  # 28% or less (housing)
    DTI_FAIR = 0.36  # 36% or less (total debt)
    DTI_POOR = 0.50  # 50% or more is concerning

    # Emergency Fund Benchmarks (Financial Planning Association)
    EMERGENCY_MINIMUM_MONTHS = 3
    EMERGENCY_RECOMMENDED_MONTHS = 6
    EMERGENCY_EXCELLENT_MONTHS = 12

    # Savings Rate Benchmarks (Bureau of Economic Analysis)
    SAVINGS_RATE_MINIMUM = 0.05  # 5%
    SAVINGS_RATE_GOOD = 0.10  # 10%
    SAVINGS_RATE_EXCELLENT = 0.20  # 20%

    # Net Worth Growth Benchmarks (CFA Institute)
    NET_WORTH_GROWTH_MINIMUM = 0.03  # 3% annual (inflation)
    NET_WORTH_GROWTH_GOOD = 0.07  # 7% annual (market average)
    NET_WORTH_GROWTH_EXCELLENT = 0.10  # 10% annual


class IndustryStandardFinancialCalculator:
    """
    Industry-standard financial calculator following CFPB, CFA Institute, and AICPA guidelines.
    Implements proper accounting principles with comprehensive edge case handling.
    """

    def __init__(self):
        self.decimal_precision = Decimal("0.01")  # Two decimal places for currency

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
                # Clean string input
                cleaned = re.sub(r"[^\d.-]", "", value.strip())
                if not cleaned or cleaned in ["-", "."]:
                    return default
                return Decimal(cleaned)
            else:
                return default
        except (InvalidOperation, ValueError, TypeError):
            return default

    def categorize_transaction(
        self, transaction: Dict[str, Any]
    ) -> TransactionCategory:
        """
        Categorize transaction using industry-standard categories.
        Handles edge cases and provides intelligent categorization.
        """
        try:
            # Get transaction details with safe defaults
            description = (
                str(transaction.get("transaction_description", "")).lower().strip()
            )
            amount = self.safe_decimal(transaction.get("transaction_amount", 0))
            transaction_type = (
                str(transaction.get("transaction_type", "")).lower().strip()
            )
            category_hint = str(transaction.get("category", "")).lower().strip()

            # Handle edge cases
            if not description and not category_hint:
                return TransactionCategory.UNCATEGORIZED

            if amount == 0:
                return (
                    TransactionCategory.TRANSFERS
                )  # Zero-amount transactions are likely transfers

            # Use existing category if provided and valid
            if category_hint:
                try:
                    return TransactionCategory(category_hint)
                except ValueError:
                    pass  # Continue with description-based categorization

            # Income categorization (credits)
            if transaction_type == "credit":
                if any(
                    keyword in description
                    for keyword in ["salary", "payroll", "wage", "pay"]
                ):
                    return TransactionCategory.SALARY
                elif any(keyword in description for keyword in ["bonus", "commission"]):
                    return TransactionCategory.BONUS
                elif any(
                    keyword in description
                    for keyword in ["freelance", "contractor", "1099"]
                ):
                    return TransactionCategory.FREELANCE
                elif any(
                    keyword in description
                    for keyword in ["dividend", "interest", "investment"]
                ):
                    return TransactionCategory.INVESTMENT_INCOME
                else:
                    return TransactionCategory.OTHER_INCOME

            # Expense categorization (debits)
            elif transaction_type == "debit":
                # Housing
                if any(
                    keyword in description
                    for keyword in ["rent", "mortgage", "property tax", "hoa"]
                ):
                    return TransactionCategory.HOUSING

                # Utilities
                elif any(
                    keyword in description
                    for keyword in [
                        "electric",
                        "gas",
                        "water",
                        "internet",
                        "phone",
                        "utility",
                    ]
                ):
                    return TransactionCategory.UTILITIES

                # Groceries vs Dining Out
                elif any(
                    keyword in description
                    for keyword in [
                        "grocery",
                        "supermarket",
                        "food market",
                        "walmart",
                        "target",
                    ]
                ):
                    return TransactionCategory.GROCERIES
                elif any(
                    keyword in description
                    for keyword in [
                        "restaurant",
                        "fast food",
                        "dining",
                        "uber eats",
                        "doordash",
                    ]
                ):
                    return TransactionCategory.DINING_OUT

                # Transportation
                elif any(
                    keyword in description
                    for keyword in [
                        "gas station",
                        "fuel",
                        "parking",
                        "transit",
                        "uber",
                        "lyft",
                    ]
                ):
                    return TransactionCategory.TRANSPORTATION

                # Healthcare
                elif any(
                    keyword in description
                    for keyword in [
                        "medical",
                        "doctor",
                        "pharmacy",
                        "hospital",
                        "health",
                    ]
                ):
                    return TransactionCategory.HEALTHCARE

                # Debt Payments
                elif any(
                    keyword in description for keyword in ["credit card", "payment"]
                ):
                    return TransactionCategory.CREDIT_CARD_PAYMENT
                elif any(
                    keyword in description for keyword in ["loan payment", "auto loan"]
                ):
                    return TransactionCategory.LOAN_PAYMENT

                # Savings (look for transfers to savings accounts)
                elif any(
                    keyword in description
                    for keyword in ["savings transfer", "emergency fund"]
                ):
                    return TransactionCategory.EMERGENCY_SAVINGS
                elif any(
                    keyword in description for keyword in ["401k", "retirement", "ira"]
                ):
                    return TransactionCategory.RETIREMENT_SAVINGS
                elif any(
                    keyword in description for keyword in ["investment", "brokerage"]
                ):
                    return TransactionCategory.INVESTMENTS

                # Subscriptions
                elif any(
                    keyword in description
                    for keyword in ["netflix", "spotify", "subscription", "monthly fee"]
                ):
                    return TransactionCategory.SUBSCRIPTIONS

                # Entertainment
                elif any(
                    keyword in description
                    for keyword in ["movie", "theater", "entertainment", "concert"]
                ):
                    return TransactionCategory.ENTERTAINMENT

                # Fees
                elif any(
                    keyword in description
                    for keyword in ["fee", "atm", "overdraft", "maintenance"]
                ):
                    return TransactionCategory.FEES

                else:
                    return TransactionCategory.UNCATEGORIZED

            else:
                return TransactionCategory.UNCATEGORIZED

        except Exception as e:
            logger.error(f"Error categorizing transaction: {str(e)}")
            return TransactionCategory.UNCATEGORIZED

    def calculate_monthly_income(
        self, transactions: List[Dict[str, Any]], months: int = 12
    ) -> Dict[str, Decimal]:
        """Calculate comprehensive monthly income with edge case handling"""
        try:
            if not transactions or months <= 0:
                return {
                    "gross_monthly_income": Decimal("0"),
                    "net_monthly_income": Decimal("0"),
                    "salary_income": Decimal("0"),
                    "other_income": Decimal("0"),
                    "consistency_score": Decimal("0"),
                }

            income_categories = [
                TransactionCategory.SALARY,
                TransactionCategory.BONUS,
                TransactionCategory.FREELANCE,
                TransactionCategory.INVESTMENT_INCOME,
                TransactionCategory.OTHER_INCOME,
            ]

            total_income = Decimal("0")
            salary_income = Decimal("0")
            monthly_incomes = []

            # Group transactions by month for consistency analysis
            monthly_income_tracking = defaultdict(Decimal)

            for transaction in transactions:
                try:
                    category = self.categorize_transaction(transaction)
                    if category not in income_categories:
                        continue

                    amount = self.safe_decimal(transaction.get("transaction_amount", 0))
                    if amount <= 0:  # Income should be positive
                        continue

                    total_income += amount

                    if category == TransactionCategory.SALARY:
                        salary_income += amount

                    # Track monthly income for consistency
                    transaction_date = transaction.get("transaction_date", "")
                    if transaction_date:
                        try:
                            date_obj = datetime.fromisoformat(
                                transaction_date.replace("Z", "+00:00")
                            )
                            month_key = f"{date_obj.year}-{date_obj.month:02d}"
                            monthly_income_tracking[month_key] += amount
                        except (AttributeError, TypeError, ValueError):
                            pass  # Skip if date parsing fails

                except Exception as e:
                    logger.error(
                        f"Error processing transaction for income calculation: {str(e)}"
                    )
                    continue

            # Calculate monthly averages
            gross_monthly_income = (
                total_income / Decimal(str(months)) if months > 0 else Decimal("0")
            )
            monthly_salary = (
                salary_income / Decimal(str(months)) if months > 0 else Decimal("0")
            )
            other_monthly_income = gross_monthly_income - monthly_salary

            # Calculate income consistency score
            monthly_incomes = list(monthly_income_tracking.values())
            if len(monthly_incomes) > 1:
                avg_income = sum(monthly_incomes) / len(monthly_incomes)
                if avg_income > 0:
                    variance = sum(
                        (income - avg_income) ** 2 for income in monthly_incomes
                    ) / len(monthly_incomes)
                    std_dev = variance.sqrt()
                    consistency_score = max(
                        Decimal("0"),
                        Decimal("100") - (std_dev / avg_income * Decimal("100")),
                    )
                else:
                    consistency_score = Decimal("0")
            else:
                consistency_score = (
                    Decimal("100") if gross_monthly_income > 0 else Decimal("0")
                )

            return {
                "gross_monthly_income": gross_monthly_income.quantize(
                    self.decimal_precision
                ),
                "net_monthly_income": gross_monthly_income.quantize(
                    self.decimal_precision
                ),  # Will be adjusted after tax calculation
                "salary_income": monthly_salary.quantize(self.decimal_precision),
                "other_income": other_monthly_income.quantize(self.decimal_precision),
                "consistency_score": consistency_score.quantize(self.decimal_precision),
            }

        except Exception as e:
            logger.error(f"Error calculating monthly income: {str(e)}")
            return {
                "gross_monthly_income": Decimal("0"),
                "net_monthly_income": Decimal("0"),
                "salary_income": Decimal("0"),
                "other_income": Decimal("0"),
                "consistency_score": Decimal("0"),
            }

    def calculate_monthly_expenses(
        self, transactions: List[Dict[str, Any]], months: int = 12
    ) -> Dict[str, Decimal]:
        """Calculate detailed monthly expenses with proper categorization"""
        try:
            if not transactions or months <= 0:
                return self._get_default_expense_breakdown()

            expense_totals = defaultdict(Decimal)

            for transaction in transactions:
                try:
                    category = self.categorize_transaction(transaction)
                    transaction_type = str(
                        transaction.get("transaction_type", "")
                    ).lower()

                    # Only process debit transactions (expenses)
                    if transaction_type != "debit":
                        continue

                    amount = self.safe_decimal(transaction.get("transaction_amount", 0))
                    if amount <= 0:  # Expenses should be positive values
                        continue

                    expense_totals[category.value] += amount

                except Exception as e:
                    logger.error(
                        f"Error processing transaction for expense calculation: {str(e)}"
                    )
                    continue

            # Calculate monthly averages and categorize
            monthly_divisor = Decimal(str(months))

            # Essential expenses (non-discretionary)
            essential_categories = [
                "housing",
                "utilities",
                "groceries",
                "transportation",
                "healthcare",
                "insurance",
                "minimum_debt_payments",
                "childcare",
            ]

            # Debt payment categories
            debt_categories = [
                "credit_card_payment",
                "loan_payment",
                "mortgage_payment",
                "extra_debt_payment",
            ]

            # Calculate totals
            essential_monthly = (
                sum(expense_totals[cat] for cat in essential_categories)
                / monthly_divisor
            )
            debt_monthly = (
                sum(expense_totals[cat] for cat in debt_categories) / monthly_divisor
            )
            total_monthly = sum(expense_totals.values()) / monthly_divisor
            discretionary_monthly = total_monthly - essential_monthly - debt_monthly

            return {
                "total_monthly_expenses": total_monthly.quantize(
                    self.decimal_precision
                ),
                "essential_monthly_expenses": essential_monthly.quantize(
                    self.decimal_precision
                ),
                "discretionary_monthly_expenses": discretionary_monthly.quantize(
                    self.decimal_precision
                ),
                "debt_payments_monthly": debt_monthly.quantize(self.decimal_precision),
                "housing_monthly": (
                    expense_totals["housing"] / monthly_divisor
                ).quantize(self.decimal_precision),
                "utilities_monthly": (
                    expense_totals["utilities"] / monthly_divisor
                ).quantize(self.decimal_precision),
                "groceries_monthly": (
                    expense_totals["groceries"] / monthly_divisor
                ).quantize(self.decimal_precision),
                "transportation_monthly": (
                    expense_totals["transportation"] / monthly_divisor
                ).quantize(self.decimal_precision),
                "healthcare_monthly": (
                    expense_totals["healthcare"] / monthly_divisor
                ).quantize(self.decimal_precision),
            }

        except Exception as e:
            logger.error(f"Error calculating monthly expenses: {str(e)}")
            return self._get_default_expense_breakdown()

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
                monthly_debt_payments = Decimal("0")  # Handle negative debt payments

            dti_ratio = (monthly_debt_payments / monthly_gross_income).quantize(
                Decimal("0.001")
            )

            # Score based on industry standards
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
            logger.error(f"Error calculating DTI ratio: {str(e)}")
            return {
                "dti_ratio": Decimal("0"),
                "dti_score": 0,
                "dti_category": "error",
                "recommendation": "Unable to calculate DTI due to data error",
            }

    def calculate_emergency_fund_coverage(
        self, liquid_savings: Decimal, monthly_essential_expenses: Decimal
    ) -> Dict[str, Any]:
        """Calculate emergency fund coverage following Financial Planning Association guidelines"""
        try:
            if monthly_essential_expenses <= 0:
                return {
                    "months_coverage": Decimal("0"),
                    "emergency_fund_score": 0,
                    "category": "insufficient_data",
                    "recommendation": "Unable to assess emergency fund without expense data",
                }

            if liquid_savings < 0:
                liquid_savings = Decimal("0")  # Handle negative liquid savings

            months_coverage = (liquid_savings / monthly_essential_expenses).quantize(
                self.decimal_precision
            )

            # Score based on industry standards
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
            logger.error(f"Error calculating emergency fund coverage: {str(e)}")
            return {
                "months_coverage": Decimal("0"),
                "emergency_fund_score": 0,
                "category": "error",
                "recommendation": "Unable to calculate emergency fund due to data error",
            }

    def calculate_savings_rate(
        self, monthly_savings: Decimal, monthly_gross_income: Decimal
    ) -> Dict[str, Any]:
        """Calculate actual savings rate (money moved to savings/investment accounts)"""
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
                monthly_savings = Decimal("0")  # Handle negative savings (withdrawals)

            savings_rate = (monthly_savings / monthly_gross_income).quantize(
                Decimal("0.001")
            )
            savings_rate_percentage = (savings_rate * 100).quantize(
                self.decimal_precision
            )

            # Score based on industry standards
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
            logger.error(f"Error calculating savings rate: {str(e)}")
            return {
                "savings_rate": Decimal("0"),
                "savings_rate_percentage": Decimal("0"),
                "savings_score": 0,
                "category": "error",
                "recommendation": "Unable to calculate savings rate due to data error",
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
            # Industry-standard weights (total = 100%)
            weights = {
                "dti": 0.30,  # 30% - Debt management is crucial
                "emergency_fund": 0.25,  # 25% - Emergency preparedness
                "savings": 0.25,  # 25% - Wealth building
                "additional": 0.20,  # 20% - Other factors (cash flow, etc.)
            }

            # Handle additional factors
            additional_score = 70  # Default neutral score
            if additional_factors:
                additional_score = sum(additional_factors.values()) / len(
                    additional_factors
                )

            # Calculate weighted score
            weighted_score = (
                dti_score * weights["dti"]
                + emergency_fund_score * weights["emergency_fund"]
                + savings_score * weights["savings"]
                + additional_score * weights["additional"]
            )

            # Round to integer
            final_score = max(0, min(100, round(weighted_score)))

            # Determine category and recommendation
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
            logger.error(
                f"Error calculating comprehensive financial health score: {str(e)}"
            )
            return {
                "financial_health_score": 0,
                "category": "error",
                "recommendation": "Unable to calculate score due to data error",
                "component_scores": {},
                "weights_used": {},
            }

    def _get_default_expense_breakdown(self) -> Dict[str, Decimal]:
        """Return default expense breakdown for edge cases"""
        return {
            "total_monthly_expenses": Decimal("0"),
            "essential_monthly_expenses": Decimal("0"),
            "discretionary_monthly_expenses": Decimal("0"),
            "debt_payments_monthly": Decimal("0"),
            "housing_monthly": Decimal("0"),
            "utilities_monthly": Decimal("0"),
            "groceries_monthly": Decimal("0"),
            "transportation_monthly": Decimal("0"),
            "healthcare_monthly": Decimal("0"),
        }
