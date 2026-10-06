import asyncio
import importlib
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import psycopg2
import pytest
from fastapi import FastAPI
from postgrest.exceptions import APIError
from sqlalchemy.exc import DatabaseError, OperationalError

from api.app import sentry
from api.app.utils import auth, db, subscription, supabase_utils
from api.app.utils.upstream import UpstreamUnavailable

achievement_routes = importlib.import_module("api.app.routes.achievement_routes")
notification_routes = importlib.import_module("api.app.routes.notification_routes")
user_metrics_routes = importlib.import_module("api.app.routes.user_metrics_routes")
subscription_routes = importlib.import_module("api.app.routes.subscription_routes")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def dropped_connection():
    return OperationalError(
        "SELECT 1",
        {},
        psycopg2.OperationalError("server closed the connection unexpectedly"),
    )


def test_engine_uses_pool_health_checks_and_configured_limits():
    assert db.engine.pool._pre_ping is True
    assert db.engine.pool._recycle == db.settings.DB_POOL_RECYCLE
    assert db.engine.pool.size() == db.settings.DB_POOL_SIZE
    assert db.engine.pool.timeout() == db.settings.DB_POOL_TIMEOUT


def test_connect_retries_once_before_any_transaction(monkeypatch):
    monkeypatch.setattr(db, "sleep", lambda _: None)
    connect = Mock(side_effect=[dropped_connection().orig, "connection"])
    dialect = SimpleNamespace(loaded_dbapi=SimpleNamespace(connect=connect))
    assert (
        db.connect_with_retry(dialect, None, (), {"connect_timeout": 5}) == "connection"
    )
    assert connect.call_count == 2


def test_connect_outage_is_bounded(monkeypatch):
    monkeypatch.setattr(db, "sleep", lambda _: None)
    connect = Mock(side_effect=dropped_connection().orig)
    dialect = SimpleNamespace(loaded_dbapi=SimpleNamespace(connect=connect))
    with pytest.raises(psycopg2.OperationalError):
        db.connect_with_retry(dialect, None, (), {})
    assert connect.call_count == 2


def test_credentials_errors_are_not_retried(monkeypatch):
    connect = Mock(
        side_effect=psycopg2.OperationalError("password authentication failed")
    )
    dialect = SimpleNamespace(loaded_dbapi=SimpleNamespace(connect=connect))
    with pytest.raises(psycopg2.OperationalError):
        db.connect_with_retry(dialect, None, (), {})
    assert connect.call_count == 1


def test_failed_transaction_is_invalidated_not_replayed():
    session = Mock()
    operation = Mock(side_effect=dropped_connection())
    with pytest.raises(UpstreamUnavailable) as exc:
        db.run_db_task(session, operation, "user")
    assert exc.value.status_code == 503
    assert exc.value.headers == {"Retry-After": "5"}
    operation.assert_called_once_with(session, "user")
    session.invalidate.assert_called_once()


def test_non_disconnect_database_error_is_not_hidden():
    error = DatabaseError("SELECT 1", {}, psycopg2.DatabaseError("invalid query"))
    with pytest.raises(DatabaseError) as exc:
        db.run_db_task(Mock(), Mock(side_effect=error))
    assert exc.value is error


@pytest.mark.parametrize("prior_failure", [False, True])
def test_rollback_disconnect_does_not_replace_response_or_original_error(
    monkeypatch, prior_failure
):
    session = Mock()
    session.close.side_effect = DatabaseError(
        "ROLLBACK", {}, psycopg2.DatabaseError("SSL SYSCALL error: Operation timed out")
    )
    session.invalidate.side_effect = dropped_connection()
    monkeypatch.setattr(db, "SessionLocal", lambda: session)
    dependency = db.get_db()
    assert next(dependency) is session
    if prior_failure:
        original = RuntimeError("original request failure")
        with pytest.raises(RuntimeError) as exc:
            dependency.throw(original)
        assert exc.value is original
    else:
        with pytest.raises(StopIteration):
            next(dependency)
    session.invalidate.assert_called_once()


