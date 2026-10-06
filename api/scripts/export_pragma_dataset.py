"""Export Galahad user history into Pragmatiq's raw parquet contract.

The default export contains only observed, point-in-time facts:

- raw transactions (never predicted category or merchant);
- goal-history events;
- budget creation/current-state events;
- Galahad billing-plan events; and
- a PII-minimised profile snapshot.

Detected recurring subscriptions are derived from transactions and are excluded
unless ``--include-derived-events`` is explicit. Aggregate financial metrics are
not exported because they duplicate transactions and depend on current category
labels. User corrections are written to ``supervised_feedback.parquet`` for the
transaction model, never inserted into the PRAGMA pretraining event stream.

The PostgreSQL source is the default because it supports a read-only streaming
cursor and does not depend on the Supabase service-role REST key. ``supabase``
remains available for deployments that provide a valid server secret.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../api

from app.config.settings import settings  # noqa: E402
from app.utils.supabase_utils import get_supabase_client  # noqa: E402

EVENTS_SCHEMA = pa.schema(
    [
        pa.field("user_id", pa.string(), nullable=False),
        pa.field("ts", pa.timestamp("us"), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("fields", pa.map_(pa.string(), pa.string()), nullable=False),
    ]
)

PROFILES_SCHEMA = pa.schema(
    [
        pa.field("user_id", pa.string(), nullable=False),
        pa.field("as_of", pa.timestamp("us"), nullable=False),
        pa.field("attributes", pa.map_(pa.string(), pa.string()), nullable=False),
        pa.field(
            "lifelong",
            pa.list_(
                pa.struct(
                    [pa.field("key", pa.string()), pa.field("ts", pa.timestamp("us"))]
                )
            ),
            nullable=False,
        ),
    ]
)

FEEDBACK_SCHEMA = pa.schema(
    [
        pa.field("user_id", pa.string(), nullable=False),
        pa.field("transaction_ref", pa.string(), nullable=False),
        pa.field("edited_at", pa.timestamp("us"), nullable=False),
        pa.field("field_name", pa.string(), nullable=False),
        pa.field("previous_value", pa.string()),
        pa.field("corrected_value", pa.string(), nullable=False),
        pa.field("description", pa.string(), nullable=False),
        pa.field("amount", pa.float64()),
        pa.field("direction", pa.string()),
        pa.field("bank", pa.string()),
    ]
)


@dataclass(frozen=True)
class ExportEvent:
    raw_user_id: str
    ts_us: int
    source: str
    fields: dict[str, str]
    source_table: str
    entity_id: str


@dataclass(frozen=True)
class EventSpec:
    table: str
    columns: tuple[str, ...]
    builder: Callable[[Mapping[str, Any]], ExportEvent | None]
    derived: bool = False


def day_to_us(day: str) -> int:
    """Day-level date string -> microseconds since epoch at 00:00 UTC."""
    dt = datetime.strptime(day[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000)


def to_us(value: Any) -> int:
    """Convert a date/datetime/ISO value to UTC epoch microseconds."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return int(dt.astimezone(UTC).timestamp() * 1_000_000)
    if isinstance(value, date):
        return day_to_us(value.isoformat())
    if isinstance(value, (int, float)):
        return int(value)
    text_value = str(value or "").strip()
    if not text_value:
        raise ValueError("timestamp value is empty")
    if len(text_value) == 10:
        return day_to_us(text_value)
    dt = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.astimezone(UTC).timestamp() * 1_000_000)


def pseudonymize_user_id(user_id: object, key: bytes) -> str:
    """Create a stable, non-reversible identifier for PRAGMA artifacts."""
    digest = hmac.new(key, str(user_id).encode("utf-8"), hashlib.sha256).hexdigest()
    return f"usr_{digest}"


def pseudonymize_transaction_id(transaction_id: object, key: bytes) -> str:
    digest = hmac.new(
        key, f"transaction:{transaction_id}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"txn_{digest}"


def _field_map(**values: Any) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            fields[key] = "true" if value else "false"
            continue
        cleaned = str(value).strip()
        if cleaned:
            fields[key] = cleaned
    return fields


def _required(row: Mapping[str, Any], key: str) -> Any:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"row has no {key}")
    return value


