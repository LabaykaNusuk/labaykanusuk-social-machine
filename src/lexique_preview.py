import argparse, json, asyncio
from pathlib import Path
from jinja2 import Template
from playwright.async_api import async_playwright

async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--index",type=int,default=0)
    a=ap.parse_args()
    entries=json.loads(Path("lexique/termes.json").read_text())
    if len(entries)!=148:
        print(f"ATTENTION: {len(entries)}/148 termes seulement. Prototype DRY uniquement.")
    item=entries[a.index]
    html=Template(Path("lexique/template.html").read_text()).render(**item)
    out=Path("lexique-preview.png").resolve()
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={"width":1080,"height":1350},device_scale_factor=1)
        await page.set_content(html)
        await page.add_style_tag(content="body{padding:0!important;display:block!important;background:transparent!important}.stage{width:1080px!important;max-width:none!important}.card{border-radius:0!important;box-shadow:none!important}.caption{display:none!important}")
        await page.locator(".card").screenshot(path=str(out))
        await browser.close()
    print(f"DRY OK: {out} - {item['term']}")
asyncio.run(main())
