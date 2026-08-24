from pathlib import Path
import json, hashlib
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
CONFIG = ROOT / "config"
OUTPUT = ROOT / "output"
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

def flatten_items():
    items = []
    for path in (CONTENT / "religion").glob("*.json"):
        data = load_json(path)
        if isinstance(data, list): items.extend(data)
    for path in (CONTENT / "tools").glob("*.json"):
        data = load_json(path)
        if isinstance(data, list): items.extend(data)
    return [x for x in items if x.get("approved") is True]

def load_history():
    p = LOGS / "publication_history.json"
    return load_json(p) if p.exists() else []

def save_history(history):
    (LOGS / "publication_history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

def is_recent(item_id, history, days=90):
    today = date.today()
    for h in history:
        if h.get("content_id") == item_id:
            try:
                d = date.fromisoformat(h["date"])
                if (today - d).days < days: return True
            except Exception: pass
    return False

def choose(pool, history, recent_days=90):
    clean = [x for x in pool if not is_recent(x["id"], history, recent_days)]
    if not clean: clean = pool[:]
    clean.sort(key=lambda x: x["id"])
    return clean[0] if clean else None

def choose_photo(item, slot_order=1):
    photos = [p for p in load_json(CONFIG / "photo_manifest.json")["photos"] if p.get("approved")]
    themes = set(item.get("theme", []))
    if item.get("type") == "tool": themes.add("universal")
    scored = []
    for p in photos:
        score = len(themes.intersection(set(p.get("tags", []))))
        if score:
            scored.append((score, p))
    candidates = [p for _,p in sorted(scored, key=lambda t:(-t[0],t[1]["id"]))]
    if not candidates:
        candidates = [p for p in photos if "universal" in p.get("tags", [])] or photos
    seed = f"{item['id']}:{slot_order}".encode()
    idx = int(hashlib.sha256(seed).hexdigest()[:8], 16) % len(candidates)
    return candidates[idx]

def make_render_payload(item, slot):
    photo = choose_photo(item, slot["order"])
    common = {
        "background": photo["file"],
        "background_position": photo.get("default_position", "center center")
    }
    if item["type"] == "tool":
        template = "story-tool.html" if slot["format"] == "story" else "feed-tool.html"
        payload = {
            **common,
            "chip": "OUTIL LABAYKANUSUK",
            "title_before": item["name"],
            "title_highlight": "",
            "copy": " · ".join(item.get("benefits", [])[:3]),
            "quote": "",
            "source": "",
            "cta": item.get("cta", "DÉCOUVRIR")
        }
    else:
        template = "story-religion.html" if slot["format"] == "story" else "feed-religion.html"
        if item["type"] == "quran":
            payload = {
                **common,
                "kicker": "PAROLE D’ALLAH",
                "title_before": "Le Hajj est un",
                "title_highlight": "devoir pour celui qui en a les moyens.",
                "copy": "La préparation du pèlerinage commence par la connaissance de ce qu’Allah a prescrit.",
                "quote": item["french"],
                "source": f"{item['reference']} — traduction {item['translation']}",
                "cta": "Prépare ton Hajj"
            }
        elif item["type"] == "hadith":
            payload = {
                **common,
                "kicker": "HADITH AUTHENTIQUE",
                "title_before": item.get("title_hook", "Un rappel pour le pèlerin"),
                "title_highlight": "",
                "copy": item.get("editorial_copy", ""),
                "quote": item.get("french", ""),
                "source": item.get("reference", ""),
                "cta": "À retenir"
            }
        else:
            payload = {
                **common,
                "kicker": "RAPPEL DU PÈLERIN",
                "title_before": item.get("title_hook", "Rappel"),
                "title_highlight": "",
                "copy": item.get("editorial_copy", ""),
                "quote": "",
                "source": item.get("support_reference", ""),
                "cta": "Médite ce rappel"
            }
    return {"content_id": item["id"], "template": template, "payload": payload}

def build_day_queue(target_date=None):
    target_date = target_date or date.today().isoformat()
    schedule = load_json(CONFIG / "publishing.json")
    history = load_history()
    items = flatten_items()
    religion_pool = [x for x in items if x["type"] in {"quran", "hadith", "editorial_religion"}]
    tool_pool = [x for x in items if x["type"] == "tool"]
    queue = []
    feed_toggle = sum(1 for h in history if h.get("slot_format") == "feed") % 2
    for slot in schedule["slots"]:
        pool = religion_pool if slot["category"] == "religion" else tool_pool if slot["category"] == "tool" else (religion_pool if feed_toggle == 0 else tool_pool)
        chosen = choose(pool, history, schedule["safety"]["prevent_duplicate_days"])
        if not chosen: raise RuntimeError(f"No approved content available for slot {slot['order']}")
        render = make_render_payload(chosen, slot)
        queue.append({"date":target_date,"slot_order":slot["order"],"slot_format":slot["format"],"slot_category":slot["category"],"content_id":render["content_id"],"template":render["template"],"payload":render["payload"]})
        history.append({"date":target_date,"slot_order":slot["order"],"slot_format":slot["format"],"content_id":render["content_id"]})
    save_history(history)
    out = OUTPUT / f"queue_{target_date}.json"
    out.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    return out

if __name__ == "__main__": print(build_day_queue())