def _transaction_event(row: Mapping[str, Any]) -> ExportEvent:
    # Category, confidence and merchant fields are intentionally absent.
    fields = _field_map(
        event_type="transaction",
        description=str(row.get("transaction_description") or "").lower(),
        amount=abs(float(row.get("transaction_amount") or 0.0)),
        direction=str(row.get("transaction_type") or "unknown").lower(),
        bank=str(row.get("bank_name") or "unknown").lower(),
        balance=row.get("balance"),
    )
    return ExportEvent(
        raw_user_id=str(_required(row, "user_id")),
        ts_us=to_us(_required(row, "transaction_date")),
        source="transaction",
        fields=fields,
        source_table="transactions",
        entity_id=str(_required(row, "id")),
    )


def _goal_history_event(row: Mapping[str, Any]) -> ExportEvent:
    action = str(row.get("event_type") or "snapshot").lower()
    fields = _field_map(
        event_type=f"goal_{action}",
        goal_category=row.get("category"),
        target_amount=row.get("target_amount"),
        current_amount=row.get("current_amount"),
        deadline=row.get("deadline"),
        goal_status=row.get("status"),
    )
    # Goal name and arbitrary JSON metadata are excluded: neither is necessary
    # for the behavioral representation and both can contain personal text.
    return ExportEvent(
        raw_user_id=str(_required(row, "user_id")),
        ts_us=to_us(_required(row, "event_timestamp")),
        source="app",
        fields=fields,
        source_table="goals_history",
        entity_id=str(_required(row, "id")),
    )


def _budget_event(row: Mapping[str, Any]) -> ExportEvent:
    fields = _field_map(
        event_type="budget_created",
        budget_category=row.get("category"),
        budget_amount=row.get("amount"),
        budget_period=row.get("period"),
        budget_end_date=row.get("end_date"),
        alert_threshold=row.get("alert_threshold"),
        is_active=row.get("is_active"),
    )
    return ExportEvent(
        raw_user_id=str(_required(row, "user_id")),
        ts_us=to_us(_required(row, "created_at")),
        source="app",
        fields=fields,
        source_table="budgets",
        entity_id=str(_required(row, "id")),
    )


def _billing_subscription_event(row: Mapping[str, Any]) -> ExportEvent:
    fields = _field_map(
        event_type="galahad_plan_started",
        plan=row.get("plan"),
        plan_status=row.get("status"),
        cancel_at_period_end=row.get("cancel_at_period_end"),
        current_period_start=row.get("current_period_start"),
        current_period_end=row.get("current_period_end"),
    )
    # Provider customer/subscription identifiers are deliberately excluded.
    return ExportEvent(
        raw_user_id=str(_required(row, "user_id")),
        ts_us=to_us(_required(row, "created_at")),
        source="app",
        fields=fields,
        source_table="billing_subscriptions",
        entity_id=str(_required(row, "id")),
    )


def _derived_subscription_event(row: Mapping[str, Any]) -> ExportEvent:
    fields = _field_map(
        event_type="recurring_candidate_detected",
        recurring_amount=row.get("amount"),
        recurring_frequency=row.get("frequency"),
        transaction_count=row.get("transaction_count"),
        detection_confidence=row.get("confidence_score"),
        is_active=row.get("is_active"),
        is_paused=row.get("is_paused"),
    )
    # name, description_pattern and category are outputs of the current
    # detection/classification stack. Feeding them back would reinforce errors.
    event_time = row.get("first_detected") or row.get("created_at")
    return ExportEvent(
        raw_user_id=str(_required(row, "user_id")),
        ts_us=to_us(_required({"ts": event_time}, "ts")),
        source="app",
        fields=fields,
        source_table="user_subscriptions",
        entity_id=str(_required(row, "id")),
    )


EVENT_SPECS = (
    EventSpec(
        table="transactions",
        columns=(
            "id",
            "user_id",
            "bank_name",
            "transaction_date",
            "transaction_description",
            "transaction_amount",
            "transaction_type",
            "balance",
        ),
        builder=_transaction_event,
    ),
    EventSpec(
        table="goals_history",
        columns=(
            "id",
            "user_id",
            "target_amount",
            "current_amount",
            "deadline",
            "category",
            "status",
            "event_type",
            "event_timestamp",
        ),
        builder=_goal_history_event,
    ),
    EventSpec(
        table="budgets",
        columns=(
            "id",
            "user_id",
            "category",
            "amount",
            "period",
            "end_date",
            "alert_threshold",
            "is_active",
            "created_at",
        ),
        builder=_budget_event,
    ),
    EventSpec(
        table="billing_subscriptions",
        columns=(
            "id",
            "user_id",
            "plan",
            "status",
            "current_period_start",
            "current_period_end",
            "cancel_at_period_end",
            "created_at",
        ),
        builder=_billing_subscription_event,
    ),
    EventSpec(
        table="user_subscriptions",
        columns=(
            "id",
            "user_id",
            "amount",
            "frequency",
            "first_detected",
            "transaction_count",
            "confidence_score",
            "is_active",
            "is_paused",
            "created_at",
        ),
        builder=_derived_subscription_event,
        derived=True,
    ),
)

