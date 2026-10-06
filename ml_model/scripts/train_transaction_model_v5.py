"""Train a candidate app-category head and conservative merchant retriever.

This is a head-only, reproducible candidate using the existing sentence encoder.
It predicts Galahad's detailed app categories directly, evaluates on a latest-
month split and a merchant-held-out split, and optionally upweights verified
Supabase corrections. It never promotes or overwrites the production model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import joblib
import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score

SEED = 42


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _amount_bucket(value: Any) -> str:
    try:
        amount = abs(float(value))
    except (TypeError, ValueError):
        return "unknown"
    for upper, name in (
        (10.0, "lt_10"),
        (100.0, "10_100"),
        (1_000.0, "100_1k"),
        (10_000.0, "1k_10k"),
        (100_000.0, "10k_100k"),
        (float("inf"), "gt_100k"),
    ):
        if amount < upper:
            return name
    return "unknown"


def feedback_input_text(row: pd.Series) -> str:
    raw_direction = str(row.get("direction") or "").lower()
    direction = {
        "c": "credit",
        "credit": "credit",
        "d": "debit",
        "debit": "debit",
    }.get(raw_direction, "unknown")
    return (
        f"transaction: {str(row.get('description') or '').lower().strip()} "
        f"| type: {direction} | payment: unknown "
        f"| amount_bucket: {_amount_bucket(row.get('amount'))}"
    )


def _read_split(data_dir: Path, name: str) -> pd.DataFrame:
    path = data_dir / f"{name}.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"missing split: {path}")
    return pd.read_parquet(path)


def _bounded_sample(frame: pd.DataFrame, cap: int | None, seed: int) -> pd.DataFrame:
    if cap is None or len(frame) <= cap:
        return frame.reset_index(drop=True)
    # Preserve minority categories before filling the remaining budget.
    per_class = max(1, cap // max(1, frame["app_category"].nunique()))
    balanced = (
        frame.sample(frac=1.0, random_state=seed)
        .groupby("app_category", sort=False)
        .head(per_class)
    )
    remainder = frame.drop(balanced.index)
    needed = cap - len(balanced)
    if needed > 0:
        balanced = pd.concat(
            [balanced, remainder.sample(n=min(needed, len(remainder)), random_state=seed)]
        )
    return balanced.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def _encode(
    model: SentenceTransformer, texts: pd.Series | list[str], batch_size: int
) -> np.ndarray:
    return np.asarray(
        model.encode(
            list(texts),
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=True,
        ),
        dtype=np.float32,
    )


def _classification_metrics(
    head: LogisticRegression,
    embeddings: np.ndarray,
    labels: pd.Series,
) -> dict[str, Any]:
    predictions = head.predict(embeddings)
    probabilities = head.predict_proba(embeddings)
    return {
        "n": int(len(labels)),
        "accuracy": round(float(accuracy_score(labels, predictions)), 4),
        "macro_f1": round(float(f1_score(labels, predictions, average="macro")), 4),
        "weighted_f1": round(
            float(f1_score(labels, predictions, average="weighted")), 4
        ),
        "mean_confidence": round(float(probabilities.max(axis=1).mean()), 4),
    }


def _confidence_threshold(
    head: LogisticRegression,
    embeddings: np.ndarray,
    labels: pd.Series,
    target_precision: float,
) -> tuple[float, dict[str, float]]:
    probabilities = head.predict_proba(embeddings)
    predictions = head.classes_[probabilities.argmax(axis=1)]
    confidence = probabilities.max(axis=1)
    correct = predictions == labels.to_numpy()
    selected_threshold = 0.99
    selected_coverage = 0.0
    selected_precision = 0.0
    for threshold in np.arange(0.50, 1.00, 0.01):
        accepted = confidence >= threshold
        if not accepted.any():
            break
        precision = float(correct[accepted].mean())
        if precision >= target_precision:
            selected_threshold = round(float(threshold), 2)
            selected_coverage = float(accepted.mean())
            selected_precision = precision
            break
    return selected_threshold, {
        "target_precision": target_precision,
        "accepted_coverage": round(selected_coverage, 4),
        "accepted_precision": round(selected_precision, 4),
    }


def _build_merchant_index(
    train: pd.DataFrame,
    embeddings: np.ndarray,
    min_templates: int,
    max_templates: int,
    exemplar_cap: int,
) -> tuple[np.ndarray, list[str]]:
    labelled = train[train["merchant_label"].notna()].copy()
    labelled["merchant_label"] = labelled["merchant_label"].astype(str)
    counts = labelled["merchant_label"].value_counts()
    merchants = sorted(
        counts[(counts >= min_templates) & (counts <= max_templates)].index
    )
    centroids: list[np.ndarray] = []
    kept_names: list[str] = []
    for merchant in merchants:
        indices = labelled.index[labelled["merchant_label"] == merchant].to_numpy()
        indices = indices[:exemplar_cap]
        centroid = embeddings[indices].mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm <= 0:
            continue
        centroids.append((centroid / norm).astype(np.float32))
        kept_names.append(merchant)
    if not centroids:
        raise ValueError("no merchants met the index thresholds")
    return np.vstack(centroids), kept_names


def _merchant_threshold(
    validation: pd.DataFrame,
    embeddings: np.ndarray,
    unknown_embeddings: np.ndarray,
    centroids: np.ndarray,
    names: list[str],
    target_precision: float,
    min_unknown_reject: float,
    min_similarity: float,
) -> tuple[float, float, dict[str, Any]]:
    name_to_index = {name: index for index, name in enumerate(names)}
    mask = validation["merchant_label"].astype("string").isin(names).to_numpy()
    if not mask.any():
        return 0.80, 0.10, {"reason": "no indexed validation merchants"}
    labels = validation.loc[mask, "merchant_label"].astype(str).to_numpy()
    similarities = embeddings[mask] @ centroids.T
    own_columns = np.array([name_to_index[label] for label in labels])
    top_order = np.argsort(-similarities, axis=1)
    top_score = similarities[np.arange(len(similarities)), top_order[:, 0]]
    second_score = similarities[np.arange(len(similarities)), top_order[:, 1]]
    correct = np.array(names, dtype=object)[top_order[:, 0]] == labels

    if len(unknown_embeddings):
        unknown_score = (unknown_embeddings @ centroids.T).max(axis=1)
        unknown_source = "merchant_holdout_test"
    else:
        # Fallback for a bounded corpus without a usable held-out set.
        leave_one_out = similarities.copy()
        leave_one_out[np.arange(len(leave_one_out)), own_columns] = -1.0
        unknown_score = leave_one_out.max(axis=1)
        unknown_source = "leave_one_out_known_merchants"

    best: tuple[float, float, float, float] | None = None
    for threshold in np.arange(min_similarity, 0.96, 0.01):
        accepted = top_score >= threshold
        if not accepted.any() or float(correct[accepted].mean()) < target_precision:
            continue
        rejected = float((unknown_score < threshold).mean())
        if rejected < min_unknown_reject:
            continue
        coverage = float(accepted.mean())
        if best is None or coverage > best[0]:
            best = (coverage, round(float(threshold), 2), rejected, float(correct[accepted].mean()))
    if best is None:
        return 0.80, 0.10, {
            "reason": "no threshold met precision/rejection constraints",
            "target_precision": target_precision,
            "min_unknown_reject": min_unknown_reject,
            "min_similarity": min_similarity,
        }

    coverage, threshold, rejected, precision = best
    accepted = top_score >= threshold
    margins = top_score - second_score
    correct_accepted_margins = margins[accepted & correct]
    margin = (
        round(float(np.quantile(correct_accepted_margins, 0.05)), 3)
        if len(correct_accepted_margins)
        else 0.10
    )
    margin = max(0.02, min(margin, 0.20))
    return threshold, margin, {
        "known_coverage": round(coverage, 4),
        "known_precision": round(precision, 4),
        "unknown_rejection": round(rejected, 4),
        "unknown_source": unknown_source,
        "margin": margin,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ml_root = Path(__file__).resolve().parents[1]
    repo_root = ml_root.parent
    parser.add_argument(
        "--data",
        type=Path,
        default=ml_root / "data" / "processed" / "transaction_v5",
    )
    parser.add_argument(
        "--encoder",
        type=Path,
        default=repo_root / "api" / "app" / "resources" / "models" / "pfc_v4" / "encoder",
    )
    parser.add_argument(
        "--feedback",
        type=Path,
        default=ml_root / "data" / "pragma" / "raw" / "supervised_feedback.parquet",
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--max-train", type=int, default=250_000)
    parser.add_argument("--max-eval", type=int, default=50_000)
    parser.add_argument("--min-category-templates", type=int, default=20)
    parser.add_argument("--merchant-min-templates", type=int, default=5)
    parser.add_argument(
        "--merchant-max-templates",
        type=int,
        default=5_000,
        help="exclude over-broad labels that dominate the unique-template corpus",
    )
    parser.add_argument("--merchant-exemplar-cap", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--target-category-precision", type=float, default=0.90)
    parser.add_argument("--target-merchant-precision", type=float, default=0.97)
    parser.add_argument("--min-unknown-reject", type=float, default=0.97)
    parser.add_argument("--merchant-min-similarity", type=float, default=0.65)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()

    run_id = datetime.now(UTC).strftime("transaction-v5-%Y%m%dT%H%M%SZ")
    out = args.out or (ml_root / "models" / run_id)
    out.mkdir(parents=True, exist_ok=False)

    train = _read_split(args.data, "train")
    validation = _read_split(args.data, "validation")
    time_test = _read_split(args.data, "time_forward_test")
    merchant_test = _read_split(args.data, "merchant_holdout_test")
    if args.smoke:
        args.max_train = min(args.max_train, 5_000)
        args.max_eval = min(args.max_eval, 1_000)

    category_counts = train["app_category"].value_counts()
    supported = set(
        category_counts[category_counts >= args.min_category_templates].index
    )
    train = train[train["app_category"].isin(supported)].copy()
    train = _bounded_sample(train, args.max_train, args.seed)
    validation = _bounded_sample(
        validation[validation["app_category"].isin(supported)].copy(),
        args.max_eval,
        args.seed,
    )
    time_test = _bounded_sample(
        time_test[time_test["app_category"].isin(supported)].copy(),
        args.max_eval,
        args.seed,
    )
    merchant_test = _bounded_sample(
        merchant_test[merchant_test["app_category"].isin(supported)].copy(),
        args.max_eval,
        args.seed,
    )

    model = SentenceTransformer(str(args.encoder), device="cpu")
    X_train = _encode(model, train["input_text"], args.batch_size)
    X_validation = _encode(model, validation["input_text"], args.batch_size)
    X_time = _encode(model, time_test["input_text"], args.batch_size)
    X_merchant = _encode(model, merchant_test["input_text"], args.batch_size)

    category_feedback_count = 0
    merchant_feedback: list[tuple[str, np.ndarray]] = []
    if args.feedback.is_file():
        feedback = pd.read_parquet(args.feedback)
        feedback["input_text"] = feedback.apply(feedback_input_text, axis=1)
        feedback_embeddings = _encode(model, feedback["input_text"], args.batch_size)
        category_mask = (
            (feedback["field_name"] == "category")
            & feedback["corrected_value"].isin(supported)
        )
        if category_mask.any():
            category_feedback_count = int(category_mask.sum())
            X_train = np.vstack(
                [X_train, feedback_embeddings[category_mask.to_numpy()]]
            )
            feedback_rows = feedback.loc[category_mask]
            train = pd.concat(
                [
                    train,
                    pd.DataFrame(
                        {
                            "app_category": feedback_rows["corrected_value"].to_numpy(),
                            "primary_label": pd.NA,
                            "merchant_label": pd.NA,
                            "sample_weight": 5.0,
                        }
                    ),
                ],
                ignore_index=True,
            )
        merchant_mask = feedback["field_name"] == "merchant_name"
        for label, embedding in zip(
            feedback.loc[merchant_mask, "corrected_value"],
            feedback_embeddings[merchant_mask.to_numpy()],
        ):
            merchant_feedback.append((str(label), embedding))

    category_head = LogisticRegression(
        max_iter=500,
        C=3.0,
        class_weight="balanced",
        n_jobs=1,
        random_state=args.seed,
    )
    category_head.fit(
        X_train,
        train["app_category"],
        sample_weight=train["sample_weight"].astype(float),
    )

    primary_rows = train["primary_label"].notna().to_numpy()
    primary_head = LogisticRegression(
        max_iter=500,
        C=3.0,
        class_weight="balanced",
        n_jobs=1,
        random_state=args.seed,
    )
    primary_head.fit(
        X_train[primary_rows],
        train.loc[primary_rows, "primary_label"],
        sample_weight=train.loc[primary_rows, "sample_weight"].astype(float),
    )

    category_threshold, threshold_metrics = _confidence_threshold(
        category_head,
        X_validation,
        validation["app_category"],
        args.target_category_precision,
    )
    category_metrics = {
        "validation": _classification_metrics(
            category_head, X_validation, validation["app_category"]
        ),
        "time_forward_test": _classification_metrics(
            category_head, X_time, time_test["app_category"]
        ),
        "merchant_holdout_test": _classification_metrics(
            category_head, X_merchant, merchant_test["app_category"]
        ),
    }

    # The merchant index uses the original train portion because appended
    # category-only feedback has no merchant label.
    merchant_train = train.iloc[: len(X_train) - category_feedback_count].copy()
    merchant_X = X_train[: len(merchant_train)]
    centroids, merchant_names = _build_merchant_index(
        merchant_train,
        merchant_X,
        args.merchant_min_templates,
        args.merchant_max_templates,
        args.merchant_exemplar_cap,
    )
    if merchant_feedback:
        # A corrected merchant gets a centroid only after at least two examples;
        # one edit is retained in feedback but cannot safely define a cluster.
        feedback_by_name: dict[str, list[np.ndarray]] = {}
        for name, embedding in merchant_feedback:
            feedback_by_name.setdefault(name, []).append(embedding)
        for name, vectors in sorted(feedback_by_name.items()):
            if len(vectors) < 2:
                continue
            centroid = np.mean(vectors, axis=0)
            centroid /= np.linalg.norm(centroid)
            if name in merchant_names:
                centroids[merchant_names.index(name)] = centroid
            else:
                merchant_names.append(name)
                centroids = np.vstack([centroids, centroid.astype(np.float32)])

    merchant_threshold, merchant_margin, merchant_calibration = _merchant_threshold(
        validation,
        X_validation,
        X_merchant,
        centroids,
        merchant_names,
        args.target_merchant_precision,
        args.min_unknown_reject,
        args.merchant_min_similarity,
    )

    encoder_out = out / "encoder"
    shutil.copytree(args.encoder, encoder_out)
    joblib.dump(category_head, out / "category_head.joblib")
    joblib.dump(primary_head, out / "primary_head.joblib")
    np.savez_compressed(
        out / "merchant_index.npz",
        centroids=centroids.astype(np.float32),
        names=np.array(merchant_names, dtype=object),
    )
    label_classes = {
        "taxonomy": "galahad_v5_app_categories",
        "model_version": run_id,
        "category_head_kind": "app_category",
        "categories": [str(value) for value in category_head.classes_],
        "primary_categories": [str(value) for value in primary_head.classes_],
        "app_category_map": {
            str(value): str(value) for value in category_head.classes_
        },
        "category_auto_accept_threshold": category_threshold,
        "merchant_threshold": merchant_threshold,
        "merchant_margin": merchant_margin,
    }
    (out / "label_classes.json").write_text(
        json.dumps(label_classes, indent=2) + "\n", encoding="utf-8"
    )
    metrics = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model_version": run_id,
        "candidate_only": True,
        "encoder_source": str(args.encoder),
        "dataset": str(args.data),
        "train_rows": len(train),
        "category_feedback_rows": category_feedback_count,
        "merchant_feedback_rows": len(merchant_feedback),
        "supported_categories": sorted(supported),
        "training_config": {
            "seed": args.seed,
            "max_train": args.max_train,
            "max_eval": args.max_eval,
            "min_category_templates": args.min_category_templates,
            "merchant_min_templates": args.merchant_min_templates,
            "merchant_max_templates": args.merchant_max_templates,
            "merchant_exemplar_cap": args.merchant_exemplar_cap,
            "target_category_precision": args.target_category_precision,
            "target_merchant_precision": args.target_merchant_precision,
            "min_unknown_reject": args.min_unknown_reject,
            "merchant_min_similarity": args.merchant_min_similarity,
        },
        "category_metrics": category_metrics,
        "category_threshold": threshold_metrics,
        "merchant_index_size": len(merchant_names),
        "merchant_calibration": merchant_calibration,
        "limitations": [
            "labels are predominantly regex-derived silver labels from one bank",
            "merchant coverage cannot exceed reviewed merchant/correction labels",
            "promotion requires human-reviewed and live-bank evaluation",
        ],
    }
    (out / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    (out / "category_report_time_forward.txt").write_text(
        classification_report(
            time_test["app_category"],
            category_head.predict(X_time),
            zero_division=0,
        )
    )
    manifest = {
        "model_version": run_id,
        "encoder_sha256": _sha256(encoder_out / "model.safetensors"),
        "category_head_sha256": _sha256(out / "category_head.joblib"),
        "primary_head_sha256": _sha256(out / "primary_head.joblib"),
        "merchant_index_sha256": _sha256(out / "merchant_index.npz"),
        "promotion_status": "candidate_not_promoted",
    }
    (out / "model_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2))
    print(f"candidate model written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
