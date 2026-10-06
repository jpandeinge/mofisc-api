"""Backfill stored transactions with the local PFC (Plaid-aligned) model.

Reclassifies every selected transaction using the same model + rules +
merchant-unknown-veto pipeline that serves new uploads. The complete selected
set is classified before any row is changed, so normalized templates stay
consistent across Supabase pages and model batches.

Safety policy:
- DRY RUN by default — prints what would change; pass --apply to write.
- Fields the user manually edited (per transaction_edit_history, the only
  table written exclusively by user-facing edit endpoints — the edit_count /
  user_edited_fields columns are bumped by a trigger on ANY category update,
  including the classifier's own writes, so they cannot distinguish user
  edits) are never touched unless --include-user-edited.
- A real category is never downgraded to Uncategorized/fallback unless
  --allow-downgrade (vetoed or low-confidence predictions keep the old label).
- Merchants: reviewed corrections and deterministic rules repair conflicting
  names automatically; model/extraction only fill blanks unless
  --overwrite-merchants is explicit.
- Near-identical descriptions are normalized into user-scoped templates. A
  reviewed correction has highest priority, then a deterministic rule, then
  model/retrieval evidence.

Usage (from galahad-api/api):
    python scripts/backfill_categories.py                 # dry run, whole table
    python scripts/backfill_categories.py --limit 200     # dry run, first 200
    python scripts/backfill_categories.py --apply         # write changes
    python scripts/backfill_categories.py --user-id UUID --apply
"""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # .../api

from app.config.settings import settings  # noqa: E402
from app.utils.local_transaction_model import (  # noqa: E402
    classify_with_local_model,
    local_model_available,
)
from app.utils.supabase_utils import get_supabase_client  # noqa: E402
from app.utils.transaction_consistency import (  # noqa: E402
    ClassificationDecision,
    fetch_reviewed_template_overrides,
    harmonize_classification_decisions,
    is_fallback_category,
)
from app.utils.transaction_rule_classifier import (  # noqa: E402
    classify_transaction_with_rules,
    extract_merchant_candidate,
    transaction_description,
)
from app.utils.transaction_template_lookup import (  # noqa: E402
    classify_transaction_with_template_lookup,
)

FETCH_COLUMNS = (
    "id,user_id,statement_id,transaction_description,transaction_amount,"
    "transaction_type,category,confidence,merchant_name,original_merchant_name,"
    "original_category"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes (default: dry run)",
    )
    parser.add_argument("--user-id", help="restrict to one user")
    parser.add_argument("--statement-id", help="restrict to one statement")
    parser.add_argument("--limit", type=int, help="stop after N transactions")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="rows per fetch/encode batch",
    )
    parser.add_argument(
        "--include-user-edited",
        action="store_true",
        help="also update rows the user edited manually",
    )
    parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="allow replacing a real category with Uncategorized",
    )
    parser.add_argument(
        "--overwrite-merchants",
        action="store_true",
        help="replace existing (non-user-edited) merchant names, not just fill missing",
    )
    return parser.parse_args()


def fetch_user_edited_fields(supabase, transaction_ids: list) -> set:
    """Return (transaction_id, field_name) pairs edited via app endpoints."""
    edited: set = set()
    for start in range(0, len(transaction_ids), 100):
        chunk = transaction_ids[start : start + 100]
        rows = (
            supabase.table("transaction_edit_history")
            .select("transaction_id,field_name")
            .in_("transaction_id", chunk)
            .execute()
            .data
            or []
        )
        edited.update((str(row["transaction_id"]), row["field_name"]) for row in rows)
    return edited


def fetch_target_rows(supabase, table: str, args: argparse.Namespace) -> list[dict]:
    """Fetch the complete target set before choosing canonical values."""
    rows: list[dict] = []
    offset = 0
    while True:
        page_size = args.batch_size
        if args.limit is not None:
            page_size = min(page_size, args.limit - len(rows))
            if page_size <= 0:
                break

        query = (
            supabase.table(table)
            .select(FETCH_COLUMNS)
            .order("id")
            .range(offset, offset + page_size - 1)
        )
        if args.user_id:
            query = query.eq("user_id", args.user_id)
        if args.statement_id:
            query = query.eq("statement_id", args.statement_id)
        page = query.execute().data or []
        if not page:
            break

        rows.extend(page)
        offset += len(page)
        print(f"  fetched {len(rows)} rows...", flush=True)
        if len(page) < page_size:
            break
    return rows


def propose_decisions(
    rows: list[dict],
    *,
    batch_size: int,
) -> tuple[list[ClassificationDecision], int]:
    """Run bounded model batches, then layer deterministic evidence on top."""
    proposed: list[ClassificationDecision] = []
    vetoed = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        predictions = classify_with_local_model(batch)
        if predictions is None:
            raise RuntimeError("local model became unavailable during backfill")

        for row, prediction in zip(batch, predictions):
            rule_match = classify_transaction_with_rules(row)
            template_match = classify_transaction_with_template_lookup(row)
            corrected_category = prediction.category
            confidence = 0.0 if prediction.vetoed else prediction.confidence
            category_source = "fallback" if prediction.vetoed else "local_model"
            if prediction.vetoed:
                vetoed += 1
            if template_match and template_match.category:
                corrected_category = template_match.category
                confidence = max(float(confidence), template_match.confidence)
                category_source = "exact_lookup"
            if rule_match and rule_match.category:
                corrected_category = rule_match.category
                confidence = max(float(confidence), rule_match.confidence)
                category_source = "rule"

            model_merchant = (
                None if prediction.merchant_unknown else prediction.merchant_name
            )
            extracted_merchant = extract_merchant_candidate(
                transaction_description(row)
            )
            if rule_match and rule_match.merchant_name:
                merchant_name = rule_match.merchant_name
                merchant_source = "rule"
            elif template_match and template_match.merchant_name:
                merchant_name = template_match.merchant_name
                merchant_source = "exact_lookup"
            elif model_merchant:
                merchant_name = model_merchant
                merchant_source = "local_model"
            else:
                merchant_name = extracted_merchant
                merchant_source = "extracted" if extracted_merchant else "unknown"

            proposed.append(
                ClassificationDecision(
                    category=corrected_category,
                    confidence=float(confidence),
                    merchant_name=merchant_name,
                    category_source=category_source,
                    merchant_source=merchant_source,
                )
            )
        print(f"  classified {len(proposed)} rows...", flush=True)
    return proposed, vetoed


