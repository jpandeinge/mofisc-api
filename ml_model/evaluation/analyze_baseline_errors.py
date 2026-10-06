"""Create compact diagnostics from baseline_predictions.csv."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ML_MODEL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_DIR = ML_MODEL_DIR / "data" / "evaluation" / "baselines"
DEFAULT_PREDICTIONS = DEFAULT_BASELINE_DIR / "baseline_predictions.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze baseline prediction errors.")
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_BASELINE_DIR)
    parser.add_argument("--top-n", type=int, default=15)
    return parser.parse_args()


def canonical(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return " ".join(str(value).strip().lower().split())


def top_records(frame: pd.DataFrame, columns: list[str], top_n: int) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    grouped = frame.groupby(columns, dropna=False).size().reset_index(name="count")
    grouped = grouped.sort_values("count", ascending=False).head(top_n)
    return grouped.to_dict("records")


def analyze_baseline(frame: pd.DataFrame, top_n: int) -> dict[str, Any]:
    data = frame.copy()
    data["_gold_category"] = data["category_corrected"].map(canonical)
    data["_pred_category"] = data["pred_category"].map(canonical)
    data["_gold_merchant"] = data["merchant_name"].map(canonical)
    data["_pred_merchant"] = data["pred_merchant_name"].map(canonical)

    category_errors = data[
        (data["_gold_category"] != "")
        & (data["_pred_category"] != "")
        & (data["_gold_category"] != data["_pred_category"])
    ]
    merchant_false_links = data[
        (data["_gold_merchant"] != "")
        & (data["_pred_merchant"] != "")
        & (data["_gold_merchant"] != data["_pred_merchant"])
    ]
    merchant_missing = data[(data["_gold_merchant"] != "") & (data["_pred_merchant"] == "")]

    split_summary = (
        data.groupby("split", dropna=False)
        .agg(
            rows=("row_id", "count"),
            category_correct=("category_correct", "mean"),
            merchant_correct=("merchant_correct", "mean"),
            subcategory_correct=("subcategory_correct", "mean"),
        )
        .reset_index()
    )

    return {
        "rows": int(len(data)),
        "split_summary": split_summary.to_dict("records"),
        "top_category_confusions": top_records(
            category_errors,
            ["category_corrected", "pred_category"],
            top_n,
        ),
        "top_merchant_false_links": top_records(
            merchant_false_links,
            ["merchant_name", "pred_merchant_name"],
            top_n,
        ),
        "top_predicted_merchants_for_unseen_split": top_records(
            data[(data["split"] == "silver_merchant_unseen") & (data["_pred_merchant"] != "")],
            ["pred_merchant_name"],
            top_n,
        ),
        "merchant_missing_predictions": int(len(merchant_missing)),
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Baseline Error Analysis",
        "",
        f"Created: {report['created_at_utc']}",
        "",
    ]
    for baseline, diagnostics in report["baselines"].items():
        lines.extend([f"## {baseline}", ""])
        lines.append("### Split Summary")
        lines.append("")
        lines.append("| Split | Rows | Category correct | Merchant correct | Subcategory correct |")
        lines.append("|---|---:|---:|---:|---:|")
        for row in diagnostics["split_summary"]:
            lines.append(
                "| {split} | {rows} | {cat:.4f} | {merchant:.4f} | {subcat:.4f} |".format(
                    split=row["split"],
                    rows=int(row["rows"]),
                    cat=float(row["category_correct"]),
                    merchant=float(row["merchant_correct"]),
                    subcat=float(row["subcategory_correct"]),
                )
            )
        lines.append("")

        if diagnostics["top_category_confusions"]:
            lines.append("### Top Category Confusions")
            lines.append("")
            lines.append("| Gold | Predicted | Count |")
            lines.append("|---|---|---:|")
            for row in diagnostics["top_category_confusions"]:
                lines.append(
                    f"| {row['category_corrected']} | {row['pred_category']} | {int(row['count'])} |"
                )
            lines.append("")

        if diagnostics["top_merchant_false_links"]:
            lines.append("### Top Merchant False Links")
            lines.append("")
            lines.append("| Gold | Predicted | Count |")
            lines.append("|---|---|---:|")
            for row in diagnostics["top_merchant_false_links"]:
                lines.append(
                    f"| {row['merchant_name']} | {row['pred_merchant_name']} | {int(row['count'])} |"
                )
            lines.append("")

        if diagnostics["top_predicted_merchants_for_unseen_split"]:
            lines.append("### Unseen Split Predicted Merchants")
            lines.append("")
            lines.append("| Predicted merchant | Count |")
            lines.append("|---|---:|")
            for row in diagnostics["top_predicted_merchants_for_unseen_split"]:
                lines.append(f"| {row['pred_merchant_name']} | {int(row['count'])} |")
            lines.append("")

        lines.append(
            f"Merchant missing predictions: {diagnostics['merchant_missing_predictions']}"
        )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    predictions_path = args.predictions.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    predictions = pd.read_csv(predictions_path)
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "predictions_path": str(predictions_path),
        "baselines": {
            baseline: analyze_baseline(frame, args.top_n)
            for baseline, frame in predictions.groupby("baseline")
        },
    }

    json_path = output_dir / "baseline_error_analysis.json"
    md_path = output_dir / "baseline_error_analysis.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
