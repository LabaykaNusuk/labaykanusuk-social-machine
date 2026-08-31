#!/usr/bin/env python3
"""
LABAYKANUSUK — Social Machine → Shopify archive bridge.

Production behavior:
- Client Credentials authentication (Shopify Dev Dashboard, 2026).
- Archives only after the workflow calls this script following a successful LIVE publication.
- Every successful LIVE publication is archive-eligible: Story, Feed, Reel, Quiz, Feed naturel.
- Upserts by stable content_id-derived handle: no duplicate Shopify entry for a republication.
- Maintains fil_pelerin_index/main.latest_handles (max 6).
- Preserves first publication date and increments publication_count only for a new publication event.
- Never prints Client Secret or access token.

Environment:
  SHOPIFY_SHOP
  SHOPIFY_CLIENT_ID
  SHOPIFY_CLIENT_SECRET
  SHOPIFY_API_VERSION  (optional, default 2026-07)

Commands:
  python src/shopify_archive.py health-check

  python src/shopify_archive.py \
    --state /tmp/production-state.json \
    --image-url "https://..." \
    --slot 10

Also accepted:
  python src/shopify_archive.py archive --state ... --image-url ... --slot ...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = ROOT / "content"
HISTORY = ROOT / "logs" / "publication_history.json"

PUBLICATION_TYPE = "publication_labaykanusuk"
INDEX_TYPE = "fil_pelerin_index"
INDEX_HANDLE = "main"

DEFAULT_API_VERSION = "2026-07"
DEFAULT_REUSE_DAYS = 120
MAX_HOME_ITEMS = 6
DEFAULT_PUBLIC_SITE = "https://www.labaykanusuk.com"


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_multiline(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    result = []
    blank = False
    for line in lines:
        if line:
            result.append(line)
            blank = False
        elif result and not blank:
            result.append("")
            blank = True
    return "\n".join(result).strip()


def sentence(*parts: Any) -> str:
    return clean(" ".join(clean(p) for p in parts if clean(p)))


def trim(value: Any, limit: int) -> str:
    value = clean(value)
    if len(value) <= limit:
        return value
    cut = value[: max(1, limit - 1)].rsplit(" ", 1)[0]
    return (cut or value[: limit - 1]).rstrip(" ,;:") + "…"


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "oui", "on"}


def int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value[:240] or "publication"


def require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def normalize_shop(value: str) -> tuple[str, str]:
    value = value.strip()
    value = value.removeprefix("https://").removeprefix("http://").strip("/")
    if value.endswith(".myshopify.com"):
        shop = value[: -len(".myshopify.com")]
    else:
        shop = value
    if not shop:
        raise RuntimeError("SHOPIFY_SHOP is empty after normalization")
    return shop, f"{shop}.myshopify.com"


def normalize_http_url(value: Any) -> str:
    value = clean(value)
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return ""
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    return ""


def normalize_related_url(value: Any) -> str:
    value = clean(value)
    if not value:
        return ""
    if value.startswith("/"):
        base = (os.environ.get("SHOPIFY_PUBLIC_BASE_URL") or DEFAULT_PUBLIC_SITE).rstrip("/")
        return normalize_http_url(base + value)
    return normalize_http_url(value)


def request_json(req: urllib.request.Request, timeout: int = 30) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Shopify HTTP {exc.code}: {body[:1500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Shopify connection error: {exc}") from exc


def get_access_token(shop: str, client_id: str, client_secret: str) -> str:
    url = f"https://{shop}.myshopify.com/admin/oauth/access_token"
    payload = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "LABAYKANUSUK-Social-Archive/2.0",
        },
    )
    data = request_json(req)
    token = data.get("access_token")
    if not token:
        raise RuntimeError("Shopify did not return an access_token")
    return str(token)


def graphql(domain: str, token: str, version: str, query: str, variables: dict | None = None) -> dict:
    url = f"https://{domain}/admin/api/{version}/graphql.json"
    payload = json.dumps(
        {"query": query, "variables": variables or {}},
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Shopify-Access-Token": token,
            "User-Agent": "LABAYKANUSUK-Social-Archive/2.0",
        },
    )
    data = request_json(req)
    if data.get("errors"):
        raise RuntimeError(
            "Shopify GraphQL errors: "
            + json.dumps(data["errors"], ensure_ascii=False)
        )
    return data.get("data") or {}


HEALTH_QUERY = r"""
query ArchiveHealthCheck {
  shop { name myshopifyDomain }
  currentAppInstallation {
    app { title }
    accessScopes { handle }
  }
  index: metaobjectByHandle(
    handle: {type: "fil_pelerin_index", handle: "main"}
  ) {
    id
    handle
    type
  }
}
"""

READ_QUERY = r"""
query ReadArchiveMetaobjects(
  $publication: MetaobjectHandleInput!,
  $index: MetaobjectHandleInput!
) {
  publication: metaobjectByHandle(handle: $publication) {
    id
    handle
    fields { key value }
  }
  index: metaobjectByHandle(handle: $index) {
    id
    handle
    fields { key value }
  }
}
"""

UPSERT_MUTATION = r"""
mutation UpsertArchivePublication(
  $handle: MetaobjectHandleInput!,
  $metaobject: MetaobjectUpsertInput!
) {
  metaobjectUpsert(handle: $handle, metaobject: $metaobject) {
    metaobject {
      id
      handle
      type
      fields { key value }
    }
    userErrors { field message code }
  }
}
"""


def field_map(metaobject: dict | None) -> dict[str, str]:
    if not metaobject:
        return {}
    return {
        str(field.get("key")): str(field.get("value") or "")
        for field in metaobject.get("fields", [])
        if isinstance(field, dict) and field.get("key")
    }


def serialize_field(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def upsert(
    domain: str,
    token: str,
    version: str,
    *,
    type_: str,
    handle: str,
    fields: dict[str, Any],
    active: bool = False,
) -> dict:
    serialized_fields = []
    for key, value in fields.items():
        if value is None or value == "":
            continue
        serialized_fields.append(
            {"key": key, "value": serialize_field(value)}
        )

    metaobject: dict[str, Any] = {"fields": serialized_fields}
    if active:
        metaobject["capabilities"] = {
            "publishable": {"status": "ACTIVE"}
        }

    data = graphql(
        domain,
        token,
        version,
        UPSERT_MUTATION,
        {
            "handle": {"type": type_, "handle": handle},
            "metaobject": metaobject,
        },
    )
    result = data.get("metaobjectUpsert") or {}
    errors = result.get("userErrors") or []
    if errors:
        raise RuntimeError(
            "Shopify metaobjectUpsert userErrors: "
            + json.dumps(errors, ensure_ascii=False)
        )
    saved = result.get("metaobject")
    if not saved:
        raise RuntimeError(f"Shopify metaobjectUpsert returned no metaobject for {type_}/{handle}")
    return saved


def recursive_records(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        if value.get("id") is not None:
            yield value
        for child in value.values():
            yield from recursive_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from recursive_records(child)


def family_from(path: Path, item: dict, slot: int | None = None) -> str:
    if slot == 9:
        return "quiz"
    if slot == 10:
        return "natural"

    lower_path = "/".join(part.lower() for part in path.parts)
    if "/natural/" in f"/{lower_path}/":
        return "natural"
    if "/quiz/" in f"/{lower_path}/":
        return "quiz"
    if "/tools/" in f"/{lower_path}/" or "/tool/" in f"/{lower_path}/":
        return "tools"
    if "/religion/" in f"/{lower_path}/":
        return "religion"

    kind = clean(item.get("type")).lower()
    category = clean(item.get("category") or item.get("slot_category")).lower()
    if "quiz" in kind or "quiz" in category:
        return "quiz"
    if "natural" in kind or "natural" in category:
        return "natural"
    if "tool" in kind or "outil" in kind or "tool" in category or "outil" in category:
        return "tools"
    if "relig" in kind or "relig" in category:
        return "religion"
    return "social"


def candidate_json_files() -> Iterable[Path]:
    if not CONTENT_ROOT.exists():
        return

    seen: set[Path] = set()
    preferred = ["natural", "quiz", "religion", "tools"]
    for family in preferred:
        folder = CONTENT_ROOT / family
        if folder.exists():
            for path in sorted(folder.rglob("*.json")):
                if path not in seen:
                    seen.add(path)
                    yield path

    for path in sorted(CONTENT_ROOT.rglob("*.json")):
        if path not in seen:
            seen.add(path)
            yield path


def find_item(content_id: str, slot: int | None = None) -> tuple[dict | None, str | None]:
    wanted = str(content_id)
    for path in candidate_json_files() or []:
        try:
            data = load_json(path, None)
        except Exception:
            continue
        for item in recursive_records(data):
            if str(item.get("id")) == wanted:
                return item, family_from(path, item, slot)
    return None, None


def latest_history_entry(content_id: str) -> dict:
    history = load_json(HISTORY, [])
    if not isinstance(history, list):
        return {}
    matches = [
        entry
        for entry in history
        if isinstance(entry, dict)
        and entry.get("status") == "published"
        and str(entry.get("content_id")) == str(content_id)
    ]
    return matches[-1] if matches else {}


def is_archive_eligible(item: dict, family: str, slot: int) -> bool:
    # Continuous archive rule: every successful LIVE publication is preserved.
    return True


def category_for(item: dict, family: str) -> str:
    explicit = clean(
        item.get("site_category")
        or item.get("category_label")
        or item.get("category")
        or item.get("section")
    )
    if explicit:
        return trim(explicit, 120)
    return {
        "natural": "Hajj & Omra",
        "quiz": "Quiz",
        "religion": "Hajj & Omra",
        "tools": "Outils",
    }.get(family, "Hajj & Omra")


def title_for(item: dict, family: str) -> str:
    explicit = clean(
        item.get("seo_title")
        or item.get("site_title")
        or item.get("title")
    )
    if explicit:
        return trim(explicit, 120)

    if family == "quiz":
        return trim(
            sentence(
                item.get("question_before"),
                item.get("question_highlight"),
                item.get("question_after"),
            ),
            120,
        )

    if family == "natural":
        return trim(
            sentence(
                item.get("quote_before"),
                item.get("quote_highlight"),
                item.get("quote_after"),
            ),
            120,
        )

    fallback = (
        item.get("title_hook")
        or item.get("hook")
        or item.get("editorial_copy")
        or item.get("caption_hook")
        or item.get("id")
    )
    return trim(fallback, 120)


def excerpt_for(item: dict, family: str) -> str:
    explicit = clean(item.get("site_excerpt") or item.get("excerpt"))
    if explicit:
        return trim(explicit, 420)

    if family == "quiz":
        return trim(
            item.get("answer_text")
            or item.get("hook")
            or item.get("caption_hook"),
            420,
        )

    if family == "natural":
        return trim(
            item.get("caption")
            or sentence(
                item.get("quote_before"),
                item.get("quote_highlight"),
                item.get("quote_after"),
            ),
            420,
        )

    return trim(
        item.get("editorial_copy")
        or item.get("caption")
        or item.get("body")
        or item.get("hook")
        or item.get("id"),
        420,
    )


def body_for(item: dict, family: str) -> str:
    explicit = clean_multiline(item.get("site_body"))
    if explicit:
        return explicit

    if family == "quiz":
        question = sentence(
            item.get("question_before"),
            item.get("question_highlight"),
            item.get("question_after"),
        )
        answer = clean_multiline(item.get("answer_text"))
        extra = clean_multiline(item.get("source"))
        parts = [part for part in (question, answer, extra) if part]
        return "\n\n".join(parts)

    return clean_multiline(
        item.get("caption")
        or item.get("editorial_copy")
        or item.get("body")
        or ""
    )


def source_for(item: dict) -> tuple[str, str]:
    source = clean(
        item.get("source_short")
        or item.get("source_reference")
        or item.get("support_reference")
        or item.get("source")
    )
    return trim(source, 250), normalize_http_url(item.get("source_url"))


def related_url_for(item: dict) -> str:
    return normalize_related_url(
        item.get("related_url")
        or item.get("site_related_url")
        or item.get("cta_url")
    )


def event_published_at(history: dict) -> str:
    return clean(
        history.get("published_at_utc")
        or history.get("published_at")
        or history.get("timestamp")
    ) or datetime.now(timezone.utc).isoformat()


def next_publication_count(existing: dict[str, str], published_at: str) -> int:
    previous = int_or(existing.get("publication_count"), 0)
    if previous > 0 and clean(existing.get("last_published_at")) == clean(published_at):
        return previous
    return max(1, previous + 1)


def latest_handles_from(index: dict[str, str]) -> list[str]:
    raw = index.get("latest_handles") or "[]"
    try:
        values = json.loads(raw)
    except Exception:
        values = []
    if not isinstance(values, list):
        return []
    return [clean(value) for value in values if clean(value)]


def shopify_credentials() -> tuple[str, str, str, str, str]:
    shop_value = require_env("SHOPIFY_SHOP")
    client_id = require_env("SHOPIFY_CLIENT_ID")
    client_secret = require_env("SHOPIFY_CLIENT_SECRET")
    version = (os.environ.get("SHOPIFY_API_VERSION") or DEFAULT_API_VERSION).strip()
    shop, domain = normalize_shop(shop_value)
    token = get_access_token(shop, client_id, client_secret)
    return shop, domain, token, version, client_id


def health_check() -> int:
    _, domain, token, version, _ = shopify_credentials()
    data = graphql(domain, token, version, HEALTH_QUERY)

    installation = data.get("currentAppInstallation") or {}
    scopes = sorted(
        scope.get("handle")
        for scope in installation.get("accessScopes") or []
        if isinstance(scope, dict) and scope.get("handle")
    )

    required = {"read_metaobjects", "write_metaobjects"}
    missing = sorted(required.difference(scopes))
    if missing:
        raise RuntimeError(
            "Required Shopify scopes missing: " + ", ".join(missing)
        )

    index = data.get("index")
    if not index or index.get("handle") != INDEX_HANDLE:
        raise RuntimeError("fil_pelerin_index/main is not visible to this app")

    result = {
        "ok": True,
        "shop": (data.get("shop") or {}).get("name"),
        "app": (installation.get("app") or {}).get("title"),
        "api_version": version,
        "required_scopes_ok": True,
        "index_main_found": True,
        "message": "SHOPIFY SOCIAL ARCHIVE CONNECTION OK",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def archive(state_path: Path, image_url: str, slot: int) -> int:
    _, domain, token, version, _ = shopify_credentials()

    state = load_json(state_path, {})
    if not isinstance(state, dict):
        raise RuntimeError(f"Invalid social state JSON: {state_path}")

    if state.get("skip"):
        print("SHOPIFY_ARCHIVE_SKIPPED: social state is skip")
        return 0

    nested_item = state.get("item") if isinstance(state.get("item"), dict) else {}
    content_id = clean(state.get("content_id") or nested_item.get("id"))
    if not content_id:
        raise RuntimeError("Missing content_id in social state")

    item, family = find_item(content_id, slot)
    if not item or not family:
        print(f"SHOPIFY_ARCHIVE_SKIPPED: content not found in repository banks: {content_id}")
        return 0

    if not is_archive_eligible(item, family, slot):
        print(f"SHOPIFY_ARCHIVE_SKIPPED: site_archive not enabled for {content_id}")
        return 0

    handle = slugify(content_id)

    read = graphql(
        domain,
        token,
        version,
        READ_QUERY,
        {
            "publication": {"type": PUBLICATION_TYPE, "handle": handle},
            "index": {"type": INDEX_TYPE, "handle": INDEX_HANDLE},
        },
    )
    existing = field_map(read.get("publication"))
    index = field_map(read.get("index"))

    history = latest_history_entry(content_id)
    published_at = event_published_at(history)
    first_published_at = existing.get("first_published_at") or published_at
    publication_count = next_publication_count(existing, published_at)

    source, source_url = source_for(item)
    title = title_for(item, family) or trim(content_id, 120)
    excerpt = excerpt_for(item, family) or title
    body = body_for(item, family)

    homepage_featured = as_bool(item.get("homepage_featured"), family in {"quiz", "natural"})
    reuse_after_days = max(
        0,
        int_or(item.get("reuse_after_days"), DEFAULT_REUSE_DAYS),
    )

    instagram_url = normalize_http_url(
        history.get("instagram_url")
        or history.get("instagram_permalink")
        or history.get("permalink")
    )

    publication_fields: dict[str, Any] = {
        "social_id": trim(content_id, 250),
        "title": title,
        "category": category_for(item, family),
        "excerpt": excerpt,
        "body": body,
        "source": source,
        "source_url": source_url,
        "image_url": normalize_http_url(image_url),
        "related_url": related_url_for(item),
        "instagram_url": instagram_url,
        "published_at": published_at,
        "first_published_at": first_published_at,
        "last_published_at": published_at,
        "reuse_after_days": reuse_after_days,
        "publication_count": publication_count,
        "site_archive": True,
        "seo_title": trim(item.get("seo_title") or title, 120),
        "format": trim(
            "quiz"
            if family == "quiz"
            else state.get("slot_format")
            or state.get("format")
            or state.get("kind")
            or family,
            80,
        ),
        "homepage_featured": homepage_featured,
    }

    saved = upsert(
        domain,
        token,
        version,
        type_=PUBLICATION_TYPE,
        handle=handle,
        fields=publication_fields,
        active=True,
    )

    latest = latest_handles_from(index)
    latest = [value for value in latest if value != handle]
    if homepage_featured:
        latest.insert(0, handle)
    latest = latest[:MAX_HOME_ITEMS]

    upsert(
        domain,
        token,
        version,
        type_=INDEX_TYPE,
        handle=INDEX_HANDLE,
        fields={
            "name": "Accueil - Le fil du pèlerin",
            "latest_handles": latest,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        active=False,
    )

    print(
        json.dumps(
            {
                "shopify_archive": True,
                "content_id": content_id,
                "family": family,
                "handle": handle,
                "metaobject_id": saved.get("id"),
                "publication_count": publication_count,
                "published_at": published_at,
                "homepage_featured": homepage_featured,
                "homepage_latest": latest,
                "message": "SHOPIFY_ARCHIVE_OK",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def parse_archive_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive a successful LABAYKANUSUK social publication to Shopify"
    )
    parser.add_argument("--state", required=True)
    parser.add_argument("--image-url", required=True)
    parser.add_argument("--slot", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "health-check":
        if len(argv) != 1:
            raise SystemExit("health-check takes no arguments")
        return health_check()

    if argv and argv[0] == "archive":
        argv = argv[1:]

    args = parse_archive_args(argv)
    return archive(Path(args.state), args.image_url, args.slot)


if __name__ == "__main__":
    raise SystemExit(main())
