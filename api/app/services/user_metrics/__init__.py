# api/app/services/user_metrics/__init__.py

"""
User Metrics Module

Comprehensive user financial and achievement metrics calculation.

Module Structure:
    - config.py - Configuration constants
    - utils/ - Helper utilities (defaults, cache)
    - calculators/ - Calculation logic (financial, achievements, activity)
    - storage/ - Database operations (placeholder for future extraction)
    - metrics_service.py - Main service orchestrator

Main Service:
    UserMetricsSupabaseService - Orchestrates all metrics calculations

Usage:
    from ..services.user_metrics import UserMetricsSupabaseService, UserMetricsConfig

    service = UserMetricsSupabaseService()
    metrics = service.calculate_comprehensive_user_metrics(user_id)
"""

from .config import UserMetricsConfig
from .metrics_service import UserMetricsSupabaseService

__all__ = ["UserMetricsSupabaseService", "UserMetricsConfig"]
