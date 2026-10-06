# api/app/models/financial_metrics.py

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base


class UserFinancialMetrics(Base):
    __tablename__ = "user_financial_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    statement_id = Column(String, nullable=True)  # Can be null for overall metrics
    calculation_date = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)

    # Financial Health Score components
    financial_health_score = Column(Numeric(5, 2), nullable=False)
    dti_score = Column(Numeric(5, 2), nullable=False)
    savings_score = Column(Numeric(5, 2), nullable=False)
    cash_flow_score = Column(Numeric(5, 2), nullable=False)
    behavior_score = Column(Numeric(5, 2), nullable=False)

    # Raw metrics for calculations
    total_income = Column(Numeric(15, 2), nullable=False, server_default="0")
    total_expenses = Column(Numeric(15, 2), nullable=False, server_default="0")
    total_debt_payments = Column(Numeric(15, 2), nullable=False, server_default="0")
    net_cash_flow = Column(Numeric(15, 2), nullable=False, server_default="0")
    latest_balance = Column(Numeric(15, 2), nullable=False, server_default="0")

    # Transaction counts and patterns
    transaction_count = Column(Integer, nullable=False, server_default="0")
    overdraft_count = Column(Integer, nullable=False, server_default="0")
    expense_volatility = Column(Numeric(15, 2), nullable=False, server_default="0")

    # Recommendations and insights
    recommendations = Column(JSONB, server_default="[]")
    insights = Column(JSONB, server_default="{}")
    score_interpretation = Column(String, nullable=True)

    # Metadata
    is_current = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="financial_metrics")

    __table_args__ = (
        Index("idx_financial_metrics_user_id", "user_id"),
        Index("idx_financial_metrics_statement_id", "statement_id"),
        Index("idx_financial_metrics_calculation_date", "calculation_date"),
        Index("idx_financial_metrics_is_current", "is_current"),
        Index("idx_financial_metrics_user_current", "user_id", "is_current"),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "statement_id": self.statement_id,
            "calculation_date": self.calculation_date.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "financial_health_score": float(self.financial_health_score),
            "dti_score": float(self.dti_score),
            "savings_score": float(self.savings_score),
            "cash_flow_score": float(self.cash_flow_score),
            "behavior_score": float(self.behavior_score),
            "total_income": float(self.total_income),
            "total_expenses": float(self.total_expenses),
            "total_debt_payments": float(self.total_debt_payments),
            "net_cash_flow": float(self.net_cash_flow),
            "latest_balance": float(self.latest_balance),
            "transaction_count": self.transaction_count,
            "overdraft_count": self.overdraft_count,
            "expense_volatility": float(self.expense_volatility),
            "recommendations": self.recommendations,
            "insights": self.insights,
            "score_interpretation": self.score_interpretation,
            "is_current": self.is_current,
        }


class UserCashFlowData(Base):
    __tablename__ = "user_cash_flow_data"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    date = Column(Date, nullable=False)
    income = Column(Numeric(15, 2), nullable=False, server_default="0")
    expenses = Column(Numeric(15, 2), nullable=False, server_default="0")
    net_flow = Column(Numeric(15, 2), nullable=False, server_default="0")
    running_balance = Column(Numeric(15, 2), nullable=False, server_default="0")
    transaction_count = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="cash_flow_data")

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="unique_user_date_cash_flow"),
        Index("idx_cash_flow_user_id", "user_id"),
        Index("idx_cash_flow_date", "date"),
        Index("idx_cash_flow_user_date", "user_id", "date"),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "date": self.date.isoformat(),
            "income": float(self.income),
            "expenses": float(self.expenses),
            "net_flow": float(self.net_flow),
            "running_balance": float(self.running_balance),
            "transaction_count": self.transaction_count,
        }


