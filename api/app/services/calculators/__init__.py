# api/app/services/calculators/__init__.py

"""
Calculator Services Module

Services for managing calculator history and statistics.

Services:
- CalculatorService - Calculator usage tracking and history

Usage:
    from ..services.calculators import CalculatorService
"""

from .calculator_service import CalculatorService

__all__ = ["CalculatorService"]