def main() -> int:
    args = parse_args()

    if args.batch_size <= 0:
        print("ERROR: --batch-size must be positive")
        return 2
    if args.limit is not None and args.limit <= 0:
        print("ERROR: --limit must be positive")
        return 2
    if not local_model_available():
        print("ERROR: local model unavailable — check LOCAL_MODEL_DIR / artifacts.")
        return 1

    supabase = get_supabase_client(use_service_role=True)
    table = settings.SUPABASE_TABLE_NAME
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] backfilling table '{table}' with the local PFC model")

    try:
        reviewed_overrides, all_edited_pairs = fetch_reviewed_template_overrides(
            supabase, user_id=args.user_id
        )
    except Exception as exc:
        if not args.include_user_edited:
            print(
                "ERROR: could not load reviewed corrections, so the backfill "
                f"cannot safely protect user edits: {exc}"
            )
            return 1
        reviewed_overrides = {}
        all_edited_pairs = set()
        print(f"WARNING: proceeding without reviewed corrections: {exc}")

    rows = fetch_target_rows(supabase, table, args)
    scanned = len(rows)
    if not rows:
        print("No matching transactions found.")
        return 0

    if args.include_user_edited:
        edited_pairs = set()
    elif all_edited_pairs:
        row_ids = {str(row["id"]) for row in rows}
        edited_pairs = {pair for pair in all_edited_pairs if pair[0] in row_ids}
    else:
        edited_pairs = fetch_user_edited_fields(supabase, [row["id"] for row in rows])

    proposed_decisions, vetoed = propose_decisions(
        rows,
        batch_size=args.batch_size,
    )
    decisions = harmonize_classification_decisions(
        rows,
        proposed_decisions,
        reviewed_overrides,
    )

    category_changes = 0
    merchant_fills = 0
    merchant_overwrites = 0
    protected_skips = 0
    downgrade_skips = 0
    unchanged = 0
    failed = 0
    transitions: Counter = Counter()

    for row, decision in zip(rows, decisions):
        update_payload: dict = {}
        old_category = row.get("category")
        corrected_category = decision.category

        category_edited = (str(row["id"]), "category") in edited_pairs
        if not category_edited or decision.category_source == "reviewed":
            if is_fallback_category(corrected_category) and not is_fallback_category(
                old_category
            ):
                if args.allow_downgrade:
                    update_payload["category"] = corrected_category
                    update_payload["confidence"] = float(decision.confidence)
                else:
                    downgrade_skips += 1
            elif corrected_category != old_category:
                update_payload["category"] = corrected_category
                update_payload["confidence"] = float(decision.confidence)
                if row.get("original_category") is None and old_category:
                    update_payload["original_category"] = old_category
        else:
            protected_skips += 1

        new_merchant = decision.merchant_name
        existing_merchant = row.get("merchant_name")
        merchant_edited = (str(row["id"]), "merchant_name") in edited_pairs
        if new_merchant and (
            not merchant_edited or decision.merchant_source == "reviewed"
        ):
            if not existing_merchant:
                update_payload["merchant_name"] = new_merchant
                merchant_fills += 1
            elif existing_merchant != new_merchant and (
                args.overwrite_merchants
                or decision.merchant_source in {"reviewed", "rule", "exact_lookup"}
            ):
                update_payload["merchant_name"] = new_merchant
                merchant_overwrites += 1
            if (
                "merchant_name" in update_payload
                and row.get("original_merchant_name") is None
            ):
                update_payload["original_merchant_name"] = (
                    existing_merchant or new_merchant
                )
        elif merchant_edited:
            protected_skips += 1

        if "category" in update_payload:
            category_changes += 1
            transitions[(old_category, corrected_category)] += 1
        elif not update_payload:
            unchanged += 1

        if args.apply and update_payload:
            try:
                supabase.table(table).update(update_payload).eq(
                    "id", row["id"]
                ).execute()
            except Exception as exc:  # keep going; report at the end
                failed += 1
                print(f"  update failed for {row['id']}: {exc}")

    print()
    print(f"[{mode}] summary")
    print(f"  scanned:              {scanned}")
    print(f"  category changes:     {category_changes}")
    print(f"  merchant fills:       {merchant_fills}")
    print(f"  merchant overwrites:  {merchant_overwrites}")
    print(f"  unchanged:            {unchanged}")
    print(f"  user-edit protected:  {protected_skips}")
    print(f"  downgrade skipped:    {downgrade_skips}")
    print(f"  vetoed predictions:   {vetoed}")
    print(f"  failed updates:       {failed}")
    if transitions:
        print("  top category transitions:")
        for (old, new), count in transitions.most_common(15):
            print(f"    {old!r} -> {new!r}: {count}")
    if not args.apply:
        print("\nDry run only — re-run with --apply to write these changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
