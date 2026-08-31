from __future__ import annotations

from pathlib import Path
from datetime import datetime, date, timedelta, timezone
import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

from render import render

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
CONFIG = ROOT / "config"
LOGS = ROOT / "logs"
OUTPUT = ROOT / "output"
HISTORY_PATH = LOGS / "publication_history.json"
EXPECTED_IG_USERNAME = os.environ.get("IG_EXPECTED_USERNAME", "labaykanusuk")
AUDIO_ROOT = ROOT / "assets" / "audio" / "approved"
AUDIO_PRIORITY = ("coran", "doua", "adhan", "anachid_sans_musique")
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg"}

SLOTS = {
    1: {"format": "story", "category": "religion"},
    2: {"format": "story", "category": "tool"},
    3: {"format": "story", "category": "religion"},
    4: {"format": "story", "category": "religion"},
    5: {"format": "story", "category": "tool"},
    6: {"format": "story", "category": "religion"},
    7: {"format": "feed", "category": "rotation_religion_or_tool"},
    8: {"format": "reel", "category": "rotation_religion_or_tool"},
}


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def approved_items():
    religion, tools = [], []
    for p in sorted((CONTENT / "religion").glob("*.json")):
        data = load_json(p, [])
        if isinstance(data, list):
            religion.extend(x for x in data if x.get("approved") is True)
    for p in sorted((CONTENT / "tools").glob("*.json")):
        data = load_json(p, [])
        if isinstance(data, list):
            tools.extend(x for x in data if x.get("approved") is True)
    return religion, tools


def history():
    data = load_json(HISTORY_PATH, [])
    return data if isinstance(data, list) else []


def published_entries(hist):
    # Backward compatible with the older history format, which had no status field.
    return [h for h in hist if h.get("status", "published") == "published"]


def item_last_used(item_id: str, hist):
    dates = []
    for h in published_entries(hist):
        if h.get("content_id") == item_id:
            try:
                dates.append(date.fromisoformat(h["date"]))
            except Exception:
                pass
    return max(dates) if dates else None


def choose_item(pool, hist, target_date: date, prevent_days=90):
    if not pool:
        raise RuntimeError("No approved content available for this category")

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

    # When the current validated library is smaller than the requested anti-repeat window,
    # choose the least recently used item instead of stopping the whole machine.
    candidates.sort(key=lambda x: (
        item_last_used(x["id"], hist) or date.min,
        x["id"],
    ))
    return candidates[0]


def choose_photo(item, hist, target_date: date, slot: int):
    manifest = load_json(CONFIG / "photo_manifest.json", {"photos": []})
    photos = [p for p in manifest.get("photos", []) if p.get("approved")]
    if not photos:
        raise RuntimeError("No approved background images in photo_manifest.json")

    themes = set(item.get("theme", []))
    if item.get("type") == "tool":
        themes.add("universal")

    scored = []
    for p in photos:
        score = len(themes.intersection(set(p.get("tags", []))))
        if score:
            scored.append((score, p))
    candidates = [p for _, p in sorted(scored, key=lambda t: (-t[0], t[1]["id"]))]
    if not candidates:
        candidates = [p for p in photos if "universal" in p.get("tags", [])] or photos

    used_today = {
        h.get("photo_id") for h in published_entries(hist)
        if h.get("date") == target_date.isoformat() and h.get("photo_id")
    }
    unused = [p for p in candidates if p.get("id") not in used_today]
    if unused:
        candidates = unused

    seed = f"{target_date.isoformat()}:{slot}:{item['id']}".encode("utf-8")
    idx = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(candidates)
    return candidates[idx]


def make_payload(item, slot_meta, photo):
    common = {
        "background": photo["file"],
        "background_position": photo.get("default_position", "center center"),
    }
    vertical = slot_meta["format"] in {"story", "reel"}

    if item["type"] == "tool":
        template = "story-tool.html" if vertical else "feed-tool.html"
        payload = {
            **common,
            "chip": "OUTIL LABAYKANUSUK",
            "title_before": item["name"],
            "title_highlight": "",
            "copy": " · ".join(item.get("benefits", [])[:3]),
            "quote": "",
            "source": "",
            "cta": item.get("cta", "DÉCOUVRIR"),
        }
        caption = (
            f"{item['name']}\n\n"
            + "\n".join(f"• {b}" for b in item.get("benefits", [])[:3])
            + f"\n\n{item.get('url', 'https://www.labaykanusuk.com')}"
        )
    elif item["type"] == "quran":
        template = "story-religion.html" if vertical else "feed-religion.html"
        payload = {
            **common,
            "kicker": "PAROLE D’ALLAH",
            "title_before": "Le Hajj est un",
            "title_highlight": "devoir pour celui qui en a les moyens.",
            "copy": "La préparation du pèlerinage commence par la connaissance de ce qu’Allah a prescrit.",
            "quote": item.get("french", ""),
            "source": f"{item.get('reference','')} — traduction {item.get('translation','')}",
            "cta": "Prépare ton Hajj",
        }
        caption = f"{item.get('french','')}\n\n{item.get('reference','')} — traduction {item.get('translation','')}"
    else:
        template = "story-religion.html" if vertical else "feed-religion.html"
        payload = {
            **common,
            "kicker": "RAPPEL DU PÈLERIN",
            "title_before": item.get("title_hook", "Rappel du pèlerin"),
            "title_highlight": "",
            "copy": item.get("editorial_copy", ""),
            "quote": "",
            "source": item.get("support_reference", ""),
            "cta": "À méditer",
        }
        caption = f"{item.get('title_hook','')}\n\n{item.get('editorial_copy','')}"

    return template, payload, caption


