from __future__ import annotations

from pathlib import Path
from datetime import date, datetime, timezone, timedelta
import argparse
import base64
import hashlib
import html
import json
import mimetypes
import os
import time

from playwright.sync_api import sync_playwright

from production_runner import (
    ROOT,
    CONFIG,
    LOGS,
    OUTPUT,
    HISTORY_PATH,
    load_json,
    save_json,
    history,
    published_entries,
    choose_photo,
    require_env,
    api_json,
    resolve_instagram_account,
)

QUIZ_DIR = ROOT / "content" / "quiz"
TEMPLATE_DIR = ROOT / "templates"
BRAND_LOGO = ROOT / "assets" / "branding" / "labaykanusuk-quiz-logo.png"
QUIZ_SLOT = 9
COOLDOWN_DAYS = 90


def approved_quizzes():
    items = []

    for path in sorted(QUIZ_DIR.glob("*.json")):
        data = load_json(path, [])

        if isinstance(data, list):
            for item in data:
                if (
                    item.get("approved") is True
                    and item.get("review_status") == "approved"
                ):
                    items.append(item)

    return items


def quiz_last_used(quiz_id: str, hist):
    dates = []

    for entry in published_entries(hist):
        if entry.get("content_type") != "quiz":
            continue

        if entry.get("content_id") != quiz_id:
            continue

        try:
            dates.append(date.fromisoformat(entry["date"]))
        except Exception:
            pass

    return max(dates) if dates else None


def choose_quiz(pool, hist, target_date: date, force: bool = False):
    """
    Sélectionne le quiz à publier.

    MODE NORMAL / force=False
    -------------------------
    - interdit de republier un quiz déjà utilisé aujourd'hui ;
    - respecte le cooldown de 90 jours ;
    - si aucun quiz frais n'existe, ne publie rien.

    MODE FORCE / force=True
    -----------------------
    - réservé aux lancements manuels de test ;
    - autorise un quiz déjà publié aujourd'hui ;
    - contourne le cooldown de 90 jours ;
    - permet donc de retester réellement le carrousel LIVE.
    """

    if not pool:
        return None

    used_today = {
        e.get("content_id")
        for e in published_entries(hist)
        if (
            e.get("date") == target_date.isoformat()
            and e.get("content_type") == "quiz"
        )
    }

    # ---------------------------------------------------------
    # FORCE MANUEL
    # ---------------------------------------------------------
    # En mode force, tous les quiz approuvés sont candidats,
    # même s'ils ont déjà été utilisés aujourd'hui ou pendant
    # les 90 derniers jours.
    # ---------------------------------------------------------
    if force:
        candidates = pool[:]

        candidates.sort(
            key=lambda q: (
                quiz_last_used(q["id"], hist) or date.min,
                q["id"],
            )
        )

        return candidates[0] if candidates else None

    # ---------------------------------------------------------
    # MODE AUTOMATIQUE NORMAL
    # ---------------------------------------------------------
    # On interdit d'abord tout quiz déjà utilisé aujourd'hui.
    # ---------------------------------------------------------
    candidates = [
        q
        for q in pool
        if q.get("id") not in used_today
    ]

    if not candidates:
        return None

    # ---------------------------------------------------------
    # COOLDOWN 90 JOURS
    # ---------------------------------------------------------
    cutoff = target_date - timedelta(days=COOLDOWN_DAYS)

    fresh = [
        q
        for q in candidates
        if (
            quiz_last_used(q["id"], hist) is None
            or quiz_last_used(q["id"], hist) < cutoff
        )
    ]

    # Aucun contenu frais :
    # on préfère sauter proprement la publication
    # plutôt que recycler trop rapidement un quiz.
    if not fresh:
        return None

    fresh.sort(
        key=lambda q: (
            quiz_last_used(q["id"], hist) or date.min,
            q["id"],
        )
    )

    return fresh[0]


def already_published_today(hist, target_date: date):
    return any(
        e.get("date") == target_date.isoformat()
        and int(e.get("slot_order", -1)) == QUIZ_SLOT
        and e.get("status", "published") == "published"
        for e in hist
    )


