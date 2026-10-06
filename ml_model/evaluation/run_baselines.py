"""Run repeatable baselines against the transaction silver evaluation set."""

from __future__ import annotations

import argparse
import heapq
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
)

if __package__:
    from .build_eval_set import (
        BASE_COLUMNS,
        CATEGORY_COL,
        DEFAULT_INPUT,
        DEFAULT_OUTPUT_DIR,
        DESCRIPTION_COL,
        MERCHANT_COL,
        SUBCATEGORY_COL,
        UNKNOWN_CATEGORIES,
        add_hash_columns,
        amount_bucket,
        available_columns,
        clean_label,
        clean_series,
        clean_text,
        drcr_label,
        iter_batches,
        normalise_for_compare,
    )
else:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from evaluation.build_eval_set import (  # type: ignore
        BASE_COLUMNS,
        CATEGORY_COL,
        DEFAULT_INPUT,
        DEFAULT_OUTPUT_DIR,
        DESCRIPTION_COL,
        MERCHANT_COL,
        SUBCATEGORY_COL,
        UNKNOWN_CATEGORIES,
        add_hash_columns,
        amount_bucket,
        available_columns,
        clean_label,
        clean_series,
        clean_text,
        drcr_label,
        iter_batches,
        normalise_for_compare,
    )


DEFAULT_EVAL = DEFAULT_OUTPUT_DIR / "silver_eval.parquet"
DEFAULT_MANIFEST = DEFAULT_OUTPUT_DIR / "holdout_manifest.json"
DEFAULT_COUNTS = DEFAULT_OUTPUT_DIR / "label_counts.json"
DEFAULT_BASELINE_DIR = DEFAULT_OUTPUT_DIR / "baselines"

MISSING = ""


@dataclass(frozen=True)
class BaselineConfig:
    eval_path: str
    source_path: str
    manifest_path: str
    counts_path: str
    output_dir: str
    batch_size: int
    max_row_groups: int | None
    embedding_model: str | None
    embedding_candidate_rows: int
    embedding_per_merchant: int
    embedding_batch_size: int
    seed: int


class CandidateSampler:
    """Keep a bounded, deterministic candidate corpus by merchant label."""

    def __init__(self, total_cap: int, per_merchant_cap: int):
        self.total_cap = total_cap
        self.per_merchant_cap = per_merchant_cap
        self._buckets: dict[str, list[tuple[float, str, dict[str, Any]]]] = defaultdict(list)

    def add(self, merchant: str, score: float, row_id: str, record: dict[str, Any]) -> None:
        if not merchant:
            return
        heap = self._buckets[merchant]
        entry = (-score, row_id, record)
        if len(heap) < self.per_merchant_cap:
            heapq.heappush(heap, entry)
            return
        if score < -heap[0][0]:
            heapq.heapreplace(heap, entry)

    def rows(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for heap in self._buckets.values():
            records.extend(record for _, _, record in heap)
        records.sort(key=lambda row: (row["sample_score"], row["row_id"]))
        return records[: self.total_cap]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score transaction model baselines against silver_eval.parquet.",
    )
    parser.add_argument("--eval-path", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--source-path", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--counts-path", type=Path, default=DEFAULT_COUNTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_BASELINE_DIR)
    parser.add_argument("--batch-size", type=int, default=250_000)
    parser.add_argument("--max-row-groups", type=int, default=None)
    parser.add_argument(
        "--embedding-model",
        type=Path,
        default=None,
        help="Optional local transformer/SentenceTransformer checkpoint directory.",
    )
    parser.add_argument("--embedding-candidate-rows", type=int, default=8_000)
    parser.add_argument("--embedding-per-merchant", type=int, default=120)
    parser.add_argument("--embedding-batch-size", type=int, default=128)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def canonical_label(value: Any) -> str:
    return " ".join(clean_label(value).lower().split())


def is_known_category_label(value: Any) -> bool:
    return canonical_label(value) not in UNKNOWN_CATEGORIES


