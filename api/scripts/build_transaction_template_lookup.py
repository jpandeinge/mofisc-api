"""Build the privacy-minimized runtime lookup from transaction-v5 splits.

The output contains no raw transaction descriptions: only 128-bit template
digests, canonical labels, and confidence. Merchant labels are included only
when they have enough distinct templates to be credible and are not broad
bank/channel placeholders.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../api

from app.utils.transaction_template_lookup import template_digest  # noqa: E402

INPUT_COLUMNS = (
    "template",
    "input_text",
    "app_category",
    "merchant_label",
    "label_confidence",
)
DENIED_MERCHANTS = {
    "FNB",
    "Generic Employer",
    "Model",
    "Nedbank",
    "Standard Bank",
}
_DIRECTION_RE = re.compile(r" \| type: ([^|]+) \|")
_MAGIC = b"GLTL1"
_HEADER = struct.Struct(">5sI")
_RECORD = struct.Struct(">16sBBB")


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data",
        type=Path,
        default=repo_root / "ml_model" / "data" / "processed" / "transaction_v5",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=(
            repo_root / "api" / "app" / "resources" / "transaction_template_lookup.bin"
        ),
    )
    parser.add_argument("--merchant-min-templates", type=int, default=5)
    parser.add_argument("--merchant-max-templates", type=int, default=5_000)
    return parser.parse_args()


def _direction(input_text: str) -> str:
    match = _DIRECTION_RE.search(str(input_text))
    return match.group(1).strip() if match else "unknown"


def main() -> int:
    args = parse_args()
    paths = sorted(args.data.glob("*.parquet"))
    if not paths:
        print(f"ERROR: no split parquet files found under {args.data}")
        return 1

    frame = pd.concat(
        [pd.read_parquet(path, columns=INPUT_COLUMNS) for path in paths],
        ignore_index=True,
    )
    frame["direction"] = frame["input_text"].map(_direction)
    key_columns = ["template", "direction"]
    if frame.duplicated(key_columns).any():
        raise ValueError("input contains duplicate template+direction keys")

    merchant_counts = frame["merchant_label"].value_counts()
    allowed_merchants = (
        set(
            merchant_counts[
                (merchant_counts >= args.merchant_min_templates)
                & (merchant_counts <= args.merchant_max_templates)
            ].index
        )
        - DENIED_MERCHANTS
    )
    frame.loc[
        ~frame["merchant_label"].isin(allowed_merchants),
        "merchant_label",
    ] = None

    categories = sorted(str(value) for value in frame["app_category"].unique())
    merchants = sorted(str(value) for value in allowed_merchants)
    category_ids = {value: index for index, value in enumerate(categories)}
    merchant_ids = {value: index + 1 for index, value in enumerate(merchants)}
    if len(categories) > 256 or len(merchants) > 255:
        raise ValueError("lookup label dictionaries exceed the binary format")

    output_rows: list[tuple[bytes, int, int, int]] = []
    seen_digests: dict[str, tuple[str, str]] = {}
    for row in frame.itertuples(index=False):
        digest = template_digest(str(row.template), str(row.direction))
        source_key = (str(row.template), str(row.direction))
        previous = seen_digests.get(digest)
        if previous is not None and previous != source_key:
            raise RuntimeError(
                f"template digest collision: {previous!r} vs {source_key!r}"
            )
        seen_digests[digest] = source_key
        merchant = None if pd.isna(row.merchant_label) else str(row.merchant_label)
        output_rows.append(
            (
                bytes.fromhex(digest),
                category_ids[str(row.app_category)],
                merchant_ids.get(merchant, 0) if merchant is not None else 0,
                round(float(row.label_confidence) * 100),
            )
        )

    output_rows.sort(key=lambda row: row[0])
    metadata = json.dumps(
        {
            "record_count": len(output_rows),
            "categories": categories,
            "merchants": merchants,
            "description": "Hashed transaction templates; no raw descriptions",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("wb") as handle:
        handle.write(_HEADER.pack(_MAGIC, len(metadata)))
        handle.write(metadata)
        for row in output_rows:
            handle.write(_RECORD.pack(*row))

    print(
        f"wrote {len(output_rows):,} hashed templates to {args.out}; "
        f"merchant labels retained for {len(allowed_merchants)} canonical names"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