def file_data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def resolve_asset(path_value: str) -> Path:
    path = Path(path_value)

    if not path.is_absolute():
        path = ROOT / path

    path = path.resolve()

    if not path.exists():
        raise RuntimeError(f"Missing asset: {path}")

    return path


def render_template(template_name: str, values: dict, output_name: str):
    template_path = TEMPLATE_DIR / template_name

    if not template_path.exists():
        raise RuntimeError(f"Missing quiz template: {template_path}")

    source = template_path.read_text(encoding="utf-8")

    for key, value in values.items():
        source = source.replace(
            "{{" + key + "}}",
            html.escape(str(value), quote=True),
        )

    # Fail closed on forgotten placeholders.
    if "{{" in source or "}}" in source:
        missing = sorted(
            set(
                part.split("}}", 1)[0]
                for part in source.split("{{")[1:]
                if "}}" in part
            )
        )

        raise RuntimeError(
            f"Unresolved template placeholders in {template_name}: {missing}"
        )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT / output_name

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={
                "width": 1080,
                "height": 1350,
            },
            device_scale_factor=1,
        )

        page.set_content(
            source,
            wait_until="load",
        )

        page.screenshot(
            path=str(output_path),
            type="jpeg",
            quality=96,
            full_page=False,
        )

        browser.close()

    return str(output_path)


def build_caption(item: dict) -> str:
    lines = [
        "◈ Quiz du pèlerin • 21H",
        "",
        item["caption_hook"],
        "",
        "Tu avais la bonne réponse ?",
        "Écris : ✅ Je savais ou 📚 Je viens d’apprendre",
        "",
        "Reviens demain à 21H pour une nouvelle question.",
        "",
        "◈ Envie d’aller plus loin ? Challenge-toi ou défie un proche avec le Grand Quiz Hajj & Omra — lien en bio.",
        "",
        "◈ Apprendre le Hajj et la Omra, une question à la fois.",
    ]

    if item.get("cta_url"):
        lines += [
            "",
            item["cta_url"],
        ]

    return "\n".join(lines)


