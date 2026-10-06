"""Fix budget SQL functions and enforce trusted device uniqueness

Revision ID: 036_fix_budget_funcs_and_devices
Revises: 035_add_budget_history_and_alerts
Create Date: 2026-02-19
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "036_fix_budget_funcs_and_devices"
down_revision = "035_add_budget_history_and_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deduplicate trusted devices first, then enforce unique constraint if missing.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.trusted_devices') IS NOT NULL THEN
                WITH ranked AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY user_id, fingerprint
                            ORDER BY COALESCE(last_used_at, last_login_at, created_at) DESC, id DESC
                        ) AS rn
                    FROM public.trusted_devices
                )
                DELETE FROM public.trusted_devices td
                USING ranked r
                WHERE td.id = r.id
                  AND r.rn > 1;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'uq_trusted_devices_user_fingerprint'
                ) THEN
                    ALTER TABLE public.trusted_devices
                    ADD CONSTRAINT uq_trusted_devices_user_fingerprint
                    UNIQUE (user_id, fingerprint);
                END IF;
            END IF;
        END
        $$;
        """
    )

    # Replace budget functions with transaction_amount/transaction_type-safe versions.
    op.execute("DROP FUNCTION IF EXISTS public.check_budget_alerts(UUID);")
    op.execute("DROP FUNCTION IF EXISTS public.get_budget_spending(UUID, VARCHAR, DATE, DATE);")
    op.execute(
        "DROP FUNCTION IF EXISTS public.get_budget_spending(UUID, VARCHAR, TIMESTAMP WITHOUT TIME ZONE, DATE);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.get_budget_spending(UUID, VARCHAR, DATE, TIMESTAMP WITHOUT TIME ZONE);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.get_budget_spending(UUID, VARCHAR, TIMESTAMP WITHOUT TIME ZONE, TIMESTAMP WITHOUT TIME ZONE);"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.get_budget_spending(
            p_user_id UUID,
            p_category VARCHAR,
            p_start_date DATE,
            p_end_date DATE
        )
        RETURNS NUMERIC AS $$
        DECLARE
            total_spending NUMERIC;
        BEGIN
            SELECT COALESCE(
                SUM(
                    CASE
                        WHEN LOWER(COALESCE(transaction_type, '')) = 'debit'
                            THEN ABS(COALESCE(transaction_amount, 0))
                        WHEN LOWER(COALESCE(transaction_type, '')) = 'credit'
                            THEN 0
                        ELSE
                            CASE
                                WHEN COALESCE(transaction_amount, 0) < 0
                                    THEN ABS(COALESCE(transaction_amount, 0))
                                ELSE 0
                            END
                    END
                ),
                0
            )
            INTO total_spending
            FROM public.transactions
            WHERE user_id = p_user_id
              AND category = p_category
              AND transaction_date::DATE >= p_start_date
              AND transaction_date::DATE <= p_end_date;

            RETURN COALESCE(total_spending, 0);
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
        """
    )

    # Compatibility overloads for older function callers.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.get_budget_spending(
            p_user_id UUID,
            p_category VARCHAR,
            p_start_date TIMESTAMP WITHOUT TIME ZONE,
            p_end_date DATE
        )
        RETURNS NUMERIC AS $$
        BEGIN
            RETURN public.get_budget_spending(
                p_user_id,
                p_category,
                p_start_date::DATE,
                p_end_date
            );
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.get_budget_spending(
            p_user_id UUID,
            p_category VARCHAR,
            p_start_date DATE,
            p_end_date TIMESTAMP WITHOUT TIME ZONE
        )
        RETURNS NUMERIC AS $$
        BEGIN
            RETURN public.get_budget_spending(
                p_user_id,
                p_category,
                p_start_date,
                p_end_date::DATE
            );
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.get_budget_spending(
            p_user_id UUID,
            p_category VARCHAR,
            p_start_date TIMESTAMP WITHOUT TIME ZONE,
            p_end_date TIMESTAMP WITHOUT TIME ZONE
        )
        RETURNS NUMERIC AS $$
        BEGIN
            RETURN public.get_budget_spending(
                p_user_id,
                p_category,
                p_start_date::DATE,
                p_end_date::DATE
            );
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.check_budget_alerts(p_user_id UUID)
        RETURNS TABLE(
            budget_id UUID,
            category VARCHAR,
            budget_amount NUMERIC,
            spent_amount NUMERIC,
            percentage INTEGER,
            threshold_exceeded BOOLEAN
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT
                b.id AS budget_id,
                b.category,
                b.amount AS budget_amount,
                s.spent_amount,
                CASE
                    WHEN b.amount > 0
                        THEN ((s.spent_amount / b.amount) * 100)::INTEGER
                    ELSE 0
                END AS percentage,
                CASE
                    WHEN b.amount > 0
                        THEN ((s.spent_amount / b.amount) * 100) >= b.alert_threshold
                    ELSE FALSE
                END AS threshold_exceeded
            FROM public.budgets b
            CROSS JOIN LATERAL (
                SELECT public.get_budget_spending(
                    b.user_id,
                    b.category,
                    CASE b.period
                        WHEN 'daily' THEN CURRENT_DATE
                        WHEN 'weekly' THEN (CURRENT_DATE - 7)
                        WHEN 'monthly' THEN DATE_TRUNC('month', CURRENT_DATE)::DATE
                        WHEN 'yearly' THEN DATE_TRUNC('year', CURRENT_DATE)::DATE
                        ELSE DATE_TRUNC('month', CURRENT_DATE)::DATE
                    END,
                    CURRENT_DATE
                ) AS spent_amount
            ) s
            WHERE b.user_id = p_user_id
              AND b.is_active = TRUE;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.check_budget_alerts(UUID);")
    op.execute("DROP FUNCTION IF EXISTS public.get_budget_spending(UUID, VARCHAR, DATE, DATE);")
    op.execute(
        "DROP FUNCTION IF EXISTS public.get_budget_spending(UUID, VARCHAR, TIMESTAMP WITHOUT TIME ZONE, DATE);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.get_budget_spending(UUID, VARCHAR, DATE, TIMESTAMP WITHOUT TIME ZONE);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.get_budget_spending(UUID, VARCHAR, TIMESTAMP WITHOUT TIME ZONE, TIMESTAMP WITHOUT TIME ZONE);"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.get_budget_spending(
            p_user_id UUID,
            p_category VARCHAR,
            p_start_date DATE,
            p_end_date DATE
        )
        RETURNS NUMERIC AS $$
        DECLARE
            total_spending NUMERIC;
        BEGIN
            SELECT COALESCE(SUM(amount), 0)
            INTO total_spending
            FROM public.transactions
            WHERE user_id = p_user_id
              AND category = p_category
              AND transaction_date >= p_start_date
              AND transaction_date <= p_end_date
              AND amount < 0;

            RETURN ABS(total_spending);
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.check_budget_alerts(p_user_id UUID)
        RETURNS TABLE(
            budget_id UUID,
            category VARCHAR,
            budget_amount NUMERIC,
            spent_amount NUMERIC,
            percentage INTEGER,
            threshold_exceeded BOOLEAN
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT
                b.id AS budget_id,
                b.category,
                b.amount AS budget_amount,
                public.get_budget_spending(
                    b.user_id,
                    b.category,
                    CASE b.period
                        WHEN 'daily' THEN CURRENT_DATE
                        WHEN 'weekly' THEN CURRENT_DATE - INTERVAL '7 days'
                        WHEN 'monthly' THEN DATE_TRUNC('month', CURRENT_DATE)::DATE
                        WHEN 'yearly' THEN DATE_TRUNC('year', CURRENT_DATE)::DATE
                    END,
                    CURRENT_DATE
                ) AS spent_amount,
                (
                    (public.get_budget_spending(
                        b.user_id,
                        b.category,
                        CASE b.period
                            WHEN 'daily' THEN CURRENT_DATE
                            WHEN 'weekly' THEN CURRENT_DATE - INTERVAL '7 days'
                            WHEN 'monthly' THEN DATE_TRUNC('month', CURRENT_DATE)::DATE
                            WHEN 'yearly' THEN DATE_TRUNC('year', CURRENT_DATE)::DATE
                        END,
                        CURRENT_DATE
                    ) / b.amount) * 100
                )::INTEGER AS percentage,
                (
                    (public.get_budget_spending(
                        b.user_id,
                        b.category,
                        CASE b.period
                            WHEN 'daily' THEN CURRENT_DATE
                            WHEN 'weekly' THEN CURRENT_DATE - INTERVAL '7 days'
                            WHEN 'monthly' THEN DATE_TRUNC('month', CURRENT_DATE)::DATE
                            WHEN 'yearly' THEN DATE_TRUNC('year', CURRENT_DATE)::DATE
                        END,
                        CURRENT_DATE
                    ) / b.amount) * 100
                ) >= b.alert_threshold AS threshold_exceeded
            FROM public.budgets b
            WHERE b.user_id = p_user_id
              AND b.is_active = TRUE;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_trusted_devices_user_fingerprint'
            ) THEN
                ALTER TABLE public.trusted_devices
                DROP CONSTRAINT uq_trusted_devices_user_fingerprint;
            END IF;
        END
        $$;
        """
    )
