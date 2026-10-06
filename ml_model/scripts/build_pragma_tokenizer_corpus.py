"""Build a tokenizer-only Pragmatiq corpus from the 37M transaction parquet.

The historical file has no customer/account identifier, so it cannot provide
real user histories. This script samples distinct transaction templates only to
fit text vocabulary, categorical vocabularies and numeric bins. Every offline
row receives a unique artificial ID, and the manifest explicitly prohibits
using the output shards for history-model pretraining.

Optionally append a real, pseudonymised Galahad raw export so profile and app
event keys enter the same tokenizer vocabulary.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

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

SOURCE_COLUMNS = (
    "TransactionDate",
    "TransactionParticulars",
    "TransactionAmount",
    "TransactionTypeDebitCredit",
    "PaymentType",
    "Channel",
    "template",
)


@dataclass(frozen=True)
class Candidate:
    score: int
    template: str
    ts: Any
    fields: dict[str, str]


def stable_score(template: str, seed: int) -> int:
    """Stable 64-bit min-hash score for deterministic sampling."""
    digest = hashlib.blake2b(
        template.encode("utf-8"),
        digest_size=8,
        person=f"gala{seed:04d}".encode("ascii")[:8],
    ).digest()
    return int.from_bytes(digest, "big")


def event_timestamp(value: Any) -> datetime:
    """Normalize the offline day-level date to a UTC datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text_value = str(value or "").strip()
    if not text_value:
        raise ValueError("transaction row has no TransactionDate")
    parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def transaction_fields(row: dict[str, Any]) -> dict[str, str]:
    fields = {
        "event_type": "transaction",
        "description": str(row.get("TransactionParticulars") or "").strip().lower(),
        "amount": str(abs(float(row.get("TransactionAmount") or 0.0))),
        "direction": str(
            row.get("TransactionTypeDebitCredit") or "unknown"
        ).strip().lower(),
        "payment_type": str(row.get("PaymentType") or "unknown").strip().lower(),
        "channel": str(row.get("Channel") or "unknown").strip().lower(),
    }
    return {key: value for key, value in fields.items() if value}


def select_distinct_templates(
    source: Path,
    max_events: int,
    seed: int,
    batch_size: int,
    max_batches: int | None = None,
) -> tuple[list[Candidate], int, int]:
    """Return a deterministic min-hash sample of distinct templates."""
    parquet = pq.ParquetFile(source)
    missing = set(SOURCE_COLUMNS) - set(parquet.schema_arrow.names)
    if missing:
        raise ValueError(f"source parquet is missing columns: {sorted(missing)}")

    # max-heap encoded with negative scores: the largest selected min-hash is
    # replaced whenever a smaller score appears.
    heap: list[tuple[int, str, Candidate]] = []
    seen_templates: set[str] = set()
    scanned = 0
    for batch_number, batch in enumerate(
        parquet.iter_batches(columns=SOURCE_COLUMNS, batch_size=batch_size)
    ):
        if max_batches is not None and batch_number >= max_batches:
            break
        for row in batch.to_pylist():
            scanned += 1
            template = str(
                row.get("template") or row.get("TransactionParticulars") or ""
            ).strip()
            if not template or template in seen_templates:
                continue
            seen_templates.add(template)
            score = stable_score(template, seed)
            candidate = Candidate(
                score=score,
                template=template,
                ts=event_timestamp(row.get("TransactionDate")),
                fields=transaction_fields(row),
            )
            item = (-score, template, candidate)
            if len(heap) < max_events:
                heapq.heappush(heap, item)
            elif score < -heap[0][0]:
                heapq.heapreplace(heap, item)

    selected = sorted(
        (item[2] for item in heap), key=lambda candidate: candidate.score
    )
    return selected, scanned, len(seen_templates)