PROFILE_COLUMNS = (
    "id",
    "user_type",
    "is_active",
    "created_at",
    "updated_at",
)

FEEDBACK_COLUMNS = (
    "id",
    "transaction_id",
    "user_id",
    "field_name",
    "old_value",
    "new_value",
    "edited_at",
)

_DATABASE_ENGINE = None


def _quoted_columns(columns: Iterable[str]) -> str:
    # All callers use constants declared above. Quoting also protects names that
    # could become SQL keywords in future schema revisions.
    return ", ".join(f'"{column}"' for column in columns)


def _database_rows(
    table: str, columns: tuple[str, ...], batch_size: int
) -> Iterator[dict[str, Any]]:
    global _DATABASE_ENGINE
    allowed_tables = {spec.table for spec in EVENT_SPECS} | {
        "user_profiles",
        "transaction_edit_history",
    }
    if table not in allowed_tables:
        raise ValueError(f"table not allowlisted for PRAGMA export: {table}")
    if _DATABASE_ENGINE is None:
        _DATABASE_ENGINE = create_engine(settings.DATABASE_URL)
    query = text(f'SELECT {_quoted_columns(columns)} FROM public."{table}"')
    with _DATABASE_ENGINE.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        streaming_connection = connection.execution_options(stream_results=True)
        result = streaming_connection.execute(query)
        while True:
            rows = result.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                yield dict(row._mapping)


def _supabase_rows(
    table: str, columns: tuple[str, ...], batch_size: int
) -> Iterator[dict[str, Any]]:
    client = get_supabase_client(use_service_role=True)
    offset = 0
    while True:
        page = (
            client.table(table)
            .select(",".join(columns))
            .range(offset, offset + batch_size - 1)
            .execute()
            .data
        )
        if not page:
            break
        yield from page
        offset += len(page)
        if len(page) < batch_size:
            break


def _row_loader(
    source: str,
) -> Callable[[str, tuple[str, ...], int], Iterator[dict[str, Any]]]:
    return _database_rows if source == "database" else _supabase_rows


def _profile_rows(
    profiles: Iterable[Mapping[str, Any]],
    events: list[ExportEvent],
    pseudonym_key: bytes,
    allow_raw_ids: bool,
) -> list[dict[str, Any]]:
    by_user = {str(row["id"]): dict(row) for row in profiles if row.get("id")}
    events_by_user: dict[str, list[ExportEvent]] = defaultdict(list)
    for event in events:
        events_by_user[event.raw_user_id].append(event)

    all_user_ids = sorted(set(by_user) | set(events_by_user))
    result: list[dict[str, Any]] = []
    for raw_user_id in all_user_ids:
        profile = by_user.get(raw_user_id, {})
        user_events = events_by_user.get(raw_user_id, [])
        event_times = [event.ts_us for event in user_events]
        created_us = to_us(profile["created_at"]) if profile.get("created_at") else None
        updated_us = to_us(profile["updated_at"]) if profile.get("updated_at") else None
        as_of = max(
            [
                value
                for value in [created_us, updated_us, *event_times]
                if value is not None
            ],
            default=int(datetime.now(UTC).timestamp() * 1_000_000),
        )

        attributes = _field_map(
            user_type=profile.get("user_type"),
            is_active=profile.get("is_active"),
        )
        milestones: list[dict[str, Any]] = []
        if created_us is not None:
            milestones.append({"key": "profile_created", "ts": created_us})
        milestone_types = {
            "transaction": "first_transaction",
            "goal_": "first_goal_event",
            "budget_created": "first_budget",
            "galahad_plan_started": "first_galahad_plan",
        }
        for marker, milestone_name in milestone_types.items():
            matching = [
                event.ts_us
                for event in user_events
                if (
                    event.fields.get("event_type") == marker
                    or event.fields.get("event_type", "").startswith(marker)
                )
            ]
            if matching:
                milestones.append({"key": milestone_name, "ts": min(matching)})

        user_id = (
            raw_user_id
            if allow_raw_ids
            else pseudonymize_user_id(raw_user_id, pseudonym_key)
        )
        result.append(
            {
                "user_id": user_id,
                "as_of": as_of,
                "attributes": list(attributes.items()),
                "lifelong": sorted(
                    milestones, key=lambda item: (item["ts"], item["key"])
                ),
            }
        )
    return result


