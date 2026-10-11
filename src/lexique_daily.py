from __future__ import annotations
import argparse, asyncio, datetime as dt, json, os, subprocess, sys, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
from jinja2 import Environment, StrictUndefined, select_autoescape
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
BANK=ROOT/"lexique/termes.json"
HISTORY=ROOT/"lexique/publication_history.json"
PARIS=ZoneInfo("Europe/Paris")

def entries():
    data=json.loads(BANK.read_text(encoding="utf-8"))
    if len(data)!=148: raise ValueError(f"148 termes attendus, {len(data)} trouvés")
    for i,item in enumerate(data):
        for field in ("term","category","context","definition","variants"):
            if field not in item: raise ValueError(f"Entrée {i+1}: {field} manquant")
    return data

def today(): return dt.datetime.now(PARIS).date().isoformat()

def history():
    return json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []

def select(data, records, day):
    done={r["date"] for r in records if r.get("status")=="published"}
    if day in done: return None
    used={int(r["index"]) for r in records if r.get("status")=="published"}
    for i in range(len(data)):
        if i not in used: return i
    return None  # stop after 148 rather than repeat

async def render(item, dest):
    source=(ROOT/"lexique/template.html").read_text(encoding="utf-8")
    env=Environment(autoescape=select_autoescape(["html"]),undefined=StrictUndefined)
    html=env.from_string(source).render(**item)
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={"width":1080,"height":1350},device_scale_factor=1)
        await page.set_content(html,wait_until="networkidle")
        await page.add_style_tag(content="""html,body{width:1080px!important;height:1350px!important;margin:0!important;padding:0!important;display:block!important;overflow:hidden!important}.stage{width:1080px!important;max-width:none!important}.card{width:1080px!important;height:1350px!important;border-radius:0!important;box-shadow:none!important}.caption{display:none!important}""")
        await page.locator(".card").screenshot(path=str(dest),type="jpeg",quality=92)
        await browser.close()

def caption(item):
    return f"{item['term']}\n\n{item['definition']}\n\nGrand Lexique du Hajj ◈ LABAYKANUSUK\nlabaykanusuk.com\n\n#Hajj #Omra #Labaykanusuk"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mode",choices=["DRY","LIVE"],default="DRY")
    ap.add_argument("--day",default=today())
    args=ap.parse_args()
    data=entries()
    records=history()
    index=select(data,records,args.day)
    if index is None:
        print("SKIP: déjà publié ce jour ou collection terminée")
        return
    item=data[index]
    out=ROOT/"lexique-preview.jpg"
    asyncio.run(render(item,out))
    print(json.dumps({"date":args.day,"index":index,"term":item["term"],"image":str(out),"caption":caption(item)},ensure_ascii=False))
    if args.mode=="DRY":
        print("DRY: aucune écriture distante, aucune publication")
        return
    if os.getenv("LEXIQUE_LIVE_ENABLED")!="true":
        raise RuntimeError("LIVE désactivé: variable LEXIQUE_LIVE_ENABLED non égale à true")
    if args.day!=today(): raise RuntimeError("LIVE seulement pour la date locale du jour")
    # GitHub Actions prépare et expose l'image; le workflow fournit l'URL publique.
    media_url=os.environ.get("LEXIQUE_MEDIA_URL")
    if not media_url: raise RuntimeError("LEXIQUE_MEDIA_URL manquante")
    from publish_instagram import publish
    result=publish(media_url,"IMAGE",caption(item),dry_run=False)
    if not result.get("id"): raise RuntimeError(f"Réponse Instagram sans ID: {result}")
    records.append({"date":args.day,"index":index,"term":item["term"],"status":"published","instagram_id":str(result["id"])})
    HISTORY.parent.mkdir(parents=True,exist_ok=True)
    HISTORY.write_text(json.dumps(records,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("PUBLISHED",result["id"])

if __name__=="__main__": main()
