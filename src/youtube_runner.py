from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta
import argparse
import json

from production_runner import (
    ROOT, OUTPUT, CONFIG,
    approved_items, history, published_entries, item_last_used,
    choose_photo, make_payload, choose_reel_audio, make_reel, save_json
)
from render import render

YT_SLOTS = {
    1: {"category": "religion", "ordinal": 0, "label": "16:00"},
    2: {"category": "tool", "ordinal": 0, "label": "20:00"},
    3: {"category": "religion", "ordinal": 1, "label": "21:00"},
    4: {"category": "tool", "ordinal": 1, "label": "22:00"},
}

def choose_youtube_item(pool, hist, target_date: date, ordinal: int, prevent_days: int = 90):
    if not pool:
        raise RuntimeError("No approved content available for this YouTube category")

    used_today = {
        h.get("content_id") for h in published_entries(hist)
        if h.get("date") == target_date.isoformat()
    }
    candidates = [x for x in pool if x.get("id") not in used_today]
    if not candidates:
        candidates = pool[:]

    cutoff = target_date - timedelta(days=prevent_days)
    fresh = []
    for item in candidates:
        last = item_last_used(item["id"], hist)
        if last is None or last < cutoff:
            fresh.append(item)
    if fresh:
        candidates = fresh

    candidates.sort(key=lambda x: (
        item_last_used(x["id"], hist) or date.min,
        x["id"],
    ))

    if len(candidates) <= ordinal:
        raise RuntimeError(
            f"Not enough distinct approved content items for YouTube slot ordinal {ordinal}. "
            f"Need at least {ordinal + 1}, found {len(candidates)}."
        )
    return candidates[ordinal]

def make_title(item):
    if item.get("type") == "tool":
        base = item.get("name", "Outil du pèlerin")
    elif item.get("type") == "quran":
        base = item.get("reference", "Rappel Coranique")
    else:
        base = item.get("title_hook", "Rappel du pèlerin")
    title = f"{base} | LABAYKANUSUK #Shorts"
    return title[:95]

def prepare(slot: int, state_path: Path):
    if slot not in YT_SLOTS:
        raise SystemExit("YouTube slot must be between 1 and 4")

    target_date = date.today()
    hist = history()
    religion, tools = approved_items()
    meta = YT_SLOTS[slot]

    pool = religion if meta["category"] == "religion" else tools
    item = choose_youtube_item(pool, hist, target_date, meta["ordinal"], prevent_days=90)

    synthetic_slot = 100 + slot
    photo = choose_photo(item, hist, target_date, synthetic_slot)

    reel_meta = {"format": "reel", "category": meta["category"]}
    template, payload, caption = make_payload(item, reel_meta, photo)

    OUTPUT.mkdir(exist_ok=True)
    payload_path = OUTPUT / f"youtube-slot-{slot:02d}.json"
    save_json(payload_path, payload)

    frame_name = f"youtube-slot-{slot:02d}-frame.jpg"
    result = render(template, payload_path, frame_name)

    audio = choose_reel_audio(target_date, item["id"])
    video_name = f"youtube-slot-{slot:02d}.mp4"
    video_path = make_reel(result["output"], video_name, audio=audio)

    state = {
        "date": target_date.isoformat(),
        "youtube_slot": slot,
        "scheduled_label": meta["label"],
        "category": meta["category"],
        "content_id": item["id"],
        "content_type": item["type"],
        "photo_id": photo["id"],
        "title": make_title(item),
        "description": (
            f"{caption}\n\n"
            "LABAYKANUSUK — Hajj & Omra\n"
            "https://www.labaykanusuk.com\n\n"
            "#Hajj #Omra #Islam #Shorts"
        ),
        "output": str(video_path),
        "audio_category": audio.get("category") if audio else None,
        "audio_mode": "approved" if audio else "silent",
    }
    save_json(state_path, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--slot", type=int, required=True)
    p.add_argument("--state", required=True)
    args = parser.parse_args()

    if args.command == "prepare":
        prepare(args.slot, Path(args.state))

if __name__ == "__main__":
    main()