def eval_lookup_key(row: pd.Series) -> str:
    desc = clean_text(row.get("transaction_particulars")).lower()
    tx_type = drcr_label(row.get("transaction_type"))
    payment = normalise_for_compare(row.get("payment_type"))
    channel = normalise_for_compare(row.get("channel_code") or row.get("channel"))
    return "|".join([desc, tx_type, payment, channel])


def source_lookup_keys(df: pd.DataFrame) -> pd.Series:
    desc = clean_series(df.get(DESCRIPTION_COL, pd.Series([""] * len(df)))).str.lower()
    tx_type = clean_series(
        df.get("TransactionTypeDebitCredit", pd.Series([""] * len(df)))
    ).str.lower()
    tx_type = tx_type.replace({"d": "debit", "dr": "debit", "c": "credit", "cr": "credit"})
    payment = clean_series(df.get("PaymentType", pd.Series([""] * len(df)))).str.lower()
    channel_code = clean_series(df.get("ChannelCode", pd.Series([""] * len(df)))).str.lower()
    channel = clean_series(df.get("Channel", pd.Series([""] * len(df)))).str.lower()
    channel = channel_code.mask(channel_code == "", channel)
    return desc + "|" + tx_type + "|" + payment + "|" + channel


def source_embedding_texts(df: pd.DataFrame) -> pd.Series:
    desc = clean_series(df.get(DESCRIPTION_COL, pd.Series([""] * len(df)))).str.lower()
    tx_type = clean_series(
        df.get("TransactionTypeDebitCredit", pd.Series([""] * len(df)))
    ).str.lower()
    tx_type = tx_type.replace({"d": "debit", "dr": "debit", "c": "credit", "cr": "credit"})
    payment = clean_series(df.get("PaymentType", pd.Series([""] * len(df)))).str.lower()
    channel_code = clean_series(df.get("ChannelCode", pd.Series([""] * len(df)))).str.lower()
    channel = clean_series(df.get("Channel", pd.Series([""] * len(df)))).str.lower()
    channel = channel_code.mask(channel_code == "", channel)
    amount = df.get("TransactionAmount", pd.Series([""] * len(df))).map(amount_bucket)
    date_text = clean_series(df.get("TransactionDate", pd.Series([""] * len(df))))
    parsed_dates = pd.to_datetime(date_text, errors="coerce")
    day = parsed_dates.dt.dayofweek.astype("Int64").astype("string").fillna("")
    month = parsed_dates.dt.month.astype("Int64").astype("string").fillna("")

    parts = "transaction: " + desc
    parts += " | type: " + tx_type.mask(tx_type == "", "")
    parts += " | payment: " + payment.mask(payment == "", "")
    parts += " | channel: " + channel.mask(channel == "", "")
    parts += " | amount_bucket: " + amount.mask(amount == "", "")
    parts += " | day_of_week: " + day.mask(day == "<NA>", "")
    parts += " | month: " + month.mask(month == "<NA>", "")
    return parts.str.replace(r"\s+\|", " |", regex=True).str.strip(" |")


