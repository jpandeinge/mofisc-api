# api/app/services/financial/__init__.py

"""
Financial Services Module

Financial calculation services for comprehensive financial analysis.

Services:
- FinancialCalculationService - Legacy financial calculations
- IndustryStandardFinancialCalculator - Industry-standard financial metrics

Usage:
    from ..services.financial import IndustryStandardFinancialCalculator
"""

from .calculation_service import FinancialCalculationService
from .industry_standard_calculator import (
    AccountType,
    FinancialHealthBenchmarks,
    IndustryStandardFinancialCalculator,
    TransactionCategory,
)

__all__ = [
    "IndustryStandardFinancialCalculator",
    "FinancialCalculationService",
    "TransactionCategory",
    "AccountType",
    "FinancialHealthBenchmarks",
]
