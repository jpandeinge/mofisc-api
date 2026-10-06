# api/app/services/users/__init__.py

"""
User Services Module

Services for managing user profiles and accounts.

Services:
- UserService - User profile and account operations

Usage:
    from ..services.users import UserService
"""

from .user_service import UserService

__all__ = ["UserService"]