def top_counter_value(counter: Counter, *, exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    for label, _ in counter.most_common():
        if clean_label(label) and label not in exclude:
            return label
    return MISSING


def load_eval(eval_path: Path) -> pd.DataFrame:
    df = pd.read_parquet(eval_path)
    required = {"row_id", "category_corrected", "merchant_name", "subcategory"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Evaluation set missing required columns: {missing}")
    df = df.copy()
    df["_lookup_key"] = df.apply(eval_lookup_key, axis=1)
    return df


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Manifest does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_majority_labels(counts_path: Path, holdout_merchants: set[str]) -> dict[str, str]:
    counts = json.loads(counts_path.read_text(encoding="utf-8"))
    category_counts = Counter(counts.get("category_counts", {}))
    merchant_counts = Counter(counts.get("merchant_counts", {}))
    subcategory_counts = Counter(counts.get("subcategory_counts", {}))
    return {
        "category": top_counter_value(
            Counter(
                {
                    key: value
                    for key, value in category_counts.items()
                    if is_known_category_label(key)
                }
            )
        ),
        "merchant": top_counter_value(merchant_counts, exclude=holdout_merchants),
        "subcategory": top_counter_value(subcategory_counts),
    }


def update_lookup_counters(
    counters: dict[str, dict[str, Counter]],
    row: dict[str, Any],
    *,
    exclude_merchant_labels: set[str],
) -> None:
    key = row["_lookup_key"]
    category = clean_label(row.get(CATEGORY_COL))
    merchant = clean_label(row.get(MERCHANT_COL))
    subcategory = clean_label(row.get(SUBCATEGORY_COL))

    if is_known_category_label(category):
        counters[key]["category"][category] += 1
    if merchant and merchant not in exclude_merchant_labels:
        counters[key]["merchant"][merchant] += 1
    if subcategory:
        counters[key]["subcategory"][subcategory] += 1


def add_embedding_candidates(
    sampler: CandidateSampler,
    frame: pd.DataFrame,
    *,
    excluded_row_ids: set[str],
    holdout_merchants: set[str],
) -> None:
    if frame.empty:
        return
    frame = frame[
        ~frame["_row_id"].isin(excluded_row_ids)
        & frame["_known_category"]
        & frame["_has_merchant"]
        & ~frame["_merchant_label"].isin(holdout_merchants)
        & (frame["_embedding_text"] != "")
    ]
    if frame.empty:
        return
    keep = (
        frame.sort_values("_sample_score")
        .groupby("_merchant_label", sort=False, dropna=False)
        .head(25)
    )
    for row in keep.to_dict("records"):
        record = {
            "row_id": row["_row_id"],
            "sample_score": float(row["_sample_score"]),
            "text": row["_embedding_text"],
            "category": clean_label(row.get(CATEGORY_COL)),
            "merchant": clean_label(row.get(MERCHANT_COL)),
            "subcategory": clean_label(row.get(SUBCATEGORY_COL)),
        }
        sampler.add(record["merchant"], record["sample_score"], record["row_id"], record)


def build_training_lookup_and_candidates(
    source_path: Path,
    columns: list[str],
    eval_keys: set[str],
    excluded_row_ids: set[str],
    holdout_merchants: set[str],
    *,
    batch_size: int,
    max_row_groups: int | None,
    seed: int,
    collect_embedding: bool,
    embedding_candidate_rows: int,
    embedding_per_merchant: int,
) -> tuple[dict[str, dict[str, Counter]], list[dict[str, Any]]]:
    counters: dict[str, dict[str, Counter]] = {
        key: {"category": Counter(), "merchant": Counter(), "subcategory": Counter()}
        for key in eval_keys
    }
    sampler = CandidateSampler(embedding_candidate_rows, embedding_per_merchant)
    rows_seen = 0

    for batch_idx, batch in enumerate(
        iter_batches(source_path, columns, batch_size, max_row_groups),
        start=1,
    ):
        df = batch.to_pandas()
        rows_seen += len(df)
        lookup_keys = source_lookup_keys(df)
        matched = df.loc[lookup_keys.isin(eval_keys)].copy()
        if not matched.empty:
            matched["_lookup_key"] = lookup_keys.loc[matched.index]
            matched = add_hash_columns(matched, seed)
            matched = matched[~matched["_row_id"].isin(excluded_row_ids)]
            for row in matched.to_dict("records"):
                update_lookup_counters(
                    counters,
                    row,
                    exclude_merchant_labels=holdout_merchants,
                )

        if collect_embedding:
            candidate = add_hash_columns(df, seed)
            category = clean_series(candidate.get(CATEGORY_COL, pd.Series([""] * len(candidate))))
            merchant = clean_series(candidate.get(MERCHANT_COL, pd.Series([""] * len(candidate))))
            candidate["_category_label"] = category
            candidate["_merchant_label"] = merchant
            candidate["_known_category"] = ~category.str.lower().isin(UNKNOWN_CATEGORIES)
            candidate["_has_merchant"] = merchant != ""
            candidate = candidate[
                ~candidate["_row_id"].isin(excluded_row_ids)
                & candidate["_known_category"]
                & candidate["_has_merchant"]
                & ~candidate["_merchant_label"].isin(holdout_merchants)
            ].copy()
            candidate["_embedding_text"] = (
                source_embedding_texts(candidate) if not candidate.empty else ""
            )
            add_embedding_candidates(
                sampler,
                candidate,
                excluded_row_ids=excluded_row_ids,
                holdout_merchants=holdout_merchants,
            )

        if batch_idx == 1 or batch_idx % 20 == 0:
            print(f"  baseline scan reached {rows_seen:,} rows...", flush=True)

    return counters, sampler.rows()


def prediction_from_counters(counters: dict[str, Counter], label: str) -> str:
    return top_counter_value(counters.get(label, Counter()))


def exact_lookup_predictions(df: pd.DataFrame, counters: dict[str, dict[str, Counter]]) -> pd.DataFrame:
    predictions = []
    for _, row in df.iterrows():
        key_counters = counters.get(row["_lookup_key"], {})
        predictions.append(
            {
                "row_id": row["row_id"],
                "baseline": "exact_lookup",
                "pred_category": prediction_from_counters(key_counters, "category"),
                "pred_merchant_name": prediction_from_counters(key_counters, "merchant"),
                "pred_subcategory": prediction_from_counters(key_counters, "subcategory"),
                "pred_merchant_top5": json.dumps(
                    [label for label, _ in key_counters.get("merchant", Counter()).most_common(5)]
                ),
            }
        )
    return pd.DataFrame(predictions)


def majority_predictions(df: pd.DataFrame, labels: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "row_id": df["row_id"],
            "baseline": "majority_train_label",
            "pred_category": labels["category"],
            "pred_merchant_name": labels["merchant"],
            "pred_subcategory": labels["subcategory"],
            "pred_merchant_top5": json.dumps([labels["merchant"]]),
        }
    )


def regex_matched_predictions(df: pd.DataFrame) -> pd.DataFrame:
    matched = df["matched_rule_id"].notna()
    return pd.DataFrame(
        {
            "row_id": df["row_id"],
            "baseline": "regex_matched_silver",
            "pred_category": df["category_corrected"].where(matched, MISSING),
            "pred_merchant_name": df["merchant_name"].where(matched, MISSING),
            "pred_subcategory": df["subcategory"].where(matched, MISSING),
            "pred_merchant_top5": [
                json.dumps([merchant]) if matched_value and clean_label(merchant) else json.dumps([])
                for matched_value, merchant in zip(matched.tolist(), df["merchant_name"].tolist())
            ],
        }
    )


def identity_predictions(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "row_id": df["row_id"],
            "baseline": "silver_label_identity",
            "pred_category": df["category_corrected"],
            "pred_merchant_name": df["merchant_name"],
            "pred_subcategory": df["subcategory"],
            "pred_merchant_top5": [
                json.dumps([merchant]) if clean_label(merchant) else json.dumps([])
                for merchant in df["merchant_name"].tolist()
            ],
        }
    )