def prepare(state_path: Path, force: bool = False):
    target_date = date.today()
    hist = history()

    # ---------------------------------------------------------
    # ANTI-DOUBLON QUOTIDIEN
    # ---------------------------------------------------------
    # En automatique :
    # si le quiz du jour est déjà publié, on ne fait rien.
    #
    # En FORCE manuel :
    # on autorise volontairement un nouveau test.
    # ---------------------------------------------------------
    if already_published_today(hist, target_date) and not force:
        state = {
            "skip": True,
            "reason": "quiz_already_published",
            "date": target_date.isoformat(),
            "slot": QUIZ_SLOT,
        }

        save_json(state_path, state)
        print(
            json.dumps(
                state,
                ensure_ascii=False,
                indent=2,
            )
        )

        return

    pool = approved_quizzes()

    # IMPORTANT :
    # on transmet maintenant force à choose_quiz().
    item = choose_quiz(
        pool,
        hist,
        target_date,
        force=force,
    )

    if item is None:
        state = {
            "skip": True,
            "reason": "no_fresh_approved_quiz",
            "date": target_date.isoformat(),
            "slot": QUIZ_SLOT,
            "approved_quiz_count": len(pool),
            "force": force,
        }

        save_json(state_path, state)

        print(
            json.dumps(
                state,
                ensure_ascii=False,
                indent=2,
            )
        )

        return

    # Reuse the machine's approved image selection engine.
    # Synthetic slot 109 avoids colliding with the normal
    # photo seed while remaining deterministic.
    photo = choose_photo(
        item,
        hist,
        target_date,
        109,
    )

    background_path = resolve_asset(
        photo["file"]
    )

    logo_path = BRAND_LOGO.resolve()

    if not logo_path.exists():
        raise RuntimeError(
            f"Missing branding asset: {logo_path}"
        )

    common = {
        "BACKGROUND_DATA": file_data_uri(background_path),
        "LOGO_DATA": file_data_uri(logo_path),
        "BACKGROUND_POSITION": photo.get(
            "default_position",
            "center center",
        ),
        "SECTION": item.get(
            "section",
            "QUIZ DU PÈLERIN",
        ),
        "RENDEZVOUS": "LE RENDEZ-VOUS DE 21H",
        "CATEGORY": item.get(
            "category_label",
            "Hajj & Omra",
        ),
        "WEBSITE": "www.labaykanusuk.com",
    }

    card1_values = {
        **common,
        "CARD_COUNTER": "1/3",
        "QUESTION_BEFORE": item.get(
            "question_before",
            "",
        ),
        "QUESTION_HIGHLIGHT": item.get(
            "question_highlight",
            "",
        ),
        "QUESTION_AFTER": item.get(
            "question_after",
            "",
        ),
        "OPTION_A": item["options"][0],
        "OPTION_B": item["options"][1],
        "OPTION_C": item["options"][2],
        "HOOK": item.get(
            "hook",
            "Peu de gens connaissent la réponse.",
        ),
    }

    card2_values = {
        **common,
        "CARD_COUNTER": "2/3",
        "ANSWER_LETTER": item["answer_letter"],
        "ANSWER": item["answer"],
        "ANSWER_TEXT": item["answer_text"],
        "SOURCE": item["source"],
        "RIGHT_MESSAGE": item.get(
            "right_message",
            "Bravo. Tu connaissais la réponse.",
        ),
        "WRONG_MESSAGE": item.get(
            "wrong_message",
            "Aujourd’hui, tu repars avec une connaissance de plus.",
        ),
        "CTA_LABEL": item.get(
            "cta_label",
            "CONTINUE D’APPRENDRE",
        ),
    }

    card3_values = {
        **common,
        "CARD_COUNTER": "3/3",
        "CTA_EYEBROW": "LABAYKANUSUK PLAY",
        "CTA_TITLE": "Challenge-toi. Défie un proche.",
        "CTA_COPY": (
            "Passe du quiz du soir au Grand Quiz Hajj & Omra "
            "et teste vraiment tes connaissances."
        ),
        "CTA_BUTTON": "GRAND QUIZ HAJJ & OMRA",
        "CTA_HINT": "Lien en bio",
    }

    out1 = render_template(
        "quiz-question.html",
        card1_values,
        "quiz-card-1.jpg",
    )

    out2 = render_template(
        "quiz-answer.html",
        card2_values,
        "quiz-card-2.jpg",
    )

    out3 = render_template(
        "quiz-cta.html",
        card3_values,
        "quiz-card-3.jpg",
    )

    state = {
        "skip": False,
        "date": target_date.isoformat(),
        "slot": QUIZ_SLOT,
        "slot_format": "carousel",
        "slot_category": "quiz",
        "kind": "CAROUSEL",
        "content_id": item["id"],
        "content_type": "quiz",
        "photo_id": photo["id"],
        "outputs": [
            out1,
            out2,
            out3,
        ],
        "public_filenames": [
            "quiz-card-1.jpg",
            "quiz-card-2.jpg",
            "quiz-card-3.jpg",
        ],
        "caption": build_caption(item),
        "cta_url": item.get(
            "cta_url",
            "",
        ),
        "source": item.get(
            "source",
            "",
        ),
        "review_status": item.get(
            "review_status"
        ),
        "force": force,
    }

    save_json(
        state_path,
        state,
    )

    print(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        )
    )


def wait_container(
    version: str,
    container_id: str,
    token: str,
    attempts: int = 30,
):
    for _ in range(attempts):
        status = api_json(
            f"https://graph.instagram.com/{version}/{container_id}",
            {
                "fields": "status_code,status",
                "access_token": token,
            },
        )

        code = status.get(
            "status_code"
        )

        if code == "FINISHED":
            return

        if code in {
            "ERROR",
            "EXPIRED",
        }:
            raise RuntimeError(
                f"Instagram container processing failed: {status}"
            )

        time.sleep(3)

    raise RuntimeError(
        f"Instagram container did not finish after {attempts} attempts: "
        f"{container_id}"
    )


