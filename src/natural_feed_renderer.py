from __future__ import annotations

from pathlib import Path
import base64
import html
import mimetypes

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "templates"
OUTPUT = ROOT / "output"


def _resolve_asset(path_value: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.exists():
        raise RuntimeError(f"Missing natural-feed asset: {path}")
    return path


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _density(item: dict) -> str:
    text = " ".join(
        str(item.get(k, ""))
        for k in ("quote_before", "quote_highlight", "quote_after")
    ).strip()
    n = len(text)
    if n > 115:
        return "density-compact"
    if n > 82:
        return "density-medium"
    return "density-normal"


def _replace(template: str, values: dict) -> str:
    raw_keys = {"BACKGROUND_DATA", "BACKGROUND_POSITION", "PANEL_CLASS", "DENSITY_CLASS"}
    rendered = template
    for key, value in values.items():
        text = str(value or "")
        if key not in raw_keys:
            text = html.escape(text, quote=True)
        rendered = rendered.replace("{{" + key + "}}", text)
    return rendered


def render_natural_feed(item: dict, photo: dict, output_name: str) -> dict:
    template_path = TEMPLATE_DIR / "feed-natural.html"
    if not template_path.exists():
        raise RuntimeError(f"Missing natural-feed template: {template_path}")

    background_path = _resolve_asset(photo["file"])
    side = str(item.get("panel_side", "left")).lower()
    if side not in {"left", "right"}:
        side = "left"

    values = {
        "BACKGROUND_DATA": _data_uri(background_path),
        "BACKGROUND_POSITION": photo.get("default_position", "center center"),
        "PANEL_CLASS": f"panel-{side}",
        "DENSITY_CLASS": _density(item),
        "QUOTE_BEFORE": item.get("quote_before", ""),
        "QUOTE_HIGHLIGHT": item.get("quote_highlight", ""),
        "QUOTE_AFTER": item.get("quote_after", ""),
        "CATEGORY_LABEL": item.get("category_label", "HAJJ & OMRA"),
        "SOURCE_SHORT": item.get("source_short", item.get("source_reference", "")),
    }

    document = _replace(template_path.read_text(encoding="utf-8"), values)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    out = OUTPUT / output_name

    with sync_playwright() as p:
        launch_args = {"headless": True}
        system_chromium = Path("/usr/bin/chromium")
        if system_chromium.exists():
            launch_args["executable_path"] = str(system_chromium)
        browser = p.chromium.launch(**launch_args)
        page = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        page.set_content(document, wait_until="load")
        page.screenshot(path=str(out), type="jpeg", quality=94, full_page=False)
        browser.close()

    return {
        "output": str(out),
        "logo_variant": "text-mark",
        "density": values["DENSITY_CLASS"],
        "panel_side": side,
    }