def normalize_array(values: pd.Series) -> list[str]:
    return [canonical_label(value) for value in values.tolist()]


def label_metrics(
    y_true: pd.Series,
    y_pred: pd.Series,
    *,
    applicable: pd.Series | None = None,
) -> dict[str, Any]:
    if applicable is None:
        applicable = y_true.map(lambda value: clean_label(value) != "")
    applicable = applicable.fillna(False)
    total = int(len(y_true))
    evaluated = int(applicable.sum())
    if evaluated == 0:
        return {
            "total_rows": total,
            "evaluated_rows": 0,
            "coverage": 0.0,
            "accuracy": None,
            "macro_f1": None,
            "weighted_f1": None,
            "macro_precision": None,
            "macro_recall": None,
        }

    true = normalize_array(y_true[applicable])
    pred_raw = y_pred[applicable]
    predicted = pred_raw.map(lambda value: clean_label(value) != "")
    pred = [canonical_label(value) or "__missing_prediction__" for value in pred_raw.tolist()]
    return {
        "total_rows": total,
        "evaluated_rows": evaluated,
        "coverage": float(predicted.mean()),
        "accuracy": float(accuracy_score(true, pred)),
        "macro_f1": float(f1_score(true, pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(true, pred, average="weighted", zero_division=0)),
        "macro_precision": float(
            precision_score(true, pred, average="macro", zero_division=0)
        ),
        "macro_recall": float(recall_score(true, pred, average="macro", zero_division=0)),
    }


def merchant_topk_metrics(df: pd.DataFrame) -> dict[str, Any]:
    applicable = df["merchant_name"].map(lambda value: clean_label(value) != "")
    total = int(applicable.sum())
    if total == 0:
        return {
            "evaluated_rows": 0,
            "top1_accuracy": None,
            "top5_accuracy": None,
            "prediction_coverage": 0.0,
            "false_link_rate": None,
        }
    view = df[applicable]
    gold = [canonical_label(value) for value in view["merchant_name"]]
    pred = [canonical_label(value) for value in view["pred_merchant_name"]]
    top1_hits = [g == p for g, p in zip(gold, pred)]
    top5_hits = []
    predicted_nonempty = []
    for g, p, top5_text in zip(gold, pred, view["pred_merchant_top5"]):
        predicted_nonempty.append(p != "")
        try:
            top5 = [canonical_label(value) for value in json.loads(top5_text or "[]")]
        except json.JSONDecodeError:
            top5 = [p] if p else []
        top5_hits.append(g in top5)
    prediction_count = sum(predicted_nonempty)
    wrong_link_count = sum(
        1 for hit, has_pred in zip(top1_hits, predicted_nonempty) if has_pred and not hit
    )
    return {
        "evaluated_rows": total,
        "top1_accuracy": float(np.mean(top1_hits)),
        "top5_accuracy": float(np.mean(top5_hits)),
        "prediction_coverage": float(np.mean(predicted_nonempty)),
        "false_link_rate": (
            float(wrong_link_count / prediction_count) if prediction_count else None
        ),
    }


def score_predictions(eval_df: pd.DataFrame, predictions: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    merged = eval_df.merge(predictions, on="row_id", how="left")
    for col in ["pred_category", "pred_merchant_name", "pred_subcategory", "pred_merchant_top5"]:
        merged[col] = merged[col].fillna(MISSING)

    category_applicable = merged["category_corrected"].map(is_known_category_label)
    category = label_metrics(
        merged["category_corrected"],
        merged["pred_category"],
        applicable=category_applicable,
    )
    subcategory = label_metrics(merged["subcategory"], merged["pred_subcategory"])
    merchant = merchant_topk_metrics(merged)

    split_metrics = {}
    for split, split_df in merged.groupby("split"):
        if len(split_df) == 0:
            continue
        split_metrics[split] = {
            "category_accuracy": label_metrics(
                split_df["category_corrected"],
                split_df["pred_category"],
                applicable=split_df["category_corrected"].map(is_known_category_label),
            )["accuracy"],
            "merchant_top1_accuracy": merchant_topk_metrics(split_df)["top1_accuracy"],
            "rows": int(len(split_df)),
        }

    scored = merged[
        [
            "row_id",
            "split",
            "baseline",
            "model_input_text",
            "category_corrected",
            "merchant_name",
            "subcategory",
            "pred_category",
            "pred_merchant_name",
            "pred_subcategory",
            "pred_merchant_top5",
        ]
    ].copy()
    scored["category_correct"] = (
        scored["category_corrected"].map(canonical_label)
        == scored["pred_category"].map(canonical_label)
    )
    scored["merchant_correct"] = (
        scored["merchant_name"].map(canonical_label)
        == scored["pred_merchant_name"].map(canonical_label)
    )
    scored["subcategory_correct"] = (
        scored["subcategory"].map(canonical_label)
        == scored["pred_subcategory"].map(canonical_label)
    )

    return {
        "rows": int(len(merged)),
        "category": category,
        "merchant": merchant,
        "subcategory": subcategory,
        "by_split": split_metrics,
    }, scored


def encode_with_transformers(
    model_path: Path,
    texts: list[str],
    *,
    batch_size: int,
) -> np.ndarray:
    import torch
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModel.from_pretrained(model_path, local_files_only=True)
    model.eval()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model.to(device)

    embeddings = []
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            encoded = tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=128,
                return_tensors="pt",
            )
            encoded = {key: value.to(device) for key, value in encoded.items()}
            output = model(**encoded)
            token_embeddings = output.last_hidden_state
            attention_mask = encoded["attention_mask"].unsqueeze(-1).expand(token_embeddings.size())
            pooled = (token_embeddings * attention_mask).sum(dim=1)
            pooled = pooled / attention_mask.sum(dim=1).clamp(min=1e-9)
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            embeddings.append(pooled.cpu().numpy())
    return np.vstack(embeddings)


def embedding_predictions(
    eval_df: pd.DataFrame,
    candidates: list[dict[str, Any]],
    *,
    model_path: Path,
    batch_size: int,
) -> pd.DataFrame:
    if not candidates:
        raise ValueError("No embedding candidates were collected")
    corpus_texts = [row["text"] for row in candidates]
    query_texts = eval_df["model_input_text"].fillna("").astype(str).tolist()

    print(f"Encoding {len(corpus_texts):,} embedding candidates...", flush=True)
    corpus_embeddings = encode_with_transformers(model_path, corpus_texts, batch_size=batch_size)
    print(f"Encoding {len(query_texts):,} evaluation rows...", flush=True)
    query_embeddings = encode_with_transformers(model_path, query_texts, batch_size=batch_size)

    similarities = query_embeddings @ corpus_embeddings.T
    top_k = min(5, similarities.shape[1])
    top_indices = np.argpartition(-similarities, kth=top_k - 1, axis=1)[:, :top_k]

    predictions = []
    for row_idx, (_, row) in enumerate(eval_df.iterrows()):
        ordered = top_indices[row_idx][
            np.argsort(-similarities[row_idx, top_indices[row_idx]])
        ]
        top_candidates = [candidates[int(index)] for index in ordered]
        merchant_counts = Counter(candidate["merchant"] for candidate in top_candidates)
        category_counts = Counter(candidate["category"] for candidate in top_candidates)
        subcategory_counts = Counter(candidate["subcategory"] for candidate in top_candidates)
        predictions.append(
            {
                "row_id": row["row_id"],
                "baseline": "embedding_nearest_neighbor",
                "pred_category": top_counter_value(category_counts),
                "pred_merchant_name": top_counter_value(merchant_counts),
                "pred_subcategory": top_counter_value(subcategory_counts),
                "pred_merchant_top5": json.dumps(
                    [candidate["merchant"] for candidate in top_candidates if candidate["merchant"]]
                ),
            }
        )
    return pd.DataFrame(predictions)


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Transaction Baseline Report",
        "",
        f"Created: {report['created_at_utc']}",
        f"Evaluation rows: {report['eval_rows']:,}",
        "",
        "## Summary",
        "",
        "| Baseline | Category accuracy | Category macro-F1 | Merchant top-1 | Merchant top-5 | Merchant coverage |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in report["baselines"].items():
        cat = metrics["category"]
        merch = metrics["merchant"]
        lines.append(
            "| {name} | {cat_acc} | {cat_f1} | {merch_top1} | {merch_top5} | {merch_cov} |".format(
                name=name,
                cat_acc=format_metric(cat["accuracy"]),
                cat_f1=format_metric(cat["macro_f1"]),
                merch_top1=format_metric(merch["top1_accuracy"]),
                merch_top5=format_metric(merch["top5_accuracy"]),
                merch_cov=format_metric(merch["prediction_coverage"]),
            )
        )
    if report.get("skipped"):
        lines.extend(["", "## Skipped", ""])
        for skipped in report["skipped"]:
            lines.append(f"- {skipped}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `silver_label_identity` is a sanity check, not a deployable model.",
            "- `regex_matched_silver` measures coverage and label agreement where the silver row has a regex match.",
            "- `exact_lookup` uses training rows with matching normalized transaction text/type/payment/channel, excluding benchmark row ids and held-out merchant labels.",
            "- Embedding retrieval is only present when `--embedding-model` is passed.",
        ]
    )
    return "\n".join(lines) + "\n"