def publish_carousel(
    state: dict,
    media_urls: list[str],
):
    if len(media_urls) != 3:
        raise RuntimeError(
            "Quiz carousel requires exactly 3 public image URLs"
        )

    token = require_env(
        "IG_ACCESS_TOKEN"
    )

    version = require_env(
        "IG_API_VERSION"
    )

    ig_id, username = resolve_instagram_account(
        token
    )

    base = (
        f"https://graph.instagram.com/"
        f"{version}/{ig_id}"
    )

    child_ids = []

    for url in media_urls:
        child = api_json(
            f"{base}/media",
            {
                "image_url": url,
                "is_carousel_item": "true",
                "access_token": token,
            },
            method="POST",
        )

        child_id = child.get("id")

        if not child_id:
            raise RuntimeError(
                f"No Instagram child container id returned: {child}"
            )

        wait_container(
            version,
            child_id,
            token,
        )

        child_ids.append(
            child_id
        )

    parent = api_json(
        f"{base}/media",
        {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": state.get(
                "caption",
                "",
            ),
            "access_token": token,
        },
        method="POST",
    )

    parent_id = parent.get("id")

    if not parent_id:
        raise RuntimeError(
            f"No Instagram carousel container id returned: {parent}"
        )

    wait_container(
        version,
        parent_id,
        token,
        attempts=40,
    )

    last_error = None

    for attempt in range(8):
        try:
            published = api_json(
                f"{base}/media_publish",
                {
                    "creation_id": parent_id,
                    "access_token": token,
                },
                method="POST",
            )

            media_id = published.get(
                "id"
            )

            if media_id:
                return {
                    "media_id": media_id,
                    "ig_id": ig_id,
                    "username": username,
                    "container_id": parent_id,
                    "child_container_ids": child_ids,
                }

        except Exception as exc:
            last_error = exc

            if attempt < 7:
                time.sleep(5)

    raise RuntimeError(
        f"Instagram carousel publication failed: {last_error}"
    )


def record_success(
    state: dict,
    media_urls: list[str],
    published: dict,
):
    hist = history()

    entry = {
        "date": state["date"],
        "slot_order": state["slot"],
        "slot_format": "carousel",
        "slot_category": "quiz",
        "content_id": state["content_id"],
        "content_type": "quiz",
        "photo_id": state.get(
            "photo_id"
        ),
        "kind": "CAROUSEL",
        "status": "published",
        "instagram_media_id": published[
            "media_id"
        ],
        "instagram_username": published[
            "username"
        ],
        "public_urls": media_urls,
        "source": state.get(
            "source",
            "",
        ),
        "published_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    hist.append(
        entry
    )

    save_json(
        HISTORY_PATH,
        hist,
    )

    return entry


def publish(
    state_path: Path,
    media_urls: list[str],
    live: bool,
):
    state = load_json(
        state_path,
        {},
    )

    if state.get("skip"):
        print(
            json.dumps(
                state,
                ensure_ascii=False,
                indent=2,
            )
        )

        return

    if not live:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "state": state,
                    "media_urls": media_urls,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        return

    result = publish_carousel(
        state,
        media_urls,
    )

    entry = record_success(
        state,
        media_urls,
        result,
    )

    print(
        json.dumps(
            {
                "published": True,
                "result": result,
                "history": entry,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "LABAYKANUSUK Quiz du pèlerin runner"
        )
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    p_prepare = sub.add_parser(
        "prepare"
    )

    p_prepare.add_argument(
        "--state",
        required=True,
    )

    p_prepare.add_argument(
        "--force",
        action="store_true",
    )

    p_publish = sub.add_parser(
        "publish"
    )

    p_publish.add_argument(
        "--state",
        required=True,
    )

    p_publish.add_argument(
        "--url",
        action="append",
        required=True,
    )

    p_publish.add_argument(
        "--live",
        action="store_true",
    )

    args = parser.parse_args()

    if args.command == "prepare":
        prepare(
            Path(args.state),
            force=args.force,
        )

    else:
        publish(
            Path(args.state),
            args.url,
            live=args.live,
        )


if __name__ == "__main__":
    main()
