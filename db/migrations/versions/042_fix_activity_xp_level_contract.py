"""Fix activity XP level contract

Revision ID: 042_fix_activity_xp_level_contract
Revises: 041_harden_notification_deduplication
Create Date: 2026-05-01
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "042_fix_activity_xp_level_contract"
down_revision = "041_harden_notification_deduplication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION calculate_user_level(total_points integer)
        RETURNS integer AS $$
            SELECT GREATEST(
                1,
                FLOOR(POWER(GREATEST(COALESCE(total_points, 0), 0) / 100.0, 0.5))::integer + 1
            );
        $$ LANGUAGE sql IMMUTABLE;

        CREATE OR REPLACE FUNCTION update_user_points_on_achievement()
        RETURNS TRIGGER AS $$
        DECLARE
            achievement_points integer;
            next_points integer;
        BEGIN
            IF NEW.is_completed = true
               AND (TG_OP = 'INSERT' OR COALESCE(OLD.is_completed, false) = false) THEN
                SELECT COALESCE(points, 0)
                INTO achievement_points
                FROM achievements
                WHERE id = NEW.achievement_id;

                next_points := COALESCE(achievement_points, 0);

                INSERT INTO user_points (id, user_id, points, level)
                VALUES (
                    uuid_generate_v4(),
                    NEW.user_id,
                    next_points,
                    calculate_user_level(next_points)
                )
                ON CONFLICT (user_id)
                DO UPDATE SET
                    points = user_points.points + next_points,
                    level = calculate_user_level(user_points.points + next_points),
                    updated_at = now();
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE OR REPLACE FUNCTION update_user_points_on_challenge()
        RETURNS TRIGGER AS $$
        DECLARE
            challenge_points integer;
            next_points integer;
        BEGIN
            IF NEW.is_completed = true
               AND (TG_OP = 'INSERT' OR COALESCE(OLD.is_completed, false) = false) THEN
                SELECT COALESCE(points, 0)
                INTO challenge_points
                FROM daily_challenges
                WHERE id = NEW.challenge_id;

                next_points := COALESCE(challenge_points, 0);

                INSERT INTO user_points (id, user_id, points, level)
                VALUES (
                    uuid_generate_v4(),
                    NEW.user_id,
                    next_points,
                    calculate_user_level(next_points)
                )
                ON CONFLICT (user_id)
                DO UPDATE SET
                    points = user_points.points + next_points,
                    level = calculate_user_level(user_points.points + next_points),
                    updated_at = now();
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        WITH earned_points AS (
            SELECT user_id, SUM(points)::integer AS points
            FROM (
                SELECT ua.user_id, COALESCE(a.points, 0) AS points
                FROM user_achievements ua
                JOIN achievements a ON a.id = ua.achievement_id
                WHERE ua.is_completed = true

                UNION ALL

                SELECT uc.user_id, COALESCE(dc.points, 0) AS points
                FROM user_challenges uc
                JOIN daily_challenges dc ON dc.id = uc.challenge_id
                WHERE uc.is_completed = true
            ) rewards
            GROUP BY user_id
        )
        INSERT INTO user_points (id, user_id, points, level)
        SELECT
            uuid_generate_v4(),
            earned_points.user_id,
            earned_points.points,
            calculate_user_level(earned_points.points)
        FROM earned_points
        ON CONFLICT (user_id)
        DO UPDATE SET
            points = EXCLUDED.points,
            level = EXCLUDED.level,
            updated_at = now();

        UPDATE user_points
        SET level = calculate_user_level(points),
            updated_at = now()
        WHERE points IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_user_points_on_achievement()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_completed = true
               AND (TG_OP = 'INSERT' OR COALESCE(OLD.is_completed, false) = false) THEN
                DECLARE
                    achievement_points INTEGER;
                BEGIN
                    SELECT points INTO achievement_points
                    FROM achievements
                    WHERE id = NEW.achievement_id;

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

        CREATE OR REPLACE FUNCTION update_user_points_on_challenge()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_completed = true
               AND (TG_OP = 'INSERT' OR COALESCE(OLD.is_completed, false) = false) THEN
                DECLARE
                    challenge_points INTEGER;
                BEGIN
                    SELECT points INTO challenge_points
                    FROM daily_challenges
                    WHERE id = NEW.challenge_id;

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

        DROP FUNCTION IF EXISTS calculate_user_level(integer);
        """
    )
