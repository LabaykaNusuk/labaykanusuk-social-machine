from __future__ import annotations

from pathlib import Path
from datetime import date
import argparse
import json

from production_runner import (
    OUTPUT,
    approved_items,
    history,
    choose_photo,
    make_payload,
    choose_reel_audio,
    make_reel,
    save_json,
)
from render import render

YT_SLOTS = {
    1: {"category": "religion", "ordinal": 0, "label": "16:00"},
    2: {"category": "tool", "ordinal": 0, "label": "20:00"},
    3: {"category": "religion", "ordinal": 1, "label": "21:00"},
    4: {"category": "tool", "ordinal": 1, "label": "22:00"},
}


def choose_youtube_item(pool, target_date: date, ordinal: int):
    """Rotate deterministically through approved content.

    Two items are consumed per category each day. This avoids depending on
    ephemeral GitHub runner state and guarantees that the two daily slots in
    the same category are different whenever at least two approved items exist.
    """
    items = sorted(pool, key=lambda item: item["id"])
    if len(items) < 2:
        raise RuntimeError(
            "YouTube production needs at least 2 approved items in each category."
        )

    start = (target_date.toordinal() * 2) % len(items)
    return items[(start + ordinal) % len(items)]


def make_title(item):
    if item.get("type") == "tool":
        base = item.get("name", "Outil du pèlerin")
    elif item.get("type") == "quran":
        base = item.get("reference", "Rappel coranique")
    else:
        base = item.get("title_hook", "Rappel du pèlerin")

    return f"{base} | LABAYKANUSUK #Shorts"[:95]


def prepare(slot: int, state_path: Path):
    if slot not in YT_SLOTS:
        raise SystemExit("YouTube slot must be between 1 and 4")

    target_date = date.today()
    instagram_hist = history()
    religion, tools = approved_items()
    meta = YT_SLOTS[slot]

    pool = religion if meta["category"] == "religion" else tools
    item = choose_youtube_item(pool, target_date, meta["ordinal"])

    # Reuse the existing approved photo engine while keeping YouTube slots
    # separate from the Instagram slot numbers.
    photo = choose_photo(item, instagram_hist, target_date, 100 + slot)

    reel_meta = {"format": "reel", "category": meta["category"]}
    template, payload, caption = make_payload(item, reel_meta, photo)

    OUTPUT.mkdir(exist_ok=True)
    payload_path = OUTPUT / f"youtube-slot-{slot:02d}.json"
    save_json(payload_path, payload)

    frame_name = f"youtube-slot-{slot:02d}-frame.jpg"
    result = render(template, payload_path, frame_name)

    # Uses only the approved audio bank. If no approved audio exists,
    # production_runner generates the Short silently.
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
        "tags": ["Hajj", "Omra", "LABAYKANUSUK", "Pèlerinage", "Shorts"],
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
    prepare(args.slot, Path(args.state))


if __name__ == "__main__":
    main()