class UserSubscriptions(Base):
    __tablename__ = "user_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    name = Column(String, nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    frequency = Column(String, nullable=False)  # monthly, yearly, weekly
    category = Column(String, nullable=True)
    description_pattern = Column(String, nullable=True)
    first_detected = Column(Date, nullable=False)
    last_seen = Column(Date, nullable=False)
    transaction_count = Column(Integer, nullable=False, server_default="1")
    confidence_score = Column(
        Numeric(3, 2), nullable=False, server_default="1.0"
    )  # 0-1 confidence
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="subscriptions")

    __table_args__ = (
        Index("idx_subscriptions_user_id", "user_id"),
        Index("idx_subscriptions_is_active", "is_active"),
        Index("idx_subscriptions_user_active", "user_id", "is_active"),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "name": self.name,
            "amount": float(self.amount),
            "frequency": self.frequency,
            "category": self.category,
            "description_pattern": self.description_pattern,
            "first_detected": self.first_detected.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "transaction_count": self.transaction_count,
            "confidence_score": float(self.confidence_score),
            "is_active": self.is_active,
        }


class UserCategorySpending(Base):
    __tablename__ = "user_category_spending"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    category = Column(String, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    total_amount = Column(Numeric(15, 2), nullable=False, server_default="0")
    transaction_count = Column(Integer, nullable=False, server_default="0")
    average_amount = Column(Numeric(15, 2), nullable=False, server_default="0")
    percentage_of_expenses = Column(Numeric(5, 2), nullable=False, server_default="0")
    trend_direction = Column(String, nullable=True)  # increasing, decreasing, stable
    trend_percentage = Column(Numeric(5, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="category_spending")

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "category",
            "period_start",
            "period_end",
            name="unique_user_category_period",
        ),
        Index("idx_category_spending_user_id", "user_id"),
        Index("idx_category_spending_category", "category"),
        Index("idx_category_spending_period", "period_start", "period_end"),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "category": self.category,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "total_amount": float(self.total_amount),
            "transaction_count": self.transaction_count,
            "average_amount": float(self.average_amount),
            "percentage_of_expenses": float(self.percentage_of_expenses),
            "trend_direction": self.trend_direction,
            "trend_percentage": (
                float(self.trend_percentage) if self.trend_percentage else None
            ),
        }


class UserMonthlyTrends(Base):
    __tablename__ = "user_monthly_trends"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id"), nullable=False)
    month = Column(Date, nullable=False)  # First day of the month
    income_current = Column(Numeric(15, 2), nullable=False, server_default="0")
    income_previous = Column(Numeric(15, 2), nullable=False, server_default="0")
    income_change_pct = Column(Numeric(5, 2), nullable=False, server_default="0")
    expenses_current = Column(Numeric(15, 2), nullable=False, server_default="0")
    expenses_previous = Column(Numeric(15, 2), nullable=False, server_default="0")
    expenses_change_pct = Column(Numeric(5, 2), nullable=False, server_default="0")
    balance_current = Column(Numeric(15, 2), nullable=False, server_default="0")
    balance_previous = Column(Numeric(15, 2), nullable=False, server_default="0")
    balance_change_pct = Column(Numeric(5, 2), nullable=False, server_default="0")
    transaction_count_current = Column(Integer, nullable=False, server_default="0")
    transaction_count_previous = Column(Integer, nullable=False, server_default="0")
    transaction_count_change_pct = Column(
        Numeric(5, 2), nullable=False, server_default="0"
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="monthly_trends")

    __table_args__ = (
        UniqueConstraint("user_id", "month", name="unique_user_month_trends"),
        Index("idx_monthly_trends_user_id", "user_id"),
        Index("idx_monthly_trends_month", "month"),
        Index("idx_monthly_trends_user_month", "user_id", "month"),
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "month": self.month.isoformat(),
            "income": {
                "currentValue": float(self.income_current),
                "previousValue": float(self.income_previous),
                "percentageChange": float(self.income_change_pct),
            },
            "expenses": {
                "currentValue": float(self.expenses_current),
                "previousValue": float(self.expenses_previous),
                "percentageChange": float(self.expenses_change_pct),
            },
            "balance": {
                "currentValue": float(self.balance_current),
                "previousValue": float(self.balance_previous),
                "percentageChange": float(self.balance_change_pct),
            },
            "transactionCount": {
                "currentValue": self.transaction_count_current,
                "previousValue": self.transaction_count_previous,
                "percentageChange": float(self.transaction_count_change_pct),
            },
        }
