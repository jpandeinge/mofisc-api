# Local Supabase connection recovery

The September 14–15 development incidents affected two independent paths:
SQLAlchemy/psycopg2 connections to the PostgreSQL pooler and HTTPX connections to
Supabase Auth/PostgREST. A connection reset or timeout is an availability failure,
not evidence of invalid login credentials or an empty account. The traces alone
cannot distinguish laptop sleep, a local network interruption, and a provider outage.

## Implemented protections

- SQLAlchemy now uses the configured pool size/overflow/wait/recycle values,
  validates idle connections with `pool_pre_ping`, and recycles them after five
  minutes by default. PostgreSQL connects time out after five seconds; TCP
  keepalives help detect dead sockets (subject to OS support).
- Initial PostgreSQL connection establishment gets at most one retry on a known
  transport failure. Failed transactions are invalidated, never replayed: some
  achievement reads also create challenges or update points.
- A disconnected socket during session cleanup no longer turns a completed or
  already-failed request into a second rollback error. Other database errors
  remain errors and are not indiscriminately suppressed.
- Auth, notification reads, subscription entitlement reads, and metrics
  calculations run in worker threads, not the async event loop.
- Selected read-only Supabase operations get at most two attempts, using a new,
  explicitly closed HTTP client each time. Connect and read timeouts default to
  three and five seconds. These are per-I/O limits, not an overall route deadline.
  Writes, token refreshes and payment operations are not automatically replayed.
- Exhausted connection failures return sanitized HTTP 503 with `Retry-After: 5`.
  Notification outages are not reported as empty inboxes; entitlement outages
  are not reported as free subscriptions. Activity does not persist fallback XP.
  Recovery warnings remain breadcrumbs, while errors/failed HTTP responses
  remain observable in Sentry. INFO logs no longer create individual issues,
  and SDK debug output follows the explicit DEBUG setting. Sentry is not disabled.

Restart the API after updating. Keep real credentials in the ignored `.env` file.
See `.env.example` for the timeout and pool settings. Existing explicit environment
overrides still take precedence.

## Verification and limits

`pytest tests/unit/test_connection_resilience.py` injects dropped connections,
TLS/read failures and rollback failures. It checks bounded retries, non-replayed
transactions, response contracts, client cleanup and event-loop responsiveness.

If 503s persist, check connectivity to the Supabase pooler and HTTPS API, whether
the project is available, and whether the laptop has just resumed from sleep.
Do not disable TLS verification, authentication, RLS or monitoring to hide these
errors. Provider/network outages cannot be eliminated by client retries.

References: [SQLAlchemy disconnect handling](https://docs.sqlalchemy.org/en/20/core/pooling.html#dealing-with-disconnects),
[PostgreSQL connection settings](https://www.postgresql.org/docs/current/libpq-connect.html),
[HTTPX transport behavior](https://www.python-httpx.org/advanced/transports/).
