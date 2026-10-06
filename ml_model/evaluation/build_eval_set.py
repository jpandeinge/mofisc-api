"""Build leakage-aware transaction evaluation datasets.

The source parquet is large, so this script scans it in batches and keeps only
bounded deterministic samples. It creates a silver benchmark and a separate
human-review seed set; the latter should be annotated before being treated as a
gold benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import pyarrow.parquet as pq

UINT64_MAX = float(2**64 - 1)
ML_MODEL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ML_MODEL_DIR
    / "data"
    / "processed"
    / "labelled_transactions_v2_regex_corrected.parquet"
)
DEFAULT_OUTPUT_DIR = ML_MODEL_DIR / "data" / "evaluation"

UNKNOWN_CATEGORIES = {
    "",
    "miscellaneous/unknown",
    "unknown",
    "uncategorised",
    "uncategorized",
    "processing...",
    "classification failed",
}

DESCRIPTION_COL = "TransactionParticulars"
CATEGORY_COL = "Category_corrected"
MERCHANT_COL = "merchant_name"
SUBCATEGORY_COL = "subcategory"

BASE_COLUMNS = [
    "Unnamed: 0",
    "TransactionDate",
    DESCRIPTION_COL,
    "TransactionParticularCode",
    "Channel",
    "ChannelCode",
    "TransactionAmount",
    "PaymentType",
    "TransactionTypeDebitCredit",
    "Category",
    "Category_original",
    CATEGORY_COL,
    MERCHANT_COL,
    SUBCATEGORY_COL,
    "matched_rule_id",
    "matched_rule_order",
    "matched_rule_name",
]


@dataclass(frozen=True)
class BuildConfig:
    input_path: str
    output_dir: str
    batch_size: int
    max_row_groups: int | None
    silver_rows: int
    review_rows: int
    per_merchant: int
    per_category: int
    per_review_stratum: int
    unseen_merchant_fraction: float
    min_merchant_count: int
    seed: int


class BoundedHashSampler:
    """Keep the lowest-score records per key without retaining full batches."""

    def __init__(self, per_key_cap: int, total_cap: int):
        self.per_key_cap = per_key_cap
        self.total_cap = total_cap
        self._buckets: dict[str, list[tuple[float, str, dict[str, Any]]]] = defaultdict(list)

    def add(self, key: str, score: float, row_id: str, record: dict[str, Any]) -> None:
        if not key:
            key = "__missing__"
        heap = self._buckets[key]
        entry = (-score, row_id, record)
        if len(heap) < self.per_key_cap:
            heapq.heappush(heap, entry)
            return
        if score < -heap[0][0]:
            heapq.heapreplace(heap, entry)

    def rows(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for heap in self._buckets.values():
            records.extend(record for _, _, record in heap)
        records.sort(key=lambda item: (item["sample_score"], item["row_id"]))
        if self.total_cap > 0:
            records = records[: self.total_cap]
        return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build silver and human-review evaluation sets for transaction models.",
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--batch-size", type=int, default=250_000)
    parser.add_argument(
        "--max-row-groups",
        type=int,
        default=None,
        help="Limit row groups for smoke tests. Omit for full scan.",
    )
    parser.add_argument("--silver-rows", type=int, default=12_000)
    parser.add_argument("--review-rows", type=int, default=3_000)
    parser.add_argument("--per-merchant", type=int, default=35)
    parser.add_argument("--per-category", type=int, default=60)
    parser.add_argument("--per-review-stratum", type=int, default=1_000)
    parser.add_argument("--unseen-merchant-fraction", type=float, default=0.20)
    parser.add_argument("--min-merchant-count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def stable_float(value: str, seed: int) -> float:
    payload = f"{seed}|{value}".encode("utf-8", errors="ignore")
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(2**64 - 1)


def stable_id(value: str, seed: int) -> str:
    payload = f"{seed}|{value}".encode("utf-8", errors="ignore")
    return hashlib.blake2b(payload, digest_size=12).hexdigest()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "null", "<na>"}:
        return ""
    return " ".join(text.split())


def clean_label(value: Any) -> str:
    return clean_text(value)


def normalise_for_compare(value: Any) -> str:
    return clean_label(value).lower()


def clean_series(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string").fillna("").str.strip()
    lowered = cleaned.str.lower()
    return cleaned.mask(lowered.isin({"nan", "none", "null", "<na>"}), "")


def is_known_category(value: Any) -> bool:
    return normalise_for_compare(value) not in UNKNOWN_CATEGORIES


def is_nonempty(value: Any) -> bool:
    return clean_text(value) != ""


def drcr_label(value: Any) -> str:
    text = normalise_for_compare(value)
    if text in {"d", "debit", "dr"}:
        return "debit"
    if text in {"c", "credit", "cr"}:
        return "credit"
    return text


def amount_bucket(value: Any) -> str:
    try:
        amount = abs(float(value))
    except (TypeError, ValueError):
        return ""
    if math.isnan(amount):
        return ""
    if amount == 0:
        return "0"
    if amount < 10:
        return "0_10"
    if amount < 50:
        return "10_50"
    if amount < 100:
        return "50_100"
    if amount < 500:
        return "100_500"
    if amount < 1_000:
        return "500_1000"
    if amount < 5_000:
        return "1000_5000"
    if amount < 20_000:
        return "5000_20000"
    return "20000_plus"


def date_parts(value: Any) -> tuple[str, str]:
    text = clean_text(value)
    if not text:
        return "", ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return "", ""
    return str(int(parsed.dayofweek)), str(int(parsed.month))


def model_input_text(record: dict[str, Any]) -> str:
    desc = clean_text(record.get(DESCRIPTION_COL)).lower()
    tx_type = drcr_label(record.get("TransactionTypeDebitCredit"))
    payment = normalise_for_compare(record.get("PaymentType"))
    channel = normalise_for_compare(record.get("ChannelCode") or record.get("Channel"))
    amount = amount_bucket(record.get("TransactionAmount"))
    day_of_week, month = date_parts(record.get("TransactionDate"))

    parts = []
    if desc:
        parts.append(f"transaction: {desc}")
    if tx_type:
        parts.append(f"type: {tx_type}")
    if payment:
        parts.append(f"payment: {payment}")
    if channel:
        parts.append(f"channel: {channel}")
    if amount:
        parts.append(f"amount_bucket: {amount}")
    if day_of_week:
        parts.append(f"day_of_week: {day_of_week}")
    if month:
        parts.append(f"month: {month}")
    return " | ".join(parts)


def available_columns(path: Path, requested: Iterable[str]) -> list[str]:
    schema_names = set(pq.ParquetFile(path).schema_arrow.names)
    return [col for col in requested if col in schema_names]


def iter_batches(path: Path, columns: list[str], batch_size: int, max_row_groups: int | None):
    parquet_file = pq.ParquetFile(path)
    row_group_count = parquet_file.metadata.num_row_groups
    if max_row_groups is None:
        row_groups = list(range(row_group_count))
    else:
        row_groups = list(range(min(max_row_groups, row_group_count)))

    yield from parquet_file.iter_batches(
        batch_size=batch_size,
        columns=columns,
        row_groups=row_groups,
    )


def count_labels(
    path: Path,
    columns: list[str],
    batch_size: int,
    max_row_groups: int | None,
) -> tuple[Counter, Counter, Counter, Counter, int]:
    category_counts: Counter = Counter()
    merchant_counts: Counter = Counter()
    subcategory_counts: Counter = Counter()
    stratum_counts: Counter = Counter()
    total_rows = 0

    for batch_idx, batch in enumerate(iter_batches(path, columns, batch_size, max_row_groups), start=1):
        df = batch.to_pandas()
        total_rows += len(df)

        category = clean_series(df.get(CATEGORY_COL, pd.Series([""] * len(df))))
        merchant = clean_series(df.get(MERCHANT_COL, pd.Series([""] * len(df))))
        subcategory = clean_series(df.get(SUBCATEGORY_COL, pd.Series([""] * len(df))))
        matched_rule = df.get("matched_rule_id", pd.Series([pd.NA] * len(df))).notna()

        known_category = ~category.str.lower().isin(UNKNOWN_CATEGORIES)
        has_merchant = merchant != ""
        has_subcategory = subcategory != ""

        category_counts.update(category[known_category].value_counts().to_dict())
        merchant_counts.update(merchant[has_merchant].value_counts().to_dict())
        subcategory_counts.update(subcategory[has_subcategory].value_counts().to_dict())

        stratum_counts["known_category"] += int(known_category.sum())
        stratum_counts["unknown_category"] += int((~known_category).sum())
        stratum_counts["has_merchant"] += int(has_merchant.sum())
        stratum_counts["missing_merchant"] += int((~has_merchant).sum())
        stratum_counts["has_subcategory"] += int(has_subcategory.sum())
        stratum_counts["regex_matched"] += int(matched_rule.sum())
        stratum_counts["regex_unmatched"] += int((~matched_rule).sum())

        if batch_idx == 1 or batch_idx % 20 == 0:
            print(f"  counted {total_rows:,} rows...", flush=True)

    return category_counts, merchant_counts, subcategory_counts, stratum_counts, total_rows


def choose_holdout_merchants(
    merchant_counts: Counter,
    min_count: int,
    fraction: float,
    seed: int,
) -> set[str]:
    eligible = [merchant for merchant, count in merchant_counts.items() if count >= min_count]
    if not eligible:
        return set()
    eligible.sort(key=lambda merchant: stable_float(f"merchant:{merchant}", seed))
    target = max(1, round(len(eligible) * fraction))
    return set(eligible[:target])


def record_hash_source(row: dict[str, Any]) -> str:
    fields = [
        row.get("Unnamed: 0"),
        row.get("TransactionDate"),
        row.get(DESCRIPTION_COL),
        row.get("TransactionAmount"),
        row.get("TransactionTypeDebitCredit"),
        row.get("PaymentType"),
        row.get(CATEGORY_COL),
        row.get(MERCHANT_COL),
        row.get("matched_rule_id"),
    ]
    return "|".join(clean_text(value) for value in fields)


def add_hash_columns(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    hash_cols = [
        col
        for col in [
            "Unnamed: 0",
            "TransactionDate",
            DESCRIPTION_COL,
            "TransactionAmount",
            "TransactionTypeDebitCredit",
            "PaymentType",
            CATEGORY_COL,
            MERCHANT_COL,
            "matched_rule_id",
        ]
        if col in df.columns
    ]
    hash_frame = df[hash_cols].astype("string").fillna("")
    hash_frame["__seed"] = str(seed)
    hash_values = pd.util.hash_pandas_object(hash_frame, index=False).astype("uint64")
    df = df.copy()
    df["_row_id"] = hash_values.map(lambda value: f"{int(value):016x}")
    df["_sample_score"] = hash_values.astype("float64") / UINT64_MAX
    return df


def prepare_batch(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    df = add_hash_columns(df, seed)
    df["_category_label"] = clean_series(df.get(CATEGORY_COL, pd.Series([""] * len(df))))
    df["_merchant_label"] = clean_series(df.get(MERCHANT_COL, pd.Series([""] * len(df))))
    df["_subcategory_label"] = clean_series(df.get(SUBCATEGORY_COL, pd.Series([""] * len(df))))
    df["_known_category"] = ~df["_category_label"].str.lower().isin(UNKNOWN_CATEGORIES)
    df["_has_merchant"] = df["_merchant_label"] != ""
    df["_has_model_input"] = clean_series(df.get(DESCRIPTION_COL, pd.Series([""] * len(df)))) != ""
    df["_matched_rule"] = df.get("matched_rule_id", pd.Series([pd.NA] * len(df))).notna()
    return df


def base_output_record(
    row: dict[str, Any],
    *,
    row_id: str,
    sample_score: float,
    split: str,
    label_quality: str,
    holdout_reason: str,
    needs_human_review: bool,
) -> dict[str, Any]:
    category = clean_label(row.get(CATEGORY_COL))
    merchant = clean_label(row.get(MERCHANT_COL))
    subcategory = clean_label(row.get(SUBCATEGORY_COL))

    return {
        "row_id": row_id,
        "split": split,
        "label_quality": label_quality,
        "holdout_reason": holdout_reason,
        "needs_human_review": needs_human_review,
        "sample_score": sample_score,
        "model_input_text": model_input_text(row),
        "transaction_date": clean_text(row.get("TransactionDate")),
        "transaction_particulars": clean_text(row.get(DESCRIPTION_COL)),
        "transaction_amount": row.get("TransactionAmount"),
        "transaction_type": drcr_label(row.get("TransactionTypeDebitCredit")),
        "payment_type": clean_text(row.get("PaymentType")),
        "channel": clean_text(row.get("Channel")),
        "channel_code": clean_text(row.get("ChannelCode")),
        "category_original": clean_label(row.get("Category_original") or row.get("Category")),
        "category_corrected": category,
        "merchant_name": merchant,
        "subcategory": subcategory,
        "matched_rule_id": row.get("matched_rule_id"),
        "matched_rule_order": row.get("matched_rule_order"),
        "matched_rule_name": clean_text(row.get("matched_rule_name")),
    }


def review_stratum(row: dict[str, Any]) -> str:
    category = row.get(CATEGORY_COL)
    merchant = row.get(MERCHANT_COL)
    matched = row.get("matched_rule_id")
    if not is_known_category(category):
        return "unknown_category"
    if not is_nonempty(merchant):
        return "missing_merchant"
    if pd.isna(matched):
        return "regex_unmatched"
    return "other_review"


def add_candidate_frame(
    frame: pd.DataFrame,
    *,
    sampler: BoundedHashSampler,
    key_col: str,
    split: str,
    label_quality: str,
    holdout_reason: str,
    needs_human_review: bool,
    per_batch_key_cap: int,
) -> None:
    if frame.empty:
        return
    keep = (
        frame.sort_values("_sample_score")
        .groupby(key_col, sort=False, dropna=False)
        .head(per_batch_key_cap)
    )
    for row in keep.to_dict("records"):
        row_id = row["_row_id"]
        score = float(row["_sample_score"])
        output = base_output_record(
            row,
            row_id=row_id,
            sample_score=score,
            split=split,
            label_quality=label_quality,
            holdout_reason=holdout_reason,
            needs_human_review=needs_human_review,
        )
        if needs_human_review:
            output.update(
                {
                    "suggested_category": output["category_corrected"],
                    "suggested_merchant_name": output["merchant_name"],
                    "suggested_subcategory": output["subcategory"],
                    "gold_category": "",
                    "gold_merchant_name": "",
                    "gold_subcategory": "",
                    "review_notes": "",
                }
            )
        sampler.add(str(row.get(key_col, "")), score, row_id, output)


def collect_samples(
    path: Path,
    columns: list[str],
    batch_size: int,
    max_row_groups: int | None,
    holdout_merchants: set[str],
    config: BuildConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merchant_seen = BoundedHashSampler(
        per_key_cap=config.per_merchant,
        total_cap=max(1, config.silver_rows // 3),
    )
    merchant_unseen = BoundedHashSampler(
        per_key_cap=config.per_merchant,
        total_cap=max(1, config.silver_rows // 3),
    )
    category_sampler = BoundedHashSampler(
        per_key_cap=config.per_category,
        total_cap=max(1, config.silver_rows - 2 * (config.silver_rows // 3)),
    )
    review_sampler = BoundedHashSampler(
        per_key_cap=config.per_review_stratum,
        total_cap=config.review_rows,
    )

    sampled_rows_seen = 0
    for batch_idx, batch in enumerate(iter_batches(path, columns, batch_size, max_row_groups), start=1):
        df = prepare_batch(batch.to_pandas(), config.seed)
        sampled_rows_seen += len(df)
        valid = df[df["_has_model_input"]]

        merchant_candidates = valid[
            valid["_known_category"] & valid["_has_merchant"] & valid["_matched_rule"]
        ]
        if not merchant_candidates.empty:
            unseen = merchant_candidates[
                merchant_candidates["_merchant_label"].isin(holdout_merchants)
            ]
            seen = merchant_candidates[
                ~merchant_candidates["_merchant_label"].isin(holdout_merchants)
            ]
            add_candidate_frame(
                unseen,
                sampler=merchant_unseen,
                key_col="_merchant_label",
                split="silver_merchant_unseen",
                label_quality="regex_merchant_category",
                holdout_reason="merchant_label_holdout",
                needs_human_review=False,
                per_batch_key_cap=max(3, config.per_merchant // 3),
            )
            add_candidate_frame(
                seen,
                sampler=merchant_seen,
                key_col="_merchant_label",
                split="silver_merchant_seen",
                label_quality="regex_merchant_category",
                holdout_reason="row_holdout",
                needs_human_review=False,
                per_batch_key_cap=max(3, config.per_merchant // 3),
            )

        category_candidates = valid[valid["_known_category"]]
        add_candidate_frame(
            category_candidates,
            sampler=category_sampler,
            key_col="_category_label",
            split="silver_category",
            label_quality="category_label",
            holdout_reason="row_holdout",
            needs_human_review=False,
            per_batch_key_cap=max(3, config.per_category // 3),
        )

        review_candidates = valid[
            (~valid["_known_category"]) | (~valid["_has_merchant"]) | (~valid["_matched_rule"])
        ].copy()
        if not review_candidates.empty:
            review_candidates["_review_stratum"] = "other_review"
            review_candidates.loc[
                ~review_candidates["_known_category"],
                "_review_stratum",
            ] = "unknown_category"
            review_candidates.loc[
                review_candidates["_known_category"] & ~review_candidates["_has_merchant"],
                "_review_stratum",
            ] = "missing_merchant"
            review_candidates.loc[
                review_candidates["_known_category"]
                & review_candidates["_has_merchant"]
                & ~review_candidates["_matched_rule"],
                "_review_stratum",
            ] = "regex_unmatched"
            review_candidates = review_candidates[
                review_candidates["_review_stratum"] != "other_review"
            ]
            add_candidate_frame(
                review_candidates,
                sampler=review_sampler,
                key_col="_review_stratum",
                split="review",
                label_quality="needs_annotation",
                holdout_reason="human_review",
                needs_human_review=True,
                per_batch_key_cap=max(20, config.per_review_stratum // 4),
            )

        if batch_idx == 1 or batch_idx % 20 == 0:
            print(f"  sampled through {sampled_rows_seen:,} rows...", flush=True)

    silver_rows = dedupe_rows(
        [
            *merchant_unseen.rows(),
            *merchant_seen.rows(),
            *category_sampler.rows(),
        ],
        total_cap=config.silver_rows,
    )
    review_rows = dedupe_rows(review_sampler.rows(), total_cap=config.review_rows)
    silver_row_ids = {row["row_id"] for row in silver_rows}
    review_rows = [row for row in review_rows if row["row_id"] not in silver_row_ids]
    review_rows = review_rows[: config.review_rows]
    for row in review_rows:
        if row["split"] == "review":
            if not is_known_category(row["category_corrected"]):
                row["split"] = "review_unknown_category"
            elif not is_nonempty(row["merchant_name"]):
                row["split"] = "review_missing_merchant"
            else:
                row["split"] = "review_regex_unmatched"
    return silver_rows, review_rows


def dedupe_rows(rows: list[dict[str, Any]], total_cap: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    priority = {
        "silver_merchant_unseen": 0,
        "silver_merchant_seen": 1,
        "silver_category": 2,
    }
    rows.sort(key=lambda item: (priority.get(item["split"], 9), item["sample_score"], item["row_id"]))
    for row in rows:
        if row["row_id"] in seen:
            continue
        seen.add(row["row_id"])
        deduped.append(row)
    deduped.sort(key=lambda item: (item["split"], item["sample_score"], item["row_id"]))
    if total_cap > 0:
        deduped = deduped[:total_cap]
    return deduped


def write_outputs(
    output_dir: Path,
    silver_rows: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
    *,
    config: BuildConfig,
    category_counts: Counter,
    merchant_counts: Counter,
    subcategory_counts: Counter,
    stratum_counts: Counter,
    total_rows_scanned: int,
    holdout_merchants: set[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    silver_df = pd.DataFrame(silver_rows)
    review_df = pd.DataFrame(review_rows)

    silver_parquet = output_dir / "silver_eval.parquet"
    silver_csv = output_dir / "silver_eval.csv"
    review_csv = output_dir / "human_review_seed.csv"
    manifest_path = output_dir / "holdout_manifest.json"
    counts_path = output_dir / "label_counts.json"

    silver_df.to_parquet(silver_parquet, index=False)
    silver_df.to_csv(silver_csv, index=False)
    review_df.to_csv(review_csv, index=False)

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": asdict(config),
        "total_rows_scanned": total_rows_scanned,
        "silver_rows": len(silver_rows),
        "review_rows": len(review_rows),
        "holdout_merchants": sorted(holdout_merchants),
        "training_exclusions": {
            "row_ids": sorted({row["row_id"] for row in [*silver_rows, *review_rows]}),
            "silver_row_ids": sorted(row["row_id"] for row in silver_rows),
            "review_row_ids": sorted(row["row_id"] for row in review_rows),
            "merchant_labels": sorted(holdout_merchants),
        },
        "split_counts": silver_df["split"].value_counts().to_dict() if len(silver_df) else {},
        "review_split_counts": review_df["split"].value_counts().to_dict() if len(review_df) else {},
        "notes": [
            "Exclude training_exclusions.row_ids from all training data.",
            "Exclude training_exclusions.merchant_labels from merchant-retrieval training when evaluating silver_merchant_unseen.",
            "human_review_seed.csv is not gold until manually annotated.",
            "model_input_text intentionally excludes merchant/category/subcategory labels.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    counts = {
        "total_rows_scanned": total_rows_scanned,
        "strata": dict(stratum_counts),
        "category_counts": dict(category_counts.most_common()),
        "merchant_counts": dict(merchant_counts.most_common()),
        "subcategory_counts": dict(subcategory_counts.most_common()),
    }
    counts_path.write_text(json.dumps(counts, indent=2), encoding="utf-8")

    print(f"Wrote {silver_parquet}")
    print(f"Wrote {silver_csv}")
    print(f"Wrote {review_csv}")
    print(f"Wrote {manifest_path}")
    print(f"Wrote {counts_path}")
    if len(silver_rows) < config.silver_rows:
        print(
            f"Silver rows below requested cap: {len(silver_rows):,} of "
            f"{config.silver_rows:,}. Increase per-key caps or label coverage "
            "if a larger benchmark is needed."
        )
    if len(review_rows) < config.review_rows:
        print(
            f"Review rows below requested cap: {len(review_rows):,} of "
            f"{config.review_rows:,}. This can happen when review strata have "
            "limited eligible rows after de-duplication."
        )


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input parquet does not exist: {input_path}")

    config = BuildConfig(
        input_path=str(input_path),
        output_dir=str(output_dir),
        batch_size=args.batch_size,
        max_row_groups=args.max_row_groups,
        silver_rows=args.silver_rows,
        review_rows=args.review_rows,
        per_merchant=args.per_merchant,
        per_category=args.per_category,
        per_review_stratum=args.per_review_stratum,
        unseen_merchant_fraction=args.unseen_merchant_fraction,
        min_merchant_count=args.min_merchant_count,
        seed=args.seed,
    )

    columns = available_columns(input_path, BASE_COLUMNS)
    required = {DESCRIPTION_COL, CATEGORY_COL, MERCHANT_COL, "matched_rule_id"}
    missing_required = sorted(required - set(columns))
    if missing_required:
        raise ValueError(f"Input parquet is missing required columns: {missing_required}")

    print(f"Scanning labels from {input_path}")
    category_counts, merchant_counts, subcategory_counts, stratum_counts, total_rows = count_labels(
        input_path,
        columns,
        args.batch_size,
        args.max_row_groups,
    )

    holdout_merchants = choose_holdout_merchants(
        merchant_counts,
        min_count=args.min_merchant_count,
        fraction=args.unseen_merchant_fraction,
        seed=args.seed,
    )
    print(f"Rows scanned: {total_rows:,}")
    print(f"Known categories: {len(category_counts):,}")
    print(f"Merchants: {len(merchant_counts):,}")
    print(f"Holdout merchants: {len(holdout_merchants):,}")

    silver_rows, review_rows = collect_samples(
        input_path,
        columns,
        args.batch_size,
        args.max_row_groups,
        holdout_merchants,
        config,
    )

    write_outputs(
        output_dir,
        silver_rows,
        review_rows,
        config=config,
        category_counts=category_counts,
        merchant_counts=merchant_counts,
        subcategory_counts=subcategory_counts,
        stratum_counts=stratum_counts,
        total_rows_scanned=total_rows,
        holdout_merchants=holdout_merchants,
    )


if __name__ == "__main__":
    main()