def choose_reel_audio(target_date: date, item_id: str):
    """Return an approved audio clip using the strict priority, or None for a silent Reel.

    Only files placed in these folders are eligible:
      assets/audio/approved/coran
      assets/audio/approved/doua
      assets/audio/approved/adhan
      assets/audio/approved/anachid_sans_musique

    Nothing outside those folders is ever selected, which enforces the no-music rule.
    """
    for category in AUDIO_PRIORITY:
        folder = AUDIO_ROOT / category
        if not folder.exists():
            continue
        files = sorted(
            p for p in folder.rglob("*")
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
        )
        if not files:
            continue
        seed = f"{target_date.isoformat()}:{item_id}:{category}".encode("utf-8")
        idx = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(files)
        return {"category": category, "path": files[idx]}
    return None


def make_reel(source_image: str, output_name: str, duration=12.0, fps=30, audio=None):
    """Create a cinematic 9:16 Reel. Approved audio is optional; otherwise publish silently."""
    src = Path(source_image)
    if not src.is_absolute():
        src = (ROOT / src).resolve()
    out = OUTPUT / output_name
    out.parent.mkdir(parents=True, exist_ok=True)

    frames = int(duration * fps)
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        f"zoompan=z='min(zoom+0.00010,1.035)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1080x1920:fps={fps},"
        "format=yuv420p"
    )

    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(src)]
    if audio:
        # Audio clips in the approved bank are publication-ready. If shorter than the Reel,
        # pad with silence; if longer, trim at Reel duration. Never source audio elsewhere.
        cmd += ["-i", str(audio["path"])]
        cmd += [
            "-vf", vf,
            "-af", f"apad=pad_dur={duration},atrim=0:{duration},aresample=48000",
        ]
    else:
        # No approved audio available: Reel still publishes, silently, as requested.
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        cmd += ["-vf", vf]

    cmd += [
        "-t", str(duration), "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to generate Reels") from exc
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"ffmpeg Reel generation failed: {err}") from exc
    return out

def already_published_slot(hist, target_date: str, slot: int):
    return any(
        h.get("date") == target_date
        and int(h.get("slot_order", -1)) == slot
        and h.get("status", "published") == "published"
        for h in hist
    )


