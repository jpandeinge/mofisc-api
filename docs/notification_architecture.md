# Notification architecture

## Decision

Galahad uses Supabase as the notification source of truth and Realtime
transport. Socket.IO is not part of the default production path.

Vercel Functions support WebSockets, but a connection remains attached to one
function instance and is bounded by the invocation duration. A horizontally
scaled Socket.IO deployment therefore also needs shared coordination such as
Redis. That additional service is not justified for the current requirement,
which is a one-way, user-scoped notification stream that Supabase already
provides.

Socket.IO remains a valid later addition for features that are truly
bidirectional, such as presence, collaborative sessions, or high-frequency live
interaction. It should not replace the notification database.

## Data and delivery flow

1. An application event calls `NotificationService`.
2. The service creates a deduplicated row in `public.notifications`.
3. Supabase Realtime emits the insert to the authenticated user's RLS-protected
   subscription.
4. The React `NotificationProvider` updates the shared badge, optionally shows a
   toast, and reconciles history after reconnects.
5. If the user opted into email, SendGrid receives the rendered email.
6. If the user opted into push and Firebase is configured, FCM sends a
   background/browser notification.

The database record is authoritative. Realtime and FCM are delivery hints; the
client always recovers missed events by refreshing notification history.

## Channel defaults

| Channel | Default | Provider |
|---|---:|---|
| In app | On | Supabase Realtime |
| Email | Off | SendGrid |
| Push | Off | Firebase Cloud Messaging, optional |
| SMS | Off | Firebase Cloud Function/Twilio, optional |

Changing a preference affects subsequent notifications. Transactional security
messages may explicitly force a channel when policy requires it.

## Vercel boundary

The frontend is ready for Vercel. The notification transport does not require a
long-running application process, sticky sessions, or a Redis pub/sub layer.

Before moving the FastAPI API itself to Vercel:

- replace long-lived lifespan loops with Vercel Cron or a durable scheduler;
- replace request-local fire-and-forget tasks with Vercel Queues or a database
  outbox worker;
- confirm the Python function bundle, including the local classification model,
  fits the selected Vercel function tier;
- keep the function region close to the Supabase project;
- store all server secrets in Vercel project environment variables;
- set `REACT_APP_API_URL` to the deployed API origin or its `/api` prefix;
- set `ALLOWED_ORIGINS` to the exact production/preview frontend origins;
- add the final custom API origin to the frontend Content Security Policy.

The current SendGrid provider retries transient failures during an invocation.
Cross-invocation durable email retries should be moved to Vercel Queues during
the API migration. Do not describe email as exactly-once delivery until that
queue consumer and dead-letter monitoring are deployed.

## Required rollout order

1. Back up the database.
2. Apply `alembic upgrade head`. Migration
   `044_secure_notification_realtime` restricts the old broad notification
   policies before adding the table to `supabase_realtime`.
3. Deploy the API with `SENDGRID_API_KEY`, `FROM_EMAIL`, and `FROM_NAME` if
   email is enabled.
4. Deploy the frontend with the Supabase URL/key and API URL.
5. Test with one account using `/api/v1/notifications/test` and the `in_app`
   channel.
6. Confirm that a second authenticated account cannot read or receive the first
   account's notification.
7. Opt a test user into email and verify SendGrid accepted delivery.
8. Monitor API errors, Supabase Realtime connections, and SendGrid failures
   during staged rollout.

## Operational acceptance checks

- Creating one event creates exactly one notification row.
- The badge and notification list update without a page reload.
- Closing and reopening the tab recovers every notification from history.
- Marking one or all notifications read updates the badge consistently.
- Disabling in-app toasts does not remove notification history.
- Missing Firebase credentials do not prevent API startup.
- Email status reports configured only when both the SendGrid key and sender
  address exist.
- RLS tests prove cross-user reads and updates are rejected.
