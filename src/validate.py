from pathlib import Path
import json, sys

ROOT = Path(__file__).resolve().parents[1]
errors = []

def load(path):
    return json.loads(path.read_text(encoding="utf-8"))

sources = {s["id"] for s in load(ROOT/"config/sources.json")["sources"]}

for path in (ROOT/"content").rglob("*.json"):
    data = load(path)
    items = data if isinstance(data, list) else [data]
    for item in items:
        if item.get("source_id") and item["source_id"] not in sources:
            errors.append(f"{path}: unknown source_id {item['source_id']}")
        if item.get("approved") is True and not item.get("source_id"):
            errors.append(f"{path}: approved item without source_id: {item.get('id')}")

if errors:
    print("\\n".join(errors))
    sys.exit(1)
print("Content validation OK")
