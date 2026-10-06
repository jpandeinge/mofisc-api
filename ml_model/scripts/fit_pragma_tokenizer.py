"""Fit and save a Pragmatiq tokenizer without tokenizing artificial histories.

Use this only with a corpus whose manifest declares
``purpose=tokenizer_fit_only``. The resulting tokenizer can then be applied to
real pseudonymised histories with ``pragmatiq tokenize --tokenizer-dir``.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from pragmatiq.data.tokenizer import PragmaTokenizer, TokenizerConfig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n-workers", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--calendar-tz", default="Africa/Windhoek")
    args = parser.parse_args()

    manifest_path = args.corpus / "corpus_manifest.json"
    if not manifest_path.is_file():
        parser.error(f"missing tokenizer-only manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    if (
        manifest.get("purpose") != "tokenizer_fit_only"
        or manifest.get("pretraining_allowed") is not False
    ):
        parser.error("corpus manifest does not identify a tokenizer-only corpus")

    config = TokenizerConfig(
        seed=args.seed,
        calendar_tz=args.calendar_tz,
        force_numeric=(
            "amount",
            "balance",
            "target_amount",
            "current_amount",
            "budget_amount",
            "recurring_amount",
        ),
        force_categorical=(
            "event_type",
            "direction",
            "payment_type",
            "channel",
            "goal_category",
            "budget_category",
            "budget_period",
            "plan",
            "plan_status",
        ),
    )
    tokenizer = PragmaTokenizer(config).fit(args.corpus, n_workers=args.n_workers)
    tokenizer.save(args.out)
    output_manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus": str(args.corpus),
        "corpus_manifest": manifest,
        "tokenizer_hash": tokenizer.content_hash,
        "vocab_size": tokenizer.vocab_size,
        "key_count": len(tokenizer.key_vocab),
        "field_kinds": tokenizer.field_kind,
        "calendar_tz": args.calendar_tz,
        "seed": args.seed,
    }
    (args.out / "fit_manifest.json").write_text(
        json.dumps(output_manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"saved tokenizer hash={tokenizer.content_hash} "
        f"vocab={tokenizer.vocab_size:,} -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
