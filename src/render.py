from pathlib import Path
import argparse, json, base64, mimetypes, colorsys
from PIL import Image, ImageStat
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
OUTPUT = ROOT / "output"

LOGO_ASSETS = {
    "black_gold": "assets/logos/sigle_black_gold.png",
    "white": "assets/logos/sigle_white.png",
    "white_gold": "assets/logos/sigle_white_gold.png",
}

def resolve_asset(path_value: str, data_path: Path) -> Path:
    p = Path(path_value)
    if p.is_absolute() and p.exists():
        return p
    for candidate in [ROOT / p, data_path.parent / p, ROOT / "config" / p]:
        candidate = candidate.resolve()
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Asset not found: {path_value}")

def to_data_uri(path_value: str, data_path: Path) -> str:
    if path_value.startswith(("http://", "https://", "data:")):
        return path_value
    p = resolve_asset(path_value, data_path)
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"

def infer_density(data: dict) -> str:
    text = " ".join(str(data.get(k, "")) for k in ["title_before", "title_highlight", "copy", "quote", "source"])
    n = len(text.strip())
    if n <= 165: return "short"
    if n <= 330: return "medium"
    if n <= 540: return "long"
    return "xlong"

def analyze_background(path: Path):
    im = Image.open(path).convert("RGB")
    w,h = im.size
    # Sample the bottom-right region where the logo lives.
    crop = im.crop((int(w*.62), int(h*.62), w, h)).resize((80,80))
    stat = ImageStat.Stat(crop)
    r,g,b = stat.mean
    luminance = 0.2126*r + 0.7152*g + 0.0722*b
    # Average HSV saturation
    px = list(crop.getdata())
    sats = []
    for rr,gg,bb in px[::16]:
        _,s,_ = colorsys.rgb_to_hsv(rr/255,gg/255,bb/255)
        sats.append(s)
    saturation = sum(sats)/len(sats) if sats else 0
    return luminance, saturation

def choose_logo(data: dict, background_path: Path | None):
    explicit = data.get("logo_variant")
    if explicit in LOGO_ASSETS:
        return explicit
    # Current approved designs have dark cinematic overlays; default is the premium white+gold mark.
    if background_path is None:
        return "white_gold"
    try:
        lum, sat = analyze_background(background_path)
        # Bright backgrounds need the black/gold mark.
        if lum >= 175:
            return "black_gold"
        # Very colorful/visually busy backgrounds get the clean white mark.
        if sat >= .52 and 70 <= lum < 175:
            return "white"
        return "white_gold"
    except Exception:
        return "white_gold"

def render(template_name, data_path, output_name):
    data_path = Path(data_path)
    data = json.loads(data_path.read_text(encoding="utf-8"))

    background_path = None
    if data.get("background") and not str(data["background"]).startswith(("http://","https://","data:")):
        background_path = resolve_asset(data["background"], data_path)
    if data.get("background"):
        data["background"] = to_data_uri(data["background"], data_path)

    variant = choose_logo(data, background_path)
    data["logo_variant_resolved"] = variant
    data["logo"] = to_data_uri(LOGO_ASSETS[variant], data_path)

    data.setdefault("density", infer_density(data))
    data.setdefault("background_position", "center center")
    data.setdefault("debug_class", "")

    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    template = env.get_template(template_name)
    html = template.render(**data)
    css = (TEMPLATES / "style.css").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="style.css">', f"<style>{css}</style>")

    width = 1080
    height = 1920 if template_name.startswith("story") else 1350
    OUTPUT.mkdir(exist_ok=True)
    out_path = OUTPUT / output_name

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/usr/bin/chromium" if Path("/usr/bin/chromium").exists() else None,
            args=["--no-sandbox"]
        )
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        page.set_content(html, wait_until="load")
        # PNG is the master. JPEG is generated after capture for platform compatibility.
        if out_path.suffix.lower() in {".jpg", ".jpeg"}:
            tmp = OUTPUT / (out_path.stem + "__master.png")
            page.screenshot(path=str(tmp), full_page=False)
            browser.close()
            Image.open(tmp).convert("RGB").save(out_path, quality=95, optimize=True)
            tmp.unlink(missing_ok=True)
        else:
            page.screenshot(path=str(out_path), full_page=False)
            browser.close()

    return {"output": str(out_path), "logo_variant": variant, "density": data["density"]}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True, choices=[
        "story-religion.html","story-tool.html","feed-religion.html","feed-tool.html"
    ])
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = render(args.template, args.data, args.output)
    print(json.dumps(result, ensure_ascii=False))
