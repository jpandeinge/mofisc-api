# api/app/routes/__init__.py

from .achievement_routes import router as achievement_routes
from .calculator_routes import router as calculator_routes
from .goal_routes import router as goal_routes
from .transaction_routes import router as transaction_routes
from .user_routes import router as user_routes

__all__ = [
    "transaction_routes",
    "goal_routes",
    "calculator_routes",
    "user_routes",
    "achievement_routes",
]