def _feedback_rows(
    edits: Iterable[Mapping[str, Any]],
    transactions_by_id: Mapping[str, Mapping[str, Any]],
    pseudonym_key: bytes,
    allow_raw_ids: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for edit in edits:
        field_name = str(edit.get("field_name") or "")
        corrected = edit.get("new_value")
        tx = transactions_by_id.get(str(edit.get("transaction_id")))
        if (
            field_name not in {"category", "merchant_name"}
            or corrected is None
            or tx is None
            or not edit.get("edited_at")
        ):
            continue
        raw_user_id = str(edit["user_id"])
        rows.append(
            {
                "user_id": (
                    raw_user_id
                    if allow_raw_ids
                    else pseudonymize_user_id(raw_user_id, pseudonym_key)
                ),
                "transaction_ref": (
                    str(edit["transaction_id"])
                    if allow_raw_ids
                    else pseudonymize_transaction_id(
                        edit["transaction_id"], pseudonym_key
                    )
                ),
                "edited_at": to_us(edit["edited_at"]),
                "field_name": field_name,
                "previous_value": (
                    None if edit.get("old_value") is None else str(edit["old_value"])
                ),
                "corrected_value": str(corrected),
                "description": str(tx.get("transaction_description") or ""),
                "amount": (
                    None
                    if tx.get("transaction_amount") is None
                    else float(tx["transaction_amount"])
                ),
                "direction": (
                    None
                    if tx.get("transaction_type") is None
                    else str(tx["transaction_type"])
                ),
                "bank": (None if tx.get("bank_name") is None else str(tx["bank_name"])),
            }
        )
    return rows


def _write_events(
    out_dir: Path,
    events: list[ExportEvent],
    pseudonym_key: bytes,
    allow_raw_ids: bool,
) -> pa.Table:
    ordered = sorted(
        events,
        key=lambda event: (
            event.raw_user_id,
            event.ts_us,
            event.source,
            event.fields.get("event_type", ""),
            event.entity_id,
        ),
    )
    table = pa.table(
        {
            "user_id": pa.array(
                [
                    (
                        event.raw_user_id
                        if allow_raw_ids
                        else pseudonymize_user_id(event.raw_user_id, pseudonym_key)
                    )
                    for event in ordered
                ],
                pa.string(),
            ),
            "ts": pa.array([event.ts_us for event in ordered], pa.timestamp("us")),
            "source": pa.array([event.source for event in ordered], pa.string()),
            "fields": pa.array(
                [list(event.fields.items()) for event in ordered],
                pa.map_(pa.string(), pa.string()),
            ),
        },
        schema=EVENTS_SCHEMA,
    )
    pq.write_table(table, out_dir / "events.parquet")
    return table


def main() -> int:
    global _DATABASE_ENGINE
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        default=str(
            Path(__file__).resolve().parents[2] / "ml_model" / "data" / "pragma" / "raw"
        ),
    )
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument(
        "--source",
        choices=("database", "supabase"),
        default=os.environ.get("PRAGMA_EXPORT_SOURCE", "database"),
    )
    parser.add_argument(
        "--include-derived-events",
        action="store_true",
        help="include detected recurring-subscription summaries with derived labels removed",
    )
    parser.add_argument(
        "--allow-raw-user-ids",
        action="store_true",
        help="development escape hatch; write database UUIDs instead of HMAC pseudonyms",
    )
    args = parser.parse_args()

    pseudonym_key = os.environ.get("PRAGMA_PSEUDONYM_KEY", "").encode("utf-8")
    if not pseudonym_key and not args.allow_raw_user_ids:
        parser.error(
            "PRAGMA_PSEUDONYM_KEY is required. Set a dedicated secret or pass "
            "--allow-raw-user-ids only for an isolated local smoke test."
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    load_rows = _row_loader(args.source)

    events: list[ExportEvent] = []
    source_counts: Counter[str] = Counter()
    transactions_by_id: dict[str, Mapping[str, Any]] = {}
    for spec in EVENT_SPECS:
        if spec.derived and not args.include_derived_events:
            continue
        table_rows = list(load_rows(spec.table, spec.columns, args.batch_size))
        for row in table_rows:
            event = spec.builder(row)
            if event is None:
                continue
            events.append(event)
            source_counts[spec.table] += 1
            if spec.table == "transactions":
                transactions_by_id[str(row["id"])] = row
        print(f"fetched {len(table_rows):,} rows from {spec.table}")

    profiles_raw = list(load_rows("user_profiles", PROFILE_COLUMNS, args.batch_size))
    edits_raw = list(
        load_rows("transaction_edit_history", FEEDBACK_COLUMNS, args.batch_size)
    )
    profiles = _profile_rows(
        profiles_raw, events, pseudonym_key, args.allow_raw_user_ids
    )
    feedback = _feedback_rows(
        edits_raw,
        transactions_by_id,
        pseudonym_key,
        args.allow_raw_user_ids,
    )

    events_table = _write_events(
        out_dir, events, pseudonym_key, args.allow_raw_user_ids
    )
    profiles_table = pa.table(
        {
            "user_id": pa.array([row["user_id"] for row in profiles], pa.string()),
            "as_of": pa.array([row["as_of"] for row in profiles], pa.timestamp("us")),
            "attributes": pa.array(
                [row["attributes"] for row in profiles],
                pa.map_(pa.string(), pa.string()),
            ),
            "lifelong": pa.array(
                [row["lifelong"] for row in profiles],
                pa.list_(
                    pa.struct(
                        [
                            pa.field("key", pa.string()),
                            pa.field("ts", pa.timestamp("us")),
                        ]
                    )
                ),
            ),
        },
        schema=PROFILES_SCHEMA,
    )
    pq.write_table(profiles_table, out_dir / "profiles.parquet")

    feedback_table = pa.Table.from_pylist(feedback, schema=FEEDBACK_SCHEMA)
    pq.write_table(feedback_table, out_dir / "supervised_feedback.parquet")

    event_types = Counter(event.fields.get("event_type", "unknown") for event in events)
    snapshot_us = max(
        [row["as_of"] for row in profiles],
        default=int(datetime.now(UTC).timestamp() * 1_000_000),
    )
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot_as_of": datetime.fromtimestamp(
            snapshot_us / 1_000_000, UTC
        ).isoformat(),
        "source": args.source,
        "event_count": events_table.num_rows,
        "profile_count": profiles_table.num_rows,
        "supervised_feedback_count": feedback_table.num_rows,
        "source_table_counts": dict(sorted(source_counts.items())),
        "event_type_counts": dict(sorted(event_types.items())),
        "user_ids_pseudonymized": not args.allow_raw_user_ids,
        "derived_events_included": args.include_derived_events,
        "excluded_pii": [
            "full_name",
            "email",
            "phone",
            "profile_picture",
            "goal_name",
            "goal_metadata",
            "provider_customer_id",
            "provider_subscription_id",
        ],
        "excluded_model_outputs": [
            "transaction.category",
            "transaction.confidence",
            "transaction.merchant_name",
            "recurring.name",
            "recurring.description_pattern",
            "recurring.category",
        ],
        "excluded_derived_tables": [
            "user_financial_metrics",
            "user_cash_flow_data",
            "user_category_spending",
            "user_monthly_trends",
        ],
        "known_limitations": [
            "transaction_date is day-level; same-day event ordering is synthetic but stable",
            "budget rows do not retain a full update history",
            "profile state is a current snapshot and must not be joined to historical forecast labels without a point-in-time snapshot",
            "the 37M offline corpus has no real user identifier and is not part of these user histories",
        ],
    }
    (out_dir / "export_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {events_table.num_rows:,} events, {profiles_table.num_rows:,} "
        f"profiles and {feedback_table.num_rows:,} corrections -> {out_dir}"
    )
    if _DATABASE_ENGINE is not None:
        _DATABASE_ENGINE.dispose()
        _DATABASE_ENGINE = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