def format_metric(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}"


def main() -> None:
    args = parse_args()
    eval_path = args.eval_path.resolve()
    source_path = args.source_path.resolve()
    manifest_path = args.manifest_path.resolve()
    counts_path = args.counts_path.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(manifest_path)
    seed = int(manifest.get("config", {}).get("seed", args.seed))
    holdout_merchants = set(manifest.get("training_exclusions", {}).get("merchant_labels", []))
    excluded_row_ids = set(manifest.get("training_exclusions", {}).get("row_ids", []))

    eval_df = load_eval(eval_path)
    eval_keys = set(eval_df["_lookup_key"])
    majority_labels = load_majority_labels(counts_path, holdout_merchants)

    config = BaselineConfig(
        eval_path=str(eval_path),
        source_path=str(source_path),
        manifest_path=str(manifest_path),
        counts_path=str(counts_path),
        output_dir=str(output_dir),
        batch_size=args.batch_size,
        max_row_groups=args.max_row_groups,
        embedding_model=str(args.embedding_model.resolve()) if args.embedding_model else None,
        embedding_candidate_rows=args.embedding_candidate_rows,
        embedding_per_merchant=args.embedding_per_merchant,
        embedding_batch_size=args.embedding_batch_size,
        seed=seed,
    )

    print(f"Loading evaluation rows from {eval_path}")
    print(f"Scanning training source from {source_path}")
    columns = available_columns(source_path, BASE_COLUMNS)
    counters, embedding_candidates = build_training_lookup_and_candidates(
        source_path,
        columns,
        eval_keys,
        excluded_row_ids,
        holdout_merchants,
        batch_size=args.batch_size,
        max_row_groups=args.max_row_groups,
        seed=seed,
        collect_embedding=args.embedding_model is not None,
        embedding_candidate_rows=args.embedding_candidate_rows,
        embedding_per_merchant=args.embedding_per_merchant,
    )

    prediction_frames = [
        identity_predictions(eval_df),
        regex_matched_predictions(eval_df),
        majority_predictions(eval_df, majority_labels),
        exact_lookup_predictions(eval_df, counters),
    ]
    skipped = []

    if args.embedding_model:
        try:
            prediction_frames.append(
                embedding_predictions(
                    eval_df,
                    embedding_candidates,
                    model_path=args.embedding_model.resolve(),
                    batch_size=args.embedding_batch_size,
                )
            )
        except Exception as exc:
            skipped.append(f"embedding_nearest_neighbor failed: {type(exc).__name__}: {exc}")
    else:
        skipped.append("embedding_nearest_neighbor skipped because --embedding-model was not provided")

    all_predictions = pd.concat(prediction_frames, ignore_index=True)
    scored_frames = []
    baseline_metrics = {}
    for baseline, baseline_predictions in all_predictions.groupby("baseline"):
        metrics, scored = score_predictions(eval_df, baseline_predictions)
        baseline_metrics[baseline] = metrics
        scored_frames.append(scored)

    scored_predictions = pd.concat(scored_frames, ignore_index=True)
    predictions_path = output_dir / "baseline_predictions.csv"
    report_path = output_dir / "baseline_report.json"
    markdown_path = output_dir / "baseline_summary.md"

    scored_predictions.to_csv(predictions_path, index=False)
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": asdict(config),
        "eval_rows": int(len(eval_df)),
        "eval_split_counts": eval_df["split"].value_counts().to_dict(),
        "holdout_merchants": sorted(holdout_merchants),
        "excluded_row_ids": len(excluded_row_ids),
        "majority_labels": majority_labels,
        "embedding_candidates": len(embedding_candidates),
        "baselines": baseline_metrics,
        "skipped": skipped,
        "notes": [
            "The silver benchmark is not a substitute for manually annotated gold labels.",
            "Do not compare historical notebook metrics directly with this holdout unless they used the same manifest exclusions.",
        ],
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown_report(report), encoding="utf-8")

    print(f"Wrote {predictions_path}")
    print(f"Wrote {report_path}")
    print(f"Wrote {markdown_path}")


if __name__ == "__main__":
    main()
