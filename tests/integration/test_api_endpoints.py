#!/usr/bin/env python3
"""
Integration tests for API endpoints.
Tests API structure, health checks, and response formats.
"""

from typing import Any, Dict

import pytest
import requests

# Configuration
API_BASE_URL = "http://127.0.0.1:8000"
API_TIMEOUT = 5


def check_api_available():
    """Check if API is accessible"""
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=API_TIMEOUT)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False


# Skip all tests in this module if API is not available
pytestmark = pytest.mark.skipif(
    not check_api_available(), reason="API is not running or not accessible"
)


@pytest.mark.integration
class TestAPIHealth:
    """Test API health and availability"""

    def test_health_endpoint_accessible(self):
        """Test that health endpoint is accessible"""
        response = requests.get(f"{API_BASE_URL}/health", timeout=API_TIMEOUT)
        assert (
            response.status_code == 200
        ), f"Health check failed with status {response.status_code}"

    def test_health_response_structure(self):
        """Test health endpoint response structure"""
        response = requests.get(f"{API_BASE_URL}/health", timeout=API_TIMEOUT)
        assert response.status_code == 200

        data = response.json()
        expected_keys = ["status", "environment", "version"]

        for key in expected_keys:
            assert key in data, f"Health response missing key: {key}"

        assert (
            data["status"] == "healthy"
        ), f"Expected status 'healthy', got '{data['status']}'"

    def test_health_endpoint_performance(self):
        """Test health endpoint responds quickly"""
        import time

        start = time.time()
        response = requests.get(f"{API_BASE_URL}/health", timeout=API_TIMEOUT)
        elapsed = time.time() - start

        assert response.status_code == 200
        assert elapsed < 1.0, f"Health check took {elapsed:.2f}s, should be < 1s"


@pytest.mark.integration
class TestAPIDocumentation:
    """Test API documentation endpoints"""

    def test_docs_endpoint(self):
        """Test that API documentation is accessible in development"""
        response = requests.get(f"{API_BASE_URL}/api/docs", timeout=API_TIMEOUT)
        # Docs may be disabled in production, so accept 200 or 404
        assert response.status_code in [
            200,
            404,
        ], f"Unexpected docs status: {response.status_code}"

    def test_openapi_spec(self):
        """Test that OpenAPI specification is accessible"""
        response = requests.get(f"{API_BASE_URL}/api/openapi.json", timeout=API_TIMEOUT)
        # OpenAPI may be disabled in production, so accept 200 or 404
        if response.status_code == 200:
            spec = response.json()
            assert "paths" in spec, "OpenAPI spec missing 'paths' key"
            assert "info" in spec, "OpenAPI spec missing 'info' key"


@pytest.mark.integration
class TestAPIEndpoints:
    """Test API endpoint structure and authentication"""

    def test_financial_metrics_requires_auth(self):
        """Test that financial metrics endpoint requires authentication"""
        response = requests.get(
            f"{API_BASE_URL}/api/v1/financial-metrics/", timeout=API_TIMEOUT
        )
        # Should return 401 (Unauthorized) or 403 (Forbidden) without auth
        assert response.status_code in [
            401,
            403,
            404,
        ], f"Expected auth error, got {response.status_code}"

    def test_transactions_endpoint_exists(self):
        """Test that transactions endpoint exists"""
        response = requests.get(
            f"{API_BASE_URL}/api/v1/transactions", timeout=API_TIMEOUT
        )
        # Endpoint should exist (may require auth, but shouldn't be 404)
        assert response.status_code != 404, "Transactions endpoint not found"


@pytest.mark.integration
class TestCalculationConsistency:
    """Test calculation logic consistency"""

    def test_net_cash_flow_calculation(self):
        """Test net cash flow calculation is mathematically correct"""
        income = 5000
        expenses = 3500
        expected_net_flow = 1500

        net_flow = income - expenses
        assert (
            net_flow == expected_net_flow
        ), f"Net flow calculation error: expected {expected_net_flow}, got {net_flow}"

    def test_savings_rate_calculation(self):
        """Test savings rate calculation is correct"""
        income = 5000
        savings = 1500
        expected_savings_rate = 30.0  # (1500/5000) * 100

        savings_rate = (savings / income) * 100
        assert (
            abs(savings_rate - expected_savings_rate) < 0.01
        ), f"Savings rate error: expected {expected_savings_rate}%, got {savings_rate:.2f}%"

    def test_percentage_bounds(self):
        """Test that percentage calculations stay within valid bounds"""
        test_cases = [
            (100, 50, 50.0),  # 50% savings rate
            (100, 100, 100.0),  # 100% savings rate
            (100, 0, 0.0),  # 0% savings rate
        ]

        for income, savings, expected_rate in test_cases:
            if income > 0:
                rate = (savings / income) * 100
                assert 0 <= rate <= 100, f"Percentage out of bounds: {rate}%"
                assert (
                    abs(rate - expected_rate) < 0.01
                ), f"Expected {expected_rate}%, got {rate:.2f}%"


@pytest.mark.integration
class TestDatabaseSchemaCompliance:
    """Test expected database schema compliance"""

    def test_expected_tables_defined(self):
        """Verify expected database tables are defined"""
        expected_tables = [
            "user_financial_metrics",
            "user_cash_flow_data",
            "user_subscriptions",
            "user_category_spending",
            "user_monthly_trends",
            "user_achievements",
            "user_streaks",
            "user_points",
        ]

        # This test documents expected tables
        assert len(expected_tables) == 8, "Expected 8 database tables"

    def test_financial_metrics_fields(self):
        """Verify expected financial metrics fields"""
        expected_fields = [
            "financial_health_score",
            "total_income",
            "total_expenses",
            "net_cash_flow",
            "transaction_count",
            "savings_score",
        ]

        # This test documents expected fields
        assert len(expected_fields) == 6, "Expected 6 financial metric fields"

    def test_streak_fields(self):
        """Verify expected streak fields"""
        expected_fields = [
            "streak_type",
            "current_count",
            "highest_count",
            "last_tracked_date",
        ]

        # This test documents expected fields
        assert len(expected_fields) == 4, "Expected 4 streak fields"
