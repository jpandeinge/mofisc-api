"""Add organisation workspaces, memberships, and shared enterprise billing.

Revision ID: 045_add_organisation_workspaces
Revises: 044_secure_notification_realtime
Create Date: 2026-08-02
"""

from alembic import op


revision = "045_add_organisation_workspaces"
down_revision = "044_secure_notification_realtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE public.organisations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT NOT NULL CHECK (char_length(btrim(name)) >= 2),
            slug TEXT NOT NULL UNIQUE,
            owner_user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'suspended', 'closed')),
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE public.organisation_members (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organisation_id UUID NOT NULL
                REFERENCES public.organisations(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'member'
                CHECK (role IN ('owner', 'admin', 'analyst', 'member')),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('invited', 'active', 'suspended')),
            invited_by UUID REFERENCES auth.users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_organisation_member UNIQUE (organisation_id, user_id),
            CONSTRAINT uq_organisation_member_user UNIQUE (user_id)
        );

        ALTER TABLE public.user_profiles
            ADD COLUMN organisation_id UUID
            REFERENCES public.organisations(id) ON DELETE SET NULL;

        ALTER TABLE public.billing_subscriptions
            ADD COLUMN organisation_id UUID
            REFERENCES public.organisations(id) ON DELETE CASCADE;

        CREATE UNIQUE INDEX uq_billing_subscriptions_organisation_id
            ON public.billing_subscriptions(organisation_id)
            WHERE organisation_id IS NOT NULL;

        CREATE INDEX idx_organisation_members_organisation
            ON public.organisation_members(organisation_id, status);
        CREATE INDEX idx_organisation_members_user
            ON public.organisation_members(user_id, status);
        CREATE INDEX idx_user_profiles_organisation
            ON public.user_profiles(organisation_id);

        CREATE OR REPLACE FUNCTION public.set_organisation_updated_at()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        SET search_path = public, pg_temp
        AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER set_organisations_updated_at
            BEFORE UPDATE ON public.organisations
            FOR EACH ROW EXECUTE FUNCTION public.set_organisation_updated_at();

        CREATE TRIGGER set_organisation_members_updated_at
            BEFORE UPDATE ON public.organisation_members
            FOR EACH ROW EXECUTE FUNCTION public.set_organisation_updated_at();

        CREATE OR REPLACE FUNCTION public.is_organisation_member(
            p_organisation_id UUID,
            p_roles TEXT[] DEFAULT NULL
        )
        RETURNS BOOLEAN
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT EXISTS (
                SELECT 1
                FROM public.organisation_members membership
                WHERE membership.organisation_id = p_organisation_id
                  AND membership.user_id = (SELECT auth.uid())
                  AND membership.status = 'active'
                  AND (p_roles IS NULL OR membership.role = ANY(p_roles))
            );
        $$;

        REVOKE ALL ON FUNCTION public.is_organisation_member(UUID, TEXT[])
            FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.is_organisation_member(UUID, TEXT[])
            TO authenticated, service_role;

        ALTER TABLE public.organisations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.organisation_members ENABLE ROW LEVEL SECURITY;

        CREATE POLICY "Service role manages organisations"
            ON public.organisations
            FOR ALL TO service_role
            USING (true) WITH CHECK (true);

        CREATE POLICY "Members can view their organisation"
            ON public.organisations
            FOR SELECT TO authenticated
            USING (public.is_organisation_member(organisations.id));

        CREATE POLICY "Owners and admins can update their organisation"
            ON public.organisations
            FOR UPDATE TO authenticated
            USING (public.is_organisation_member(organisations.id, ARRAY['owner', 'admin']))
            WITH CHECK (public.is_organisation_member(organisations.id, ARRAY['owner', 'admin']));

        CREATE POLICY "Service role manages organisation members"
            ON public.organisation_members
            FOR ALL TO service_role
            USING (true) WITH CHECK (true);

        CREATE POLICY "Members can view workspace membership"
            ON public.organisation_members
            FOR SELECT TO authenticated
            USING (public.is_organisation_member(organisation_members.organisation_id));

        CREATE POLICY "Organisation members can view shared billing"
            ON public.billing_subscriptions
            FOR SELECT TO authenticated
            USING (
                organisation_id IS NOT NULL
                AND public.is_organisation_member(billing_subscriptions.organisation_id)
            );

        REVOKE ALL ON public.organisations FROM anon;
        REVOKE ALL ON public.organisation_members FROM anon;
        GRANT SELECT ON public.organisations TO authenticated;
        GRANT UPDATE (name, settings) ON public.organisations TO authenticated;
        GRANT SELECT ON public.organisation_members TO authenticated;

        CREATE OR REPLACE FUNCTION public.bootstrap_organisation_workspace(
            p_user_id UUID,
            p_email TEXT,
            p_full_name TEXT,
            p_organisation_name TEXT
        )
        RETURNS TABLE (
            organisation_id UUID,
            organisation_name TEXT,
            organisation_slug TEXT,
            member_role TEXT,
            plan TEXT,
            subscription_status TEXT
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            v_organisation_id UUID;
            v_organisation_name TEXT;
            v_organisation_slug TEXT;
            v_member_role TEXT;
            v_plan TEXT;
            v_subscription_status TEXT;
        BEGIN
            IF p_user_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM auth.users WHERE id = p_user_id
            ) THEN
                RAISE EXCEPTION 'A valid authenticated user is required';
            END IF;

            IF char_length(btrim(COALESCE(p_organisation_name, ''))) < 2 THEN
                RAISE EXCEPTION 'Organisation name must contain at least 2 characters';
            END IF;

            INSERT INTO public.user_profiles (
                id, user_type, full_name, email, preferences, is_active
            )
            VALUES (
                p_user_id,
                'organisation',
                COALESCE(NULLIF(btrim(p_full_name), ''), split_part(p_email, '@', 1)),
                lower(btrim(p_email)),
                '{}'::jsonb,
                true
            )
            ON CONFLICT (id) DO UPDATE
            SET user_type = 'organisation',
                full_name = CASE
                    WHEN btrim(COALESCE(public.user_profiles.full_name, '')) = ''
                        THEN EXCLUDED.full_name
                    ELSE public.user_profiles.full_name
                END,
                updated_at = now();

            SELECT membership.organisation_id, membership.role
            INTO v_organisation_id, v_member_role
            FROM public.organisation_members membership
            WHERE membership.user_id = p_user_id
              AND membership.status = 'active'
            LIMIT 1;

            IF v_organisation_id IS NULL THEN
                v_organisation_id := gen_random_uuid();
                v_organisation_slug := trim(both '-' FROM lower(
                    regexp_replace(btrim(p_organisation_name), '[^a-zA-Z0-9]+', '-', 'g')
                )) || '-' || left(replace(p_user_id::text, '-', ''), 8);

                INSERT INTO public.organisations (
                    id, name, slug, owner_user_id
                )
                VALUES (
                    v_organisation_id,
                    btrim(p_organisation_name),
                    v_organisation_slug,
                    p_user_id
                );

                INSERT INTO public.organisation_members (
                    organisation_id, user_id, role, status, invited_by
                )
                VALUES (
                    v_organisation_id, p_user_id, 'owner', 'active', p_user_id
                );
                v_member_role := 'owner';
            END IF;

            UPDATE public.user_profiles
            SET organisation_id = v_organisation_id,
                user_type = 'organisation',
                updated_at = now()
            WHERE id = p_user_id;

            INSERT INTO public.billing_subscriptions (
                user_id,
                organisation_id,
                plan,
                status,
                current_period_start,
                current_period_end,
                cancel_at_period_end
            )
            VALUES (
                p_user_id,
                v_organisation_id,
                'enterprise',
                'trialing',
                now(),
                now() + interval '7 days',
                false
            )
            ON CONFLICT (user_id) DO UPDATE
            SET organisation_id = EXCLUDED.organisation_id,
                plan = CASE
                    WHEN billing_subscriptions.organisation_id IS NULL
                        AND billing_subscriptions.plan = 'free'
                        AND billing_subscriptions.dodo_subscription_id IS NULL
                        THEN 'enterprise'
                    ELSE billing_subscriptions.plan
                END,
                status = CASE
                    WHEN billing_subscriptions.organisation_id IS NULL
                        AND billing_subscriptions.plan = 'free'
                        AND billing_subscriptions.dodo_subscription_id IS NULL
                        THEN 'trialing'
                    ELSE billing_subscriptions.status
                END,
                current_period_start = CASE
                    WHEN billing_subscriptions.organisation_id IS NULL
                        AND billing_subscriptions.plan = 'free'
                        AND billing_subscriptions.dodo_subscription_id IS NULL
                        THEN now()
                    ELSE billing_subscriptions.current_period_start
                END,
                current_period_end = CASE
                    WHEN billing_subscriptions.organisation_id IS NULL
                        AND billing_subscriptions.plan = 'free'
                        AND billing_subscriptions.dodo_subscription_id IS NULL
                        THEN now() + interval '7 days'
                    ELSE billing_subscriptions.current_period_end
                END;

            SELECT organisation.name, organisation.slug
            INTO v_organisation_name, v_organisation_slug
            FROM public.organisations organisation
            WHERE organisation.id = v_organisation_id;

            SELECT subscription.plan, subscription.status
            INTO v_plan, v_subscription_status
            FROM public.billing_subscriptions subscription
            WHERE subscription.organisation_id = v_organisation_id
            LIMIT 1;

            RETURN QUERY SELECT
                v_organisation_id,
                v_organisation_name,
                v_organisation_slug,
                v_member_role,
                COALESCE(v_plan, 'free'),
                COALESCE(v_subscription_status, 'active');
        END;
        $$;

        REVOKE ALL ON FUNCTION public.bootstrap_organisation_workspace(
            UUID, TEXT, TEXT, TEXT
        ) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.bootstrap_organisation_workspace(
            UUID, TEXT, TEXT, TEXT
        ) TO service_role;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS "Organisation members can view shared billing"
            ON public.billing_subscriptions;
        DROP POLICY IF EXISTS "Members can view their organisation"
            ON public.organisations;
        DROP POLICY IF EXISTS "Owners and admins can update their organisation"
            ON public.organisations;
        DROP POLICY IF EXISTS "Members can view workspace membership"
            ON public.organisation_members;

        DROP FUNCTION IF EXISTS public.bootstrap_organisation_workspace(
            UUID, TEXT, TEXT, TEXT
        );
        DROP FUNCTION IF EXISTS public.is_organisation_member(UUID, TEXT[]);

        DROP TRIGGER IF EXISTS set_organisation_members_updated_at
            ON public.organisation_members;
        DROP TRIGGER IF EXISTS set_organisations_updated_at
            ON public.organisations;
        DROP FUNCTION IF EXISTS public.set_organisation_updated_at();

        DROP INDEX IF EXISTS public.uq_billing_subscriptions_organisation_id;
        ALTER TABLE public.billing_subscriptions DROP COLUMN IF EXISTS organisation_id;
        ALTER TABLE public.user_profiles DROP COLUMN IF EXISTS organisation_id;

        DROP TABLE IF EXISTS public.organisation_members;
        DROP TABLE IF EXISTS public.organisations;
        """
    )
