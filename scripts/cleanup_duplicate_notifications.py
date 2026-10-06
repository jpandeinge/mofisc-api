#!/usr/bin/env python3
"""
Cleanup duplicate notifications in Supabase.

Usage:
  python scripts/cleanup_duplicate_notifications.py --days 30
  python scripts/cleanup_duplicate_notifications.py --days 7 --dry-run
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(BASE_DIR))

from api.app.utils.supabase_utils import get_supabase_client  # noqa: E402


def compute_dedupe_key(notification: Dict) -> str:
    data = notification.get("data") or {}
    dedupe_key = data.get("dedupe_key")
    if dedupe_key:
        return f"{notification.get('user_id')}|{notification.get('notification_type')}|{dedupe_key}"

    primary_id = (
        data.get("subscription_id")
        or data.get("budget_id")
        or data.get("goal_id")
        or data.get("transaction_id")
        or data.get("statement_id")
        or ""
    )

    return (
        f"{notification.get('user_id')}|{notification.get('notification_type')}|"
        f"{primary_id}|{notification.get('title')}|{notification.get('body')}|"
        f"{notification.get('action_url')}"
    )


def chunk_list(items: List[str], size: int) -> List[List[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def cleanup_duplicates(days: int, dry_run: bool, batch_size: int) -> int:
    supabase = get_supabase_client(use_service_role=True)
    since = datetime.utcnow() - timedelta(days=days)
    since_iso = since.isoformat()

    seen: Dict[str, str] = {}
    delete_ids: List[str] = []

    offset = 0
    page_size = 1000

    while True:
        response = (
            supabase.table("notifications")
            .select(
                "id,user_id,notification_type,title,body,data,created_at,action_url"
            )
            .gte("created_at", since_iso)
            .order("created_at", desc=True)
            .range(offset, offset + page_size - 1)
            .execute()
        )

        data = response.data or []
        if not data:
            break

        for notification in data:
            key = compute_dedupe_key(notification)
            if key in seen:
                delete_ids.append(notification["id"])
            else:
                seen[key] = notification["id"]

        if len(data) < page_size:
            break

        offset += page_size

    if dry_run or not delete_ids:
        return len(delete_ids)

    for chunk in chunk_list(delete_ids, batch_size):
        supabase.table("notifications").delete().in_("id", chunk).execute()

    return len(delete_ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cleanup duplicate notifications.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report duplicates without deleting.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Delete batch size.",
    )
    args = parser.parse_args()

    deleted_count = cleanup_duplicates(args.days, args.dry_run, args.batch_size)
    mode = "Would remove" if args.dry_run else "Removed"
    print(f"{mode} {deleted_count} duplicate notifications.")


if __name__ == "__main__":
    main()