def prepare(slot: int, state_path: Path, force=False):
    if slot not in SLOTS:
        raise SystemExit("slot must be between 1 and 8")

    target_date = date.today()
    hist = history()
    if already_published_slot(hist, target_date.isoformat(), slot) and not force:
        state = {"skip": True, "reason": "slot_already_published", "date": target_date.isoformat(), "slot": slot}
        save_json(state_path, state)
        print(json.dumps(state, ensure_ascii=False))
        return

    religion, tools = approved_items()
    meta = SLOTS[slot]

    if meta["category"] == "religion":
        pool = religion
    elif meta["category"] == "tool":
        pool = tools
    else:
        # Feed may be religion or tool. With the current validated religion library,
        # the four religious Stories consume the full daily set, so prefer a tool for feed
        # until the source library is expanded.
        religion_used_today = {
            h.get("content_id") for h in published_entries(hist)
            if h.get("date") == target_date.isoformat()
        }
        unused_religion = [r for r in religion if r["id"] not in religion_used_today]
        feed_count = sum(1 for h in published_entries(hist) if h.get("slot_format") == "feed")
        if unused_religion and feed_count % 2 == 0:
            pool = unused_religion
        else:
            pool = tools

    item = choose_item(pool, hist, target_date, prevent_days=90)
    photo = choose_photo(item, hist, target_date, slot)
    template, payload, caption = make_payload(item, meta, photo)

    OUTPUT.mkdir(exist_ok=True)
    payload_path = OUTPUT / f"production-slot-{slot:02d}.json"
    save_json(payload_path, payload)
    reel_audio = None
    if meta["format"] == "reel":
        frame_name = f"production-slot-{slot:02d}-frame.jpg"
        result = render(template, payload_path, frame_name)
        output_name = f"production-slot-{slot:02d}.mp4"
        reel_audio = choose_reel_audio(target_date, item["id"])
        reel_path = make_reel(result["output"], output_name, audio=reel_audio)
        final_output = str(reel_path)
        public_filename = f"slot-{slot:02d}.mp4"
    else:
        output_name = f"production-slot-{slot:02d}.jpg"
        result = render(template, payload_path, output_name)
        final_output = result["output"]
        public_filename = f"slot-{slot:02d}.jpg"

    state = {
        "skip": False,
        "date": target_date.isoformat(),
        "slot": slot,
        "slot_format": meta["format"],
        "slot_category": meta["category"],
        "kind": "STORY" if meta["format"] == "story" else ("REEL" if meta["format"] == "reel" else "IMAGE"),
        "content_id": item["id"],
        "content_type": item["type"],
        "photo_id": photo["id"],
        "template": template,
        "output": final_output,
        "public_filename": public_filename,
        "caption": caption if meta["format"] in {"feed", "reel"} else "",
        "logo_variant": result.get("logo_variant"),
        "density": result.get("density"),
        "audio_category": reel_audio.get("category") if reel_audio else None,
        "audio_file": str(reel_audio["path"].relative_to(ROOT)) if reel_audio else None,
        "audio_mode": "approved" if reel_audio else "silent",
    }
    save_json(state_path, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing environment variable {name}")
    return value


def api_json(url: str, params=None, method="GET"):
    params = params or {}
    if method == "GET":
        full = url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(full, method="GET")
    else:
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Instagram API HTTP {exc.code}: {body}") from exc


def resolve_instagram_account(token: str):
    data = api_json(
        "https://graph.instagram.com/me",
        {"fields": "id,username", "access_token": token},
    )
    ig_id = str(data.get("id", ""))
    username = str(data.get("username", ""))
    if not ig_id or not username:
        raise RuntimeError(f"Could not resolve Instagram account: {data}")
    if EXPECTED_IG_USERNAME and username.lower() != EXPECTED_IG_USERNAME.lower():
        raise RuntimeError(f"Token belongs to @{username}, expected @{EXPECTED_IG_USERNAME}")
    return ig_id, username


def _active_story_ids(base: str, token: str):
    """Return the IDs currently exposed by Instagram as active Stories."""
    data = api_json(
        f"{base}/stories",
        {
            "fields": "id",
            "access_token": token,
        },
    )
    return {
        str(item.get("id", ""))
        for item in data.get("data", [])
        if item.get("id")
    }


def verify_story_publication(base: str, version: str, token: str, media_id: str, attempts: int = 12, delay: int = 5):
    """
    Verify a Story after /media_publish.

    Primary check: the published media ID appears in the account's active
    Stories edge. As a safety fallback, if that edge is unavailable for the
    current Instagram Login/API combination, verify that the returned media ID
    resolves as a real published IG media object instead of breaking every
    Story publication.
    """
    media_id = str(media_id)
    active_edge_responded = False
    direct_media_seen = False
    last_active_error = None
    last_direct_error = None

    for attempt in range(1, attempts + 1):
        # Strong check: Story must appear among active Stories.
        try:
            active_ids = _active_story_ids(base, token)
            active_edge_responded = True
            if media_id in active_ids:
                print(json.dumps({
                    "story_verified": True,
                    "verification": "active_stories",
                    "instagram_media_id": media_id,
                    "attempt": attempt,
                }, ensure_ascii=False))
                return "active_stories"
        except Exception as exc:
            last_active_error = exc

        # Safe fallback: make sure Meta's returned media ID resolves as a
        # published media object. This prevents a temporary/unsupported
        # /stories read edge from stopping all Story publishing.
        try:
            media = api_json(
                f"https://graph.instagram.com/{version}/{media_id}",
                {
                    "fields": "id,media_type,media_product_type,timestamp",
                    "access_token": token,
                },
            )
            if str(media.get("id", "")) == media_id:
                direct_media_seen = True
        except Exception as exc:
            last_direct_error = exc

        if attempt < attempts:
            time.sleep(delay)

    # If the active Stories edge answered successfully but never contained the
    # new ID, treat the publication as unverified. Crucially, record_success()
    # will then NOT mark the slot as published, so the backup run can retry.
    if active_edge_responded:
        raise RuntimeError(
            "Instagram returned a media_id, but the Story did not appear "
            f"in active Stories after {attempts * delay} seconds: {media_id}"
        )

    # The active edge itself was unavailable. Do not break an otherwise valid
    # Story if the published media object is resolvable; log the degraded check.
    if direct_media_seen:
        print(json.dumps({
            "story_verified": True,
            "verification": "direct_media_fallback",
            "instagram_media_id": media_id,
            "warning": str(last_active_error) if last_active_error else "active Stories edge unavailable",
        }, ensure_ascii=False))
        return "direct_media_fallback"

    raise RuntimeError(
        "Instagram Story verification failed. "
        f"active_edge_error={last_active_error}; direct_media_error={last_direct_error}"
    )


def publish_instagram(state, media_url: str):
    token = require_env("IG_ACCESS_TOKEN")
    version = require_env("IG_API_VERSION")
    ig_id, username = resolve_instagram_account(token)
    base = f"https://graph.instagram.com/{version}/{ig_id}"

    params = {"access_token": token}
    if state["kind"] == "STORY":
        params.update({"image_url": media_url, "media_type": "STORIES"})
    elif state["kind"] == "IMAGE":
        params.update({"image_url": media_url, "caption": state.get("caption", "")})
    elif state["kind"] == "REEL":
        params.update({
            "video_url": media_url,
            "media_type": "REELS",
            "caption": state.get("caption", ""),
            "share_to_feed": "true",
        })
    else:
        raise RuntimeError(f"Unsupported production kind: {state['kind']}")

    created = api_json(f"{base}/media", params, method="POST")
    container_id = created.get("id")
    if not container_id:
        raise RuntimeError(f"No container id returned: {created}")

    # Give Meta time to fetch and process the public media.
    poll_attempts = 60 if state["kind"] == "REEL" else 30
    for _ in range(poll_attempts):
        status = api_json(
            f"https://graph.instagram.com/{version}/{container_id}",
            {"fields": "status_code,status", "access_token": token},
        )
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Container processing failed: {status}")
        time.sleep(4)

    # Some image containers are publishable even if status polling is not
    # returned consistently.
    last_error = None
    media_id = None

    for attempt in range(8):
        try:
            published = api_json(
                f"{base}/media_publish",
                {"creation_id": container_id, "access_token": token},
                method="POST",
            )
            media_id = published.get("id")
            if media_id:
                break
        except Exception as exc:
            last_error = exc
            if attempt == 7:
                break
            time.sleep(5)

    if not media_id:
        raise RuntimeError(f"Instagram publication failed: {last_error}")

    # Once Meta has returned a published media ID, do not call media_publish
    # again on the same container. Verify the Story separately.
    story_verification = None
    if state["kind"] == "STORY":
        story_verification = verify_story_publication(
            base=base,
            version=version,
            token=token,
            media_id=str(media_id),
        )

    return {
        "media_id": str(media_id),
        "ig_id": ig_id,
        "username": username,
        "container_id": container_id,
        "story_verification": story_verification,
    }

def record_success(state, media_url: str, published):
    hist = history()
    entry = {
        "date": state["date"],
        "slot_order": state["slot"],
        "slot_format": state["slot_format"],
        "slot_category": state["slot_category"],
        "content_id": state["content_id"],
        "content_type": state["content_type"],
        "photo_id": state.get("photo_id"),
        "kind": state["kind"],
        "status": "published",
        "instagram_media_id": published["media_id"],
        "instagram_username": published["username"],
        "public_url": media_url,
        "audio_category": state.get("audio_category"),
        "audio_file": state.get("audio_file"),
        "audio_mode": state.get("audio_mode"),
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    hist.append(entry)
    save_json(HISTORY_PATH, hist)
    return entry


def publish(state_path: Path, media_url: str, live: bool):
    state = load_json(state_path, {})
    if state.get("skip"):
        print(json.dumps(state, ensure_ascii=False))
        return
    if not live:
        print(json.dumps({"dry_run": True, "state": state, "media_url": media_url}, ensure_ascii=False, indent=2))
        return
    result = publish_instagram(state, media_url)
    entry = record_success(state, media_url, result)
    print(json.dumps({"published": True, "result": result, "history": entry}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--slot", type=int, required=True)
    p_prepare.add_argument("--state", required=True)
    p_prepare.add_argument("--force", action="store_true")

    p_publish = sub.add_parser("publish")
    p_publish.add_argument("--state", required=True)
    p_publish.add_argument("--url", required=True)
    p_publish.add_argument("--live", action="store_true")

    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.slot, Path(args.state), force=args.force)
    else:
        publish(Path(args.state), args.url, live=args.live)


if __name__ == "__main__":
    main()
from __future__ import annotations

from pathlib import Path
from datetime import datetime, date, timedelta, timezone
import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

from render import render

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
CONFIG = ROOT / "config"
LOGS = ROOT / "logs"
OUTPUT = ROOT / "output"
HISTORY_PATH = LOGS / "publication_history.json"
EXPECTED_IG_USERNAME = os.environ.get("IG_EXPECTED_USERNAME", "labaykanusuk")
AUDIO_ROOT = ROOT / "assets" / "audio" / "approved"
AUDIO_PRIORITY = ("coran", "doua", "adhan", "anachid_sans_musique")
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg"}

SLOTS = {
    1: {"format": "story", "category": "religion"},
    2: {"format": "story", "category": "tool"},
    3: {"format": "story", "category": "religion"},
    4: {"format": "story", "category": "religion"},
    5: {"format": "story", "category": "tool"},
    6: {"format": "story", "category": "religion"},
    7: {"format": "feed", "category": "rotation_religion_or_tool"},
    8: {"format": "reel", "category": "rotation_religion_or_tool"},
}


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def approved_items():
    religion, tools = [], []
    for p in sorted((CONTENT / "religion").glob("*.json")):
        data = load_json(p, [])
        if isinstance(data, list):
            religion.extend(x for x in data if x.get("approved") is True)
    for p in sorted((CONTENT / "tools").glob("*.json")):
        data = load_json(p, [])
        if isinstance(data, list):
            tools.extend(x for x in data if x.get("approved") is True)
    return religion, tools


def history():
    data = load_json(HISTORY_PATH, [])
    return data if isinstance(data, list) else []


def published_entries(hist):
    # Backward compatible with the older history format, which had no status field.
    return [h for h in hist if h.get("status", "published") == "published"]


def item_last_used(item_id: str, hist):
    dates = []
    for h in published_entries(hist):
        if h.get("content_id") == item_id:
            try:
                dates.append(date.fromisoformat(h["date"]))
            except Exception:
                pass
    return max(dates) if dates else None


def choose_item(pool, hist, target_date: date, prevent_days=90):
    if not pool:
        raise RuntimeError("No approved content available for this category")

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

    # When the current validated library is smaller than the requested anti-repeat window,
    # choose the least recently used item instead of stopping the whole machine.
    candidates.sort(key=lambda x: (
        item_last_used(x["id"], hist) or date.min,
        x["id"],
    ))
    return candidates[0]


def choose_photo(item, hist, target_date: date, slot: int):
    manifest = load_json(CONFIG / "photo_manifest.json", {"photos": []})
    photos = [p for p in manifest.get("photos", []) if p.get("approved")]
    if not photos:
        raise RuntimeError("No approved background images in photo_manifest.json")

    themes = set(item.get("theme", []))
    if item.get("type") == "tool":
        themes.add("universal")

    scored = []
    for p in photos:
        score = len(themes.intersection(set(p.get("tags", []))))
        if score:
            scored.append((score, p))
    candidates = [p for _, p in sorted(scored, key=lambda t: (-t[0], t[1]["id"]))]
    if not candidates:
        candidates = [p for p in photos if "universal" in p.get("tags", [])] or photos

    used_today = {
        h.get("photo_id") for h in published_entries(hist)
        if h.get("date") == target_date.isoformat() and h.get("photo_id")
    }
    unused = [p for p in candidates if p.get("id") not in used_today]
    if unused:
        candidates = unused

    seed = f"{target_date.isoformat()}:{slot}:{item['id']}".encode("utf-8")
    idx = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(candidates)
    return candidates[idx]


def make_payload(item, slot_meta, photo):
    common = {
        "background": photo["file"],
        "background_position": photo.get("default_position", "center center"),
    }
    vertical = slot_meta["format"] in {"story", "reel"}

    if item["type"] == "tool":
        template = "story-tool.html" if vertical else "feed-tool.html"
        payload = {
            **common,
            "chip": "OUTIL LABAYKANUSUK",
            "title_before": item["name"],
            "title_highlight": "",
            "copy": " · ".join(item.get("benefits", [])[:3]),
            "quote": "",
            "source": "",
            "cta": item.get("cta", "DÉCOUVRIR"),
        }
        caption = (
            f"{item['name']}\n\n"
            + "\n".join(f"• {b}" for b in item.get("benefits", [])[:3])
            + f"\n\n{item.get('url', 'https://www.labaykanusuk.com')}"
        )
    elif item["type"] == "quran":
        template = "story-religion.html" if vertical else "feed-religion.html"
        payload = {
            **common,
            "kicker": "PAROLE D’ALLAH",
            "title_before": "Le Hajj est un",
            "title_highlight": "devoir pour celui qui en a les moyens.",
            "copy": "La préparation du pèlerinage commence par la connaissance de ce qu’Allah a prescrit.",
            "quote": item.get("french", ""),
            "source": f"{item.get('reference','')} — traduction {item.get('translation','')}",
            "cta": "Prépare ton Hajj",
        }
        caption = f"{item.get('french','')}\n\n{item.get('reference','')} — traduction {item.get('translation','')}"
    else:
        template = "story-religion.html" if vertical else "feed-religion.html"
        payload = {
            **common,
            "kicker": "RAPPEL DU PÈLERIN",
            "title_before": item.get("title_hook", "Rappel du pèlerin"),
            "title_highlight": "",
            "copy": item.get("editorial_copy", ""),
            "quote": "",
            "source": item.get("support_reference", ""),
            "cta": "À méditer",
        }
        caption = f"{item.get('title_hook','')}\n\n{item.get('editorial_copy','')}"

    return template, payload, caption


def choose_reel_audio(target_date: date, item_id: str):
    """Return an approved audio clip using the strict priority, or None for a silent Reel.

    Only files placed in these folders are eligible:
      assets/audio/approved/coran
      assets/audio/approved/doua
      assets/audio/approved/adhan
      assets/audio/approved/anachid_sans_musique

    Nothing outside those folders is ever selected, which enforces the no-music rule.
    """
    for category in AUDIO_PRIORITY:
        folder = AUDIO_ROOT / category
        if not folder.exists():
            continue
        files = sorted(
            p for p in folder.rglob("*")
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
        )
        if not files:
            continue
        seed = f"{target_date.isoformat()}:{item_id}:{category}".encode("utf-8")
        idx = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(files)
        return {"category": category, "path": files[idx]}
    return None


def make_reel(source_image: str, output_name: str, duration=12.0, fps=30, audio=None):
    """Create a cinematic 9:16 Reel. Approved audio is optional; otherwise publish silently."""
    src = Path(source_image)
    if not src.is_absolute():
        src = (ROOT / src).resolve()
    out = OUTPUT / output_name
    out.parent.mkdir(parents=True, exist_ok=True)

    frames = int(duration * fps)
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        f"zoompan=z='min(zoom+0.00010,1.035)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s=1080x1920:fps={fps},"
        "format=yuv420p"
    )

    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(src)]
    if audio:
        # Audio clips in the approved bank are publication-ready. If shorter than the Reel,
        # pad with silence; if longer, trim at Reel duration. Never source audio elsewhere.
        cmd += ["-i", str(audio["path"])]
        cmd += [
            "-vf", vf,
            "-af", f"apad=pad_dur={duration},atrim=0:{duration},aresample=48000",
        ]
    else:
        # No approved audio available: Reel still publishes, silently, as requested.
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        cmd += ["-vf", vf]

    cmd += [
        "-t", str(duration), "-r", str(fps),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to generate Reels") from exc
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"ffmpeg Reel generation failed: {err}") from exc
    return out

def already_published_slot(hist, target_date: str, slot: int):
    return any(
        h.get("date") == target_date
        and int(h.get("slot_order", -1)) == slot
        and h.get("status", "published") == "published"
        for h in hist
    )


def prepare(slot: int, state_path: Path, force=False):
    if slot not in SLOTS:
        raise SystemExit("slot must be between 1 and 8")

    target_date = date.today()
    hist = history()
    if already_published_slot(hist, target_date.isoformat(), slot) and not force:
        state = {"skip": True, "reason": "slot_already_published", "date": target_date.isoformat(), "slot": slot}
        save_json(state_path, state)
        print(json.dumps(state, ensure_ascii=False))
        return

    religion, tools = approved_items()
    meta = SLOTS[slot]

    if meta["category"] == "religion":
        pool = religion
    elif meta["category"] == "tool":
        pool = tools
    else:
        # Feed may be religion or tool. With the current validated religion library,
        # the four religious Stories consume the full daily set, so prefer a tool for feed
        # until the source library is expanded.
        religion_used_today = {
            h.get("content_id") for h in published_entries(hist)
            if h.get("date") == target_date.isoformat()
        }
        unused_religion = [r for r in religion if r["id"] not in religion_used_today]
        feed_count = sum(1 for h in published_entries(hist) if h.get("slot_format") == "feed")
        if unused_religion and feed_count % 2 == 0:
            pool = unused_religion
        else:
            pool = tools

    item = choose_item(pool, hist, target_date, prevent_days=90)
    photo = choose_photo(item, hist, target_date, slot)
    template, payload, caption = make_payload(item, meta, photo)

    OUTPUT.mkdir(exist_ok=True)
    payload_path = OUTPUT / f"production-slot-{slot:02d}.json"
    save_json(payload_path, payload)
    reel_audio = None
    if meta["format"] == "reel":
        frame_name = f"production-slot-{slot:02d}-frame.jpg"
        result = render(template, payload_path, frame_name)
        output_name = f"production-slot-{slot:02d}.mp4"
        reel_audio = choose_reel_audio(target_date, item["id"])
        reel_path = make_reel(result["output"], output_name, audio=reel_audio)
        final_output = str(reel_path)
        public_filename = f"slot-{slot:02d}.mp4"
    else:
        output_name = f"production-slot-{slot:02d}.jpg"
        result = render(template, payload_path, output_name)
        final_output = result["output"]
        public_filename = f"slot-{slot:02d}.jpg"

    state = {
        "skip": False,
        "date": target_date.isoformat(),
        "slot": slot,
        "slot_format": meta["format"],
        "slot_category": meta["category"],
        "kind": "STORY" if meta["format"] == "story" else ("REEL" if meta["format"] == "reel" else "IMAGE"),
        "content_id": item["id"],
        "content_type": item["type"],
        "photo_id": photo["id"],
        "template": template,
        "output": final_output,
        "public_filename": public_filename,
        "caption": caption if meta["format"] in {"feed", "reel"} else "",
        "logo_variant": result.get("logo_variant"),
        "density": result.get("density"),
        "audio_category": reel_audio.get("category") if reel_audio else None,
        "audio_file": str(reel_audio["path"].relative_to(ROOT)) if reel_audio else None,
        "audio_mode": "approved" if reel_audio else "silent",
    }
    save_json(state_path, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing environment variable {name}")
    return value


def api_json(url: str, params=None, method="GET"):
    params = params or {}
    if method == "GET":
        full = url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(full, method="GET")
    else:
        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Instagram API HTTP {exc.code}: {body}") from exc


def resolve_instagram_account(token: str):
    data = api_json(
        "https://graph.instagram.com/me",
        {"fields": "id,username", "access_token": token},
    )
    ig_id = str(data.get("id", ""))
    username = str(data.get("username", ""))
    if not ig_id or not username:
        raise RuntimeError(f"Could not resolve Instagram account: {data}")
    if EXPECTED_IG_USERNAME and username.lower() != EXPECTED_IG_USERNAME.lower():
        raise RuntimeError(f"Token belongs to @{username}, expected @{EXPECTED_IG_USERNAME}")
    return ig_id, username


def _active_story_ids(base: str, token: str):
    """Return the IDs currently exposed by Instagram as active Stories."""
    data = api_json(
        f"{base}/stories",
        {
            "fields": "id",
            "access_token": token,
        },
    )
    return {
        str(item.get("id", ""))
        for item in data.get("data", [])
        if item.get("id")
    }


def verify_story_publication(base: str, version: str, token: str, media_id: str, attempts: int = 12, delay: int = 5):
    """
    Verify a Story after /media_publish.

    Primary check: the published media ID appears in the account's active
    Stories edge. As a safety fallback, if that edge is unavailable for the
    current Instagram Login/API combination, verify that the returned media ID
    resolves as a real published IG media object instead of breaking every
    Story publication.
    """
    media_id = str(media_id)
    active_edge_responded = False
    direct_media_seen = False
    last_active_error = None
    last_direct_error = None

    for attempt in range(1, attempts + 1):
        # Strong check: Story must appear among active Stories.
        try:
            active_ids = _active_story_ids(base, token)
            active_edge_responded = True
            if media_id in active_ids:
                print(json.dumps({
                    "story_verified": True,
                    "verification": "active_stories",
                    "instagram_media_id": media_id,
                    "attempt": attempt,
                }, ensure_ascii=False))
                return "active_stories"
        except Exception as exc:
            last_active_error = exc

        # Safe fallback: make sure Meta's returned media ID resolves as a
        # published media object. This prevents a temporary/unsupported
        # /stories read edge from stopping all Story publishing.
        try:
            media = api_json(
                f"https://graph.instagram.com/{version}/{media_id}",
                {
                    "fields": "id,media_type,media_product_type,timestamp",
                    "access_token": token,
                },
            )
            if str(media.get("id", "")) == media_id:
                direct_media_seen = True
        except Exception as exc:
            last_direct_error = exc

        if attempt < attempts:
            time.sleep(delay)

    # If the active Stories edge answered successfully but never contained the
    # new ID, treat the publication as unverified. Crucially, record_success()
    # will then NOT mark the slot as published, so the backup run can retry.
    if active_edge_responded:
        raise RuntimeError(
            "Instagram returned a media_id, but the Story did not appear "
            f"in active Stories after {attempts * delay} seconds: {media_id}"
        )

    # The active edge itself was unavailable. Do not break an otherwise valid
    # Story if the published media object is resolvable; log the degraded check.
    if direct_media_seen:
        print(json.dumps({
            "story_verified": True,
            "verification": "direct_media_fallback",
            "instagram_media_id": media_id,
            "warning": str(last_active_error) if last_active_error else "active Stories edge unavailable",
        }, ensure_ascii=False))
        return "direct_media_fallback"

    raise RuntimeError(
        "Instagram Story verification failed. "
        f"active_edge_error={last_active_error}; direct_media_error={last_direct_error}"
    )


def publish_instagram(state, media_url: str):
    token = require_env("IG_ACCESS_TOKEN")
    version = require_env("IG_API_VERSION")
    ig_id, username = resolve_instagram_account(token)
    base = f"https://graph.instagram.com/{version}/{ig_id}"

    params = {"access_token": token}
    if state["kind"] == "STORY":
        params.update({"image_url": media_url, "media_type": "STORIES"})
    elif state["kind"] == "IMAGE":
        params.update({"image_url": media_url, "caption": state.get("caption", "")})
    elif state["kind"] == "REEL":
        params.update({
            "video_url": media_url,
            "media_type": "REELS",
            "caption": state.get("caption", ""),
            "share_to_feed": "true",
        })
    else:
        raise RuntimeError(f"Unsupported production kind: {state['kind']}")

    created = api_json(f"{base}/media", params, method="POST")
    container_id = created.get("id")
    if not container_id:
        raise RuntimeError(f"No container id returned: {created}")

    # Give Meta time to fetch and process the public media.
    poll_attempts = 60 if state["kind"] == "REEL" else 30
    for _ in range(poll_attempts):
        status = api_json(
            f"https://graph.instagram.com/{version}/{container_id}",
            {"fields": "status_code,status", "access_token": token},
        )
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in {"ERROR", "EXPIRED"}:
            raise RuntimeError(f"Container processing failed: {status}")
        time.sleep(4)

    # Some image containers are publishable even if status polling is not
    # returned consistently.
    last_error = None
    media_id = None

    for attempt in range(8):
        try:
            published = api_json(
                f"{base}/media_publish",
                {"creation_id": container_id, "access_token": token},
                method="POST",
            )
            media_id = published.get("id")
            if media_id:
                break
        except Exception as exc:
            last_error = exc
            if attempt == 7:
                break
            time.sleep(5)

    if not media_id:
        raise RuntimeError(f"Instagram publication failed: {last_error}")

    # Once Meta has returned a published media ID, do not call media_publish
    # again on the same container. Verify the Story separately.
    story_verification = None
    if state["kind"] == "STORY":
        story_verification = verify_story_publication(
            base=base,
            version=version,
            token=token,
            media_id=str(media_id),
        )

    return {
        "media_id": str(media_id),
        "ig_id": ig_id,
        "username": username,
        "container_id": container_id,
        "story_verification": story_verification,
    }

def record_success(state, media_url: str, published):
    hist = history()
    entry = {
        "date": state["date"],
        "slot_order": state["slot"],
        "slot_format": state["slot_format"],
        "slot_category": state["slot_category"],
        "content_id": state["content_id"],
        "content_type": state["content_type"],
        "photo_id": state.get("photo_id"),
        "kind": state["kind"],
        "status": "published",
        "instagram_media_id": published["media_id"],
        "instagram_username": published["username"],
        "public_url": media_url,
        "audio_category": state.get("audio_category"),
        "audio_file": state.get("audio_file"),
        "audio_mode": state.get("audio_mode"),
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    hist.append(entry)
    save_json(HISTORY_PATH, hist)
    return entry


def publish(state_path: Path, media_url: str, live: bool):
    state = load_json(state_path, {})
    if state.get("skip"):
        print(json.dumps(state, ensure_ascii=False))
        return
    if not live:
        print(json.dumps({"dry_run": True, "state": state, "media_url": media_url}, ensure_ascii=False, indent=2))
        return
    result = publish_instagram(state, media_url)
    entry = record_success(state, media_url, result)
    print(json.dumps({"published": True, "result": result, "history": entry}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--slot", type=int, required=True)
    p_prepare.add_argument("--state", required=True)
    p_prepare.add_argument("--force", action="store_true")

    p_publish = sub.add_parser("publish")
    p_publish.add_argument("--state", required=True)
    p_publish.add_argument("--url", required=True)
    p_publish.add_argument("--live", action="store_true")

    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.slot, Path(args.state), force=args.force)
    else:
        publish(Path(args.state), args.url, live=args.live)


if __name__ == "__main__":
    main()
