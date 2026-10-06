"""
Pytest configuration and shared fixtures for Galahad API tests.
Following industry best practices for test organization.
"""

import os
import sys
from pathlib import Path

import pytest

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment variables and configuration."""
    # Set test environment
    os.environ["TESTING"] = "true"
    os.environ["ENV"] = "test"

    yield

    # Cleanup after all tests
    if "TESTING" in os.environ:
        del os.environ["TESTING"]


@pytest.fixture
def sample_transaction_data():
    """Provide sample transaction data for tests."""
    return [
        {
            "transaction_date": "2024-01-01T00:00:00",
            "transaction_amount": 5000.00,
            "transaction_type": "credit",
            "transaction_description": "Salary Payment",
            "category": "salary",
        },
        {
            "transaction_date": "2024-01-02T00:00:00",
            "transaction_amount": 1500.00,
            "transaction_type": "debit",
            "transaction_description": "Rent Payment",
            "category": "housing",
        },
        {
            "transaction_date": "2024-01-03T00:00:00",
            "transaction_amount": 400.00,
            "transaction_type": "debit",
            "transaction_description": "Grocery Store",
            "category": "groceries",
        },
    ]


@pytest.fixture
def mock_user_id():
    """Provide a mock user ID for tests."""
    return "test-user-12345"
