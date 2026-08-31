#!/usr/bin/env python3
"""
LABAYKANUSUK — historical social archive import.

Reads logs/publication_history.json and imports already-published social content
into Shopify without publishing anything again to Instagram.

Default window: last 14 days.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import shopify_archive as sa


def parse_dt(value: Any) -> datetime | None:
    raw = sa.clean(value)
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def published_at(entry: dict) -> str:
    return sa.event_published_at(entry)


def slot_of(entry: dict) -> int | None:
    for key in ("slot", "slot_id", "publication_slot"):
        if entry.get(key) is not None:
            try:
                return int(entry.get(key))
            except (TypeError, ValueError):
                pass
    return None


def image_url_of(entry: dict, item: dict) -> str:
    candidates = (
        entry.get("image_url"),
        entry.get("media_url"),
        entry.get("public_url"),
        entry.get("container_url"),
        entry.get("media_url1"),
        entry.get("image"),
        item.get("image_url"),
        item.get("photo_url"),
        item.get("background_image"),
        item.get("image"),
        item.get("image1_url"),
    )
    for value in candidates:
        url = sa.normalize_http_url(value)
        if url:
            return url
    return ""


def instagram_url_of(entry: dict) -> str:
    for key in ("instagram_url", "instagram_permalink", "permalink", "post_url"):
        url = sa.normalize_http_url(entry.get(key))
        if url:
            return url
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    history = sa.load_json(sa.HISTORY, [])
    if not isinstance(history, list):
        raise RuntimeError("logs/publication_history.json must contain a JSON list")

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, args.days))
    grouped: dict[str, list[dict]] = defaultdict(list)

    for entry in history:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "published":
            continue
        content_id = sa.clean(entry.get("content_id"))
        if not content_id:
            continue
        dt = parse_dt(published_at(entry))
        if dt and dt < cutoff:
            continue
        grouped[content_id].append(entry)

    _, domain, token, version, _ = sa.shopify_credentials()

    imported = 0
    skipped = []
    featured_candidates: list[tuple[datetime, str]] = []

    # Read current homepage index once.
    index_read = sa.graphql(
        domain, token, version, sa.READ_QUERY,
        {
            "publication": {"type": sa.PUBLICATION_TYPE, "handle": "__backfill_probe__"},
            "index": {"type": sa.INDEX_TYPE, "handle": sa.INDEX_HANDLE},
        },
    )
    current_index = sa.field_map(index_read.get("index"))
    current_latest = sa.latest_handles_from(current_index)

    def latest_key(entries: list[dict]) -> datetime:
        parsed = [parse_dt(published_at(x)) for x in entries]
        parsed = [x for x in parsed if x]
        return max(parsed) if parsed else datetime.now(timezone.utc)

    for content_id, entries in sorted(grouped.items(), key=lambda kv: latest_key(kv[1])):
        entries = sorted(entries, key=lambda e: parse_dt(published_at(e)) or datetime.min.replace(tzinfo=timezone.utc))
        first_entry = entries[0]
        last_entry = entries[-1]
        slot = slot_of(last_entry)

        item, family = sa.find_item(content_id, slot)
        if not item or not family:
            skipped.append({"content_id": content_id, "reason": "content_not_found_in_banks"})
            continue

        handle = sa.slugify(content_id)

        existing_read = sa.graphql(
            domain, token, version, sa.READ_QUERY,
            {
                "publication": {"type": sa.PUBLICATION_TYPE, "handle": handle},
                "index": {"type": sa.INDEX_TYPE, "handle": sa.INDEX_HANDLE},
            },
        )
        existing = sa.field_map(existing_read.get("publication"))

        first_published = existing.get("first_published_at") or published_at(first_entry)
        last_published = published_at(last_entry)
        historical_count = len(entries)
        existing_count = sa.int_or(existing.get("publication_count"), 0)
        publication_count = max(existing_count, historical_count)

        source, source_url = sa.source_for(item)
        title = sa.title_for(item, family) or sa.trim(content_id, 120)
        excerpt = sa.excerpt_for(item, family) or title
        body = sa.body_for(item, family)

        homepage_featured = sa.as_bool(
            item.get("homepage_featured"),
            family in {"quiz", "natural"},
        )

        fields = {
            "social_id": sa.trim(content_id, 250),
            "title": title,
            "category": sa.category_for(item, family),
            "excerpt": excerpt,
            "body": body,
            "source": source,
            "source_url": source_url,
            "image_url": image_url_of(last_entry, item),
            "related_url": sa.related_url_for(item),
            "instagram_url": instagram_url_of(last_entry),
            "published_at": last_published,
            "first_published_at": first_published,
            "last_published_at": last_published,
            "reuse_after_days": max(0, sa.int_or(item.get("reuse_after_days"), sa.DEFAULT_REUSE_DAYS)),
            "publication_count": publication_count,
            "site_archive": True,
            "seo_title": sa.trim(item.get("seo_title") or title, 120),
            "format": sa.trim(
                "quiz" if family == "quiz" else last_entry.get("format") or family,
                80,
            ),
            "homepage_featured": homepage_featured,
        }

        sa.upsert(
            domain,
            token,
            version,
            type_=sa.PUBLICATION_TYPE,
            handle=handle,
            fields=fields,
            active=True,
        )

        imported += 1
        if homepage_featured:
            featured_candidates.append((latest_key(entries), handle))

        print(f"IMPORTED {content_id} -> {handle}")

    # Homepage: newest featured first, merged without duplicates.
    featured_candidates.sort(key=lambda x: x[0], reverse=True)
    imported_handles = [handle for _, handle in featured_candidates]
    merged = []
    for handle in imported_handles + current_latest:
        if handle and handle not in merged:
            merged.append(handle)
    merged = merged[: sa.MAX_HOME_ITEMS]

    sa.upsert(
        domain,
        token,
        version,
        type_=sa.INDEX_TYPE,
        handle=sa.INDEX_HANDLE,
        fields={
            "name": "Accueil - Le fil du pèlerin",
            "latest_handles": merged,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        active=False,
    )

    print(json.dumps({
        "ok": True,
        "days": args.days,
        "published_events_found": sum(len(v) for v in grouped.values()),
        "unique_content_found": len(grouped),
        "imported": imported,
        "skipped": skipped,
        "homepage_latest": merged,
        "message": "SHOPIFY_BACKFILL_OK",
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
