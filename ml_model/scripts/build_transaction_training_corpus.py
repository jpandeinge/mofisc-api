"""Build leakage-safe transaction-model splits from the 37M-row silver corpus.

The source contains heavy duplication and only ~100 rule-labelled merchants.
This builder keeps one row per normalised template, predicts the app's detailed
category labels (for example Groceries rather than the PFC primary
FOOD_AND_DRINK), creates a latest-month time-forward test, and removes entire
merchants into a separate open-world test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

USE_COLUMNS = (
    "row_id",
    "TransactionDate",
    "TransactionParticulars",
    "TransactionAmount",
    "TransactionTypeDebitCredit",
    "PaymentType",
    "template",
    "pfc_primary",
    "pfc_detailed",
    "merchant_v3",
    "label_source",
    "label_confidence",
    "merchant_source",
)

OUTPUT_COLUMNS = (
    "row_id",
    "transaction_date",
    "template",
    "input_text",
    "primary_label",
    "app_category",
    "merchant_label",
    "label_source",
    "label_confidence",
    "merchant_source",
    "sample_weight",
    "split",
)

SOURCE_WEIGHTS = {
    "regex_subcategory": 1.0,
    "category_mapping": 0.75,
    "template_propagation": 0.60,
}

AMOUNT_BUCKETS = (
    (10.0, "lt_10"),
    (100.0, "10_100"),
    (1_000.0, "100_1k"),
    (10_000.0, "1k_10k"),
    (100_000.0, "10k_100k"),
    (float("inf"), "gt_100k"),
)


def stable_bucket(value: str, seed: int, modulo: int = 10_000) -> int:
    digest = hashlib.blake2b(
        value.encode("utf-8"),
        digest_size=8,
        person=f"split{seed:03d}".encode("ascii")[:8],
    ).digest()
    return int.from_bytes(digest, "big") % modulo


def _amount_bucket(amounts: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(amounts, errors="coerce").abs()
    output = pd.Series("unknown", index=amounts.index, dtype="object")
    lower = 0.0
    for upper, name in AMOUNT_BUCKETS:
        output[(numeric >= lower) & (numeric < upper)] = name
        lower = upper
    return output


def build_input_text(frame: pd.DataFrame) -> pd.Series:
    """Serialize exactly the fields available to the production API."""
    descriptions = (
        frame["TransactionParticulars"]
        .astype("string")
        .str.lower()
        .str.strip()
        .fillna("")
    )
    direction = (
        frame["TransactionTypeDebitCredit"]
        .astype("string")
        .str.lower()
        .map({"d": "debit", "c": "credit", "debit": "debit", "credit": "credit"})
        .fillna("unknown")
    )
    payment = (
        frame["PaymentType"].astype("string").str.lower().str.strip().fillna("unknown")
    )
    payment = payment.mask(payment == "", "unknown")
    amounts = _amount_bucket(frame["TransactionAmount"])
    return (
        "transaction: "
        + descriptions
        + " | type: "
        + direction
        + " | payment: "
        + payment
        + " | amount_bucket: "
        + amounts
    )


def load_unique_high_confidence(
    source: Path,
    min_confidence: float,
    max_batches: int | None,
) -> tuple[pd.DataFrame, int]:
    parquet = pq.ParquetFile(source)
    missing = set(USE_COLUMNS) - set(parquet.schema_arrow.names)
    if missing:
        raise ValueError(f"source parquet is missing columns: {sorted(missing)}")

    frames: list[pd.DataFrame] = []
    seen_templates: set[str] = set()
    scanned = 0
    for batch_number, batch in enumerate(
        parquet.iter_batches(columns=USE_COLUMNS, batch_size=524_288)
    ):
        if max_batches is not None and batch_number >= max_batches:
            break
        part = batch.to_pandas()
        scanned += len(part)
        part = part[
            (part["pfc_primary"] != "UNCATEGORIZED")
            & (part["label_confidence"] >= min_confidence)
            & part["template"].notna()
            & part["TransactionParticulars"].notna()
        ].copy()
        part = part.drop_duplicates("template", keep="first")
        if seen_templates:
            part = part[~part["template"].isin(seen_templates)]
        seen_templates.update(part["template"].astype(str))
        frames.append(part)
    if not frames:
        raise ValueError("no labelled templates passed the filters")
    return pd.concat(frames, ignore_index=True), scanned


def app_category_labels(
    frame: pd.DataFrame, taxonomy: dict
) -> pd.Series:
    detailed_map = taxonomy["app_display"]["detailed"]
    primary_map = taxonomy["app_display"]["primary"]
    detailed = frame["pfc_detailed"].map(detailed_map)
    primary = frame["pfc_primary"].map(primary_map)
    result = detailed.fillna(primary)
    if result.isna().any():
        unknown = sorted(frame.loc[result.isna(), "pfc_primary"].unique())
        raise ValueError(f"taxonomy lacks app mappings for primary labels: {unknown}")
    return result


def assign_splits(
    frame: pd.DataFrame,
    seed: int,
    holdout_merchants: int,
    merchant_min_templates: int,
    merchant_max_templates: int,
) -> tuple[pd.DataFrame, str, list[str]]:
    frame = frame.copy()
    parsed_dates = pd.to_datetime(frame["TransactionDate"], errors="coerce")
    if parsed_dates.isna().any():
        raise ValueError("training rows contain invalid TransactionDate values")
    newest = parsed_dates.max()
    time_cutoff = newest.replace(day=1).normalize()
    has_prior_month = bool((parsed_dates < time_cutoff).any())

    eligible_counts = (
        frame.loc[frame["merchant_v3"].notna(), "merchant_v3"].value_counts()
    )
    eligible = eligible_counts[
        (eligible_counts >= merchant_min_templates)
        & (eligible_counts <= merchant_max_templates)
    ].index.tolist()
    eligible.sort(key=lambda merchant: stable_bucket(str(merchant), seed))
    held_out = set(eligible[:holdout_merchants])

    split = np.full(len(frame), "train", dtype=object)
    merchant_mask = frame["merchant_v3"].isin(held_out).to_numpy()
    hash_buckets = (
        frame["template"]
        .astype(str)
        .map(lambda value: stable_bucket(value, seed))
        .to_numpy()
    )
    if has_prior_month:
        time_mask = (parsed_dates >= time_cutoff).to_numpy() & ~merchant_mask
        validation_mask = (
            (hash_buckets < 1_000) & ~merchant_mask & ~time_mask
        )
        cutoff_description = time_cutoff.date().isoformat()
    else:
        # Bounded smoke scans may contain only one month. Keep all four splits
        # non-empty without pretending this hash split is temporal.
        time_mask = (
            (hash_buckets >= 1_000)
            & (hash_buckets < 2_000)
            & ~merchant_mask
        )
        validation_mask = (hash_buckets < 1_000) & ~merchant_mask
        cutoff_description = "hash_fallback_no_prior_month"
    split[validation_mask] = "validation"
    split[time_mask] = "time_forward_test"
    split[merchant_mask] = "merchant_holdout_test"
    frame["split"] = split
    return frame, cutoff_description, sorted(held_out)


def _write_split(frame: pd.DataFrame, out: Path, split_name: str) -> int:
    selected = frame[frame["split"] == split_name].loc[:, OUTPUT_COLUMNS]
    table = pa.Table.from_pandas(selected, preserve_index=False)
    pq.write_table(table, out / f"{split_name}.parquet", compression="zstd")
    return len(selected)


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
        "--taxonomy",
        type=Path,
        default=ml_root / "data" / "processed" / "labels_v4" / "taxonomy_v4.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ml_root / "data" / "processed" / "transaction_v5",
    )
    parser.add_argument("--min-confidence", type=float, default=0.80)
    parser.add_argument("--holdout-merchants", type=int, default=15)
    parser.add_argument("--merchant-min-templates", type=int, default=200)
    parser.add_argument(
        "--merchant-max-templates",
        type=int,
        default=5_000,
        help="exclude broad bank/channel-like labels from the open-world holdout",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--max-batches",
        type=int,
        help="bounded smoke mode; omit to scan the complete parquet",
    )
    args = parser.parse_args()

    taxonomy = json.loads(args.taxonomy.read_text())
    frame, scanned = load_unique_high_confidence(
        args.source, args.min_confidence, args.max_batches
    )
    frame["input_text"] = build_input_text(frame)
    frame["primary_label"] = frame["pfc_primary"].astype(str)
    frame["app_category"] = app_category_labels(frame, taxonomy)
    frame["merchant_label"] = frame["merchant_v3"].astype("string")
    frame["transaction_date"] = pd.to_datetime(
        frame["TransactionDate"]
    ).dt.date.astype(str)
    source_weight = frame["label_source"].map(SOURCE_WEIGHTS).fillna(0.5)
    frame["sample_weight"] = (
        source_weight * frame["label_confidence"].astype(float)
    ).clip(0.1, 1.0)
    frame, time_cutoff, held_out = assign_splits(
        frame,
        seed=args.seed,
        holdout_merchants=args.holdout_merchants,
        merchant_min_templates=args.merchant_min_templates,
        merchant_max_templates=args.merchant_max_templates,
    )

    args.out.mkdir(parents=True, exist_ok=True)
    split_counts = {
        split_name: _write_split(frame, args.out, split_name)
        for split_name in (
            "train",
            "validation",
            "time_forward_test",
            "merchant_holdout_test",
        )
    }
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": str(args.source),
        "taxonomy": str(args.taxonomy),
        "rows_scanned": scanned,
        "unique_high_confidence_templates": len(frame),
        "min_confidence": args.min_confidence,
        "holdout_merchant_count_requested": args.holdout_merchants,
        "merchant_holdout_min_templates": args.merchant_min_templates,
        "merchant_holdout_max_templates": args.merchant_max_templates,
        "split_counts": split_counts,
        "time_forward_cutoff": time_cutoff,
        "held_out_merchants": held_out,
        "seed": args.seed,
        "input_fields": [
            "TransactionParticulars",
            "TransactionTypeDebitCredit",
            "PaymentType",
            "TransactionAmount bucket",
        ],
        "target_fields_excluded_from_input": [
            "pfc_primary",
            "pfc_detailed",
            "merchant_v3",
            "Category_corrected",
        ],
        "app_category_distribution": {
            str(key): int(value)
            for key, value in frame["app_category"].value_counts().items()
        },
        "merchant_count": int(frame["merchant_label"].nunique()),
        "label_source_distribution": {
            str(key): int(value)
            for key, value in frame["label_source"].value_counts().items()
        },
        "promotion_status": "candidate_dataset_requires_model_and_human_evaluation",
    }
    (args.out / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {len(frame):,} distinct templates to {args.out}; "
        f"splits={split_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