@pytest.fixture
def read_transport(monkeypatch):
    clients = []
    real_client = httpx.Client

    def install(handler):
        def client_factory(**kwargs):
            client = real_client(transport=httpx.MockTransport(handler), **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(supabase_utils.httpx, "Client", client_factory)
        monkeypatch.setattr(
            supabase_utils,
            "get_supabase_client",
            lambda **kwargs: kwargs["http_client"],
        )
        monkeypatch.setattr(supabase_utils, "sleep", lambda _: None)
        return clients

    return install


@pytest.mark.parametrize(
    "error_class",
    [httpx.ConnectError, httpx.ReadTimeout, httpx.ReadError, httpx.RemoteProtocolError],
)
def test_supabase_read_recovers_using_fresh_closed_clients(read_transport, error_class):
    attempts = []

    def handle(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise error_class("connection interrupted", request=request)
        return httpx.Response(200, json={"ok": True})

    clients = read_transport(handle)
    result = supabase_utils.run_supabase_read(
        lambda client: client.get("https://example.invalid").json()
    )
    assert result == {"ok": True}
    assert len(attempts) == 2
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert all(client.is_closed for client in clients)
    assert (
        clients[0].timeout.connect == supabase_utils.settings.SUPABASE_CONNECT_TIMEOUT
    )
    assert clients[0].timeout.read == supabase_utils.settings.SUPABASE_READ_TIMEOUT


def test_exhausted_read_returns_sanitized_503_not_empty_data(read_transport):
    def handle(request):
        raise httpx.ReadTimeout("private connection details", request=request)

    clients = read_transport(handle)
    with pytest.raises(UpstreamUnavailable) as exc:
        supabase_utils.run_supabase_read(
            lambda client: client.get("https://example.invalid")
        )
    assert exc.value.status_code == 503
    assert "private" not in exc.value.detail
    assert len(clients) == 2 and all(client.is_closed for client in clients)


def test_postgrest_schema_errors_are_not_retried(read_transport):
    clients = read_transport(lambda request: httpx.Response(200))
    operation = Mock(
        side_effect=APIError(
            {"message": "missing table", "code": "42P01", "details": None, "hint": None}
        )
    )
    with pytest.raises(APIError):
        supabase_utils.run_supabase_read(operation)
    operation.assert_called_once()
    assert len(clients) == 1 and clients[0].is_closed


@pytest.fixture
def test_app(monkeypatch):
    app = FastAPI()
    app.include_router(notification_routes.router, prefix="/notifications")
    app.include_router(achievement_routes.router, prefix="/achievements")
    app.include_router(user_metrics_routes.router)
    app.include_router(subscription_routes.router, prefix="/billing")
    app.dependency_overrides[auth.get_current_user] = lambda: SimpleNamespace(
        id="user-1"
    )
    app.dependency_overrides[db.get_db] = lambda: Mock()
    monkeypatch.setattr(notification_routes.limiter, "enabled", False)
    return app


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path", ["/notifications/history", "/notifications/unread-count"]
)
async def test_notification_outage_contract(test_app, monkeypatch, path):
    monkeypatch.setattr(
        notification_routes,
        "run_supabase_read",
        Mock(side_effect=UpstreamUnavailable("Data service")),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert "temporarily unavailable" in response.json()["detail"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path,method",
    [
        ("/achievements/challenges", "get_active_challenges"),
        ("/achievements/user/stats", "get_user_game_stats"),
    ],
)
async def test_achievement_disconnect_is_not_fake_zero_progress(
    test_app, monkeypatch, path, method
):
    operation = Mock(side_effect=dropped_connection())
    monkeypatch.setattr(achievement_routes.achievement_service, method, operation)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 503
    operation.assert_called_once()


@pytest.mark.anyio
async def test_subscription_outage_does_not_calculate_or_persist_free_plan_metrics(
    test_app, monkeypatch
):
    monkeypatch.setattr(
        subscription,
        "run_supabase_read",
        Mock(side_effect=UpstreamUnavailable("Data service")),
    )
    calculate = Mock()
    monkeypatch.setattr(
        user_metrics_routes.user_metrics_service,
        "calculate_comprehensive_user_metrics",
        calculate,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get("/metrics")
    assert response.status_code == 503
    calculate.assert_not_called()


@pytest.mark.anyio
async def test_slow_subscription_lookup_does_not_block_event_loop(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def read(operation):
        started.set()
        assert release.wait(timeout=2)
        return {"plan": "premium"}

    monkeypatch.setattr(subscription, "run_supabase_read", read)
    task = asyncio.create_task(subscription.get_user_subscription("user-1"))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()
        assert not task.done()
    finally:
        release.set()
    assert await task == {"plan": "premium"}


@pytest.mark.anyio
async def test_auth_network_failure_is_not_invalid_credentials(monkeypatch):
    monkeypatch.setattr(
        auth, "run_supabase_read", Mock(side_effect=UpstreamUnavailable("Data service"))
    )
    with pytest.raises(UpstreamUnavailable) as exc:
        await auth.get_current_user(
            SimpleNamespace(), SimpleNamespace(credentials="test-token")
        )
    assert exc.value.status_code == 503


@pytest.mark.anyio
async def test_activity_disconnect_does_not_sync_fallback_points(test_app, monkeypatch):
    monkeypatch.setattr(
        achievement_routes,
        "get_user_subscription",
        AsyncMock(return_value={"plan": "free"}),
    )
    monkeypatch.setattr(
        achievement_routes.user_metrics_service,
        "calculate_comprehensive_user_metrics",
        Mock(return_value={}),
    )
    monkeypatch.setattr(
        achievement_routes.achievement_service,
        "get_user_game_stats",
        Mock(side_effect=dropped_connection()),
    )
    sync = Mock()
    monkeypatch.setattr(
        achievement_routes.achievement_service,
        "sync_user_points_with_activity_points",
        sync,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get("/achievements/user/activity")
    assert response.status_code == 503
    sync.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path", ["/billing/subscription-status/user-1", "/billing/usage/user-1"]
)
async def test_billing_preserves_retryable_outage_response(test_app, monkeypatch, path):
    monkeypatch.setattr(
        subscription_routes,
        "get_user_subscription",
        AsyncMock(side_effect=UpstreamUnavailable("Data service")),
    )
    monkeypatch.setattr(subscription_routes, "get_supabase_client", Mock())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(path)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


@pytest.mark.anyio
async def test_notification_read_success_contract_and_user_scope(test_app, monkeypatch):
    notification = {
        "id": "notification-1",
        "notification_type": "system_alert",
        "title": "Test",
        "body": "Body",
        "data": {},
        "is_read": False,
        "priority": "medium",
        "created_at": "2026-09-19T10:00:00Z",
    }
    query = Mock()
    for method in ("table", "select", "eq", "order", "range"):
        getattr(query, method).return_value = query
    query.execute.side_effect = [
        SimpleNamespace(data=[notification], count=1),
        SimpleNamespace(count=1),
    ]
    monkeypatch.setattr(
        notification_routes, "run_supabase_read", lambda operation: operation(query)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get("/notifications/history?limit=10&offset=0")
    assert response.status_code == 200
    assert response.json()["notifications"][0]["type"] == "system_alert"
    assert response.json()["total"] == response.json()["unread_count"] == 1
    assert query.eq.call_args_list.count((("user_id", "user-1"),)) == 2
    query.range.assert_called_once_with(0, 9)


@pytest.mark.anyio
@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
async def test_notification_invalid_pagination_is_rejected_before_io(
    test_app, monkeypatch, query
):
    read = Mock()
    monkeypatch.setattr(notification_routes, "run_supabase_read", read)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(f"/notifications/history?{query}")
    assert response.status_code == 422
    read.assert_not_called()


def test_subscription_absence_is_still_free_after_successful_lookup(monkeypatch):
    query = Mock()
    for method in ("table", "select", "eq", "limit"):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(data=[])
    monkeypatch.setattr(
        subscription, "get_organisation_membership", Mock(return_value=None)
    )
    result = subscription._read_user_subscription(query, "user-1")
    assert result["plan"] == "free" and result["has_active_access"] is True
    query.eq.assert_called_once_with("user_id", "user-1")


def test_subscription_keeps_workspace_scope_and_paid_entitlements(monkeypatch):
    query = Mock()
    for method in ("table", "select", "eq", "limit"):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(
        data=[{"plan": "enterprise", "status": "active"}]
    )
    monkeypatch.setattr(
        subscription,
        "get_organisation_membership",
        Mock(return_value={"organisation_id": "workspace-1"}),
    )
    result = subscription._read_user_subscription(query, "user-1")
    assert result["plan"] == "enterprise"
    query.eq.assert_called_once_with("organisation_id", "workspace-1")


@pytest.mark.anyio
async def test_notification_read_is_off_the_event_loop(test_app, monkeypatch):
    main_thread = threading.get_ident()

    def read(operation):
        assert threading.get_ident() != main_thread
        return 7

    monkeypatch.setattr(notification_routes, "run_supabase_read", read)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get("/notifications/unread-count")
    assert response.status_code == 200
    assert response.json() == {"unread_count": 7}


def test_sentry_keeps_error_reporting_and_debug_is_opt_in(monkeypatch):
    monkeypatch.setattr(sentry.settings, "SENTRY_DSN", "https://test@example.invalid/1")
    monkeypatch.setattr(sentry.settings, "DEBUG", False)
    integration = Mock()
    initialize = Mock()
    monkeypatch.setattr(sentry, "LoggingIntegration", integration)
    monkeypatch.setattr(sentry.sentry_sdk, "init", initialize)
    sentry.init_sentry()
    assert integration.call_args.kwargs["event_level"] == logging.ERROR
    assert integration.call_args.kwargs["level"] == logging.DEBUG
    assert initialize.call_args.kwargs["debug"] is False
    assert initialize.call_args.kwargs["dsn"]


@pytest.mark.parametrize(
    "user",
    [
        {"email": "test@example.invalid"},
        SimpleNamespace(email="test@example.invalid"),
        SimpleNamespace(id="user-1"),
    ],
)
def test_sentry_accepts_authenticated_user_shapes_without_exposing_email(
    monkeypatch, user
):
    capture = Mock()
    monkeypatch.setattr(sentry, "set_user", capture)
    sentry.set_sentry_user(user)
    payload = capture.call_args.args[0]
    assert payload["id"] == "[USER_ID]"
    assert "test@" not in str(payload)
