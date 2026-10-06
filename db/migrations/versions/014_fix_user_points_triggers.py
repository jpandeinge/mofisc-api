# migrations/versions/014_fix_user_points_triggers.py

"""Fix user_points trigger functions to include id column

Revision ID: 014_fix_user_points_triggers
Revises: 013_fix_type_mismatch
Create Date: 2025-12-18
"""

from alembic import op

# revision identifiers
revision = "014_fix_points_triggers"
down_revision = "013_fix_type_mismatch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fix the achievement completion trigger function
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_user_points_on_achievement()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_completed = true AND (OLD.is_completed = false OR OLD.is_completed IS NULL) THEN
                -- Get achievement points
                DECLARE
                    achievement_points INTEGER;
                BEGIN
                    SELECT points INTO achievement_points
                    FROM achievements
                    WHERE id = NEW.achievement_id;

                    -- Update user points (now includes id with uuid_generate_v4())
                    INSERT INTO user_points (id, user_id, points, level)
                    VALUES (uuid_generate_v4(), NEW.user_id, achievement_points, 1)
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        points = user_points.points + achievement_points,
                        level = GREATEST(1, FLOOR(POWER((user_points.points + achievement_points) / 100.0, 0.5))::INTEGER);
                END;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """
    )

    # Fix the challenge completion trigger function
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_user_points_on_challenge()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_completed = true AND (OLD.is_completed = false OR OLD.is_completed IS NULL) THEN
                -- Get challenge points
                DECLARE
                    challenge_points INTEGER;
                BEGIN
                    SELECT points INTO challenge_points
                    FROM daily_challenges
                    WHERE id = NEW.challenge_id;

                    -- Update user points (now includes id with uuid_generate_v4())
                    INSERT INTO user_points (id, user_id, points, level)
                    VALUES (uuid_generate_v4(), NEW.user_id, challenge_points, 1)
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        points = user_points.points + challenge_points,
                        level = GREATEST(1, FLOOR(POWER((user_points.points + challenge_points) / 100.0, 0.5))::INTEGER);
                END;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """
    )


def downgrade() -> None:
    # Revert to original trigger functions (without id)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_user_points_on_achievement()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_completed = true AND (OLD.is_completed = false OR OLD.is_completed IS NULL) THEN
                DECLARE
                    achievement_points INTEGER;
                BEGIN
                    SELECT points INTO achievement_points
                    FROM achievements
                    WHERE id = NEW.achievement_id;

                    INSERT INTO user_points (user_id, points, level)
                    VALUES (NEW.user_id, achievement_points, 1)
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        points = user_points.points + achievement_points,
                        level = GREATEST(1, FLOOR(POWER((user_points.points + achievement_points) / 100.0, 0.5))::INTEGER);
                END;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_user_points_on_challenge()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_completed = true AND (OLD.is_completed = false OR OLD.is_completed IS NULL) THEN
                DECLARE
                    challenge_points INTEGER;
                BEGIN
                    SELECT points INTO challenge_points
                    FROM daily_challenges
                    WHERE id = NEW.challenge_id;

                    INSERT INTO user_points (user_id, points, level)
                    VALUES (NEW.user_id, challenge_points, 1)
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        points = user_points.points + challenge_points,
                        level = GREATEST(1, FLOOR(POWER((user_points.points + challenge_points) / 100.0, 0.5))::INTEGER);
                END;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """
    )