def _empty_profiles() -> pa.Table:
    return pa.table(
        {
            "user_id": pa.array([], pa.string()),
            "as_of": pa.array([], pa.timestamp("us")),
            "attributes": pa.array([], pa.map_(pa.string(), pa.string())),
            "lifelong": pa.array(
                [],
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


def _candidate_events(candidates: list[Candidate]) -> pa.Table:
    return pa.table(
        {
            "user_id": pa.array(
                [f"tokenizer_only_{candidate.score:016x}" for candidate in candidates],
                pa.string(),
            ),
            "ts": pa.array([candidate.ts for candidate in candidates], pa.timestamp("us")),
            "source": pa.array(["transaction"] * len(candidates), pa.string()),
            "fields": pa.array(
                [list(candidate.fields.items()) for candidate in candidates],
                pa.map_(pa.string(), pa.string()),
            ),
        },
        schema=EVENTS_SCHEMA,
    )


def _load_live_tables(raw_dir: Path | None) -> tuple[pa.Table, pa.Table]:
    if raw_dir is None:
        return pa.Table.from_pylist([], schema=EVENTS_SCHEMA), _empty_profiles()
    events_path = raw_dir / "events.parquet"
    profiles_path = raw_dir / "profiles.parquet"
    if not events_path.is_file() or not profiles_path.is_file():
        raise FileNotFoundError(
            f"--include-live-raw requires events.parquet and profiles.parquet in {raw_dir}"
        )
    events = pq.read_table(events_path).cast(EVENTS_SCHEMA)
    profiles = pq.read_table(profiles_path).cast(PROFILES_SCHEMA)
    return events, profiles


def _sort_events(table: pa.Table) -> pa.Table:
    if table.num_rows == 0:
        return table
    indices = pc.sort_indices(
        table, sort_keys=[("user_id", "ascending"), ("ts", "ascending")]
    )
    return table.take(indices)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ml_root = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--source",
        type=Path,
        default=(
            ml_root
            / "data"
            / "processed"
            / "labels_v4"
            / "transactions_labelled_v4.parquet"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ml_root / "data" / "pragma" / "tokenizer_corpus",
    )
    parser.add_argument("--max-events", type=int, default=500_000)
    parser.add_argument("--batch-size", type=int, default=262_144)
    parser.add_argument(
        "--max-batches",
        type=int,
        help="bounded smoke mode; omit to scan the complete parquet",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--include-live-raw",
        type=Path,
        help="pseudonymised multi-table Galahad raw export to add to tokenizer fitting",
    )
    args = parser.parse_args()
    if args.max_events < 1:
        parser.error("--max-events must be positive")

    candidates, scanned, distinct = select_distinct_templates(
        args.source,
        args.max_events,
        args.seed,
        args.batch_size,
        args.max_batches,
    )
    offline_events = _candidate_events(candidates)
    live_events, live_profiles = _load_live_tables(args.include_live_raw)
    events = _sort_events(pa.concat_tables([offline_events, live_events]))

    args.out.mkdir(parents=True, exist_ok=True)
    pq.write_table(events, args.out / "events.parquet")
    pq.write_table(live_profiles, args.out / "profiles.parquet")
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": "tokenizer_fit_only",
        "pretraining_allowed": False,
        "reason": (
            "offline rows have no real customer/account identifier; their unique "
            "artificial IDs must never be interpreted as user histories"
        ),
        "source": str(args.source),
        "source_rows_scanned": scanned,
        "distinct_templates_seen": distinct,
        "offline_templates_selected": offline_events.num_rows,
        "live_events_appended": live_events.num_rows,
        "live_profiles_appended": live_profiles.num_rows,
        "seed": args.seed,
        "max_batches": args.max_batches,
        "selection": "smallest stable blake2b template hashes",
        "excluded_labels": [
            "pfc_primary",
            "pfc_detailed",
            "merchant_v3",
            "label_source",
            "label_confidence",
        ],
    }
    (args.out / "corpus_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote tokenizer-only corpus: {events.num_rows:,} events, "
        f"{live_profiles.num_rows:,} profiles -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
