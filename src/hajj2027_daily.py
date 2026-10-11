#!/usr/bin/env python3
"""Standalone Hajj 2027 Instagram library. No imports from existing runners."""
import argparse, html, json, os, sys, subprocess, tempfile
from pathlib import Path
from datetime import datetime, timezone
from jinja2 import Environment, FileSystemLoader, select_autoescape

BASE=Path(__file__).resolve().parents[1]
BANK=BASE/'hajj2027/posts.json'
HISTORY=BASE/'hajj2027/history.json'
TEMPLATE=BASE/'hajj2027/template.html'
OUT=BASE/'hajj2027/output'

def load():
    bank=json.loads(BANK.read_text(encoding='utf-8'))['items']
    history=json.loads(HISTORY.read_text(encoding='utf-8'))
    assert len(bank)==274 and [x['id'] for x in bank]==list(range(1,275)), 'Invalid bank'
    return bank,history

def save(h):
    HISTORY.write_text(json.dumps(h,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def next_item(bank,h):
    used=set(h['published'])
    if h.get('pending') is not None:
        raise SystemExit('STOP: pending post exists. Inspect history and Instagram before retrying.')
    return next((x for x in bank if x['id'] not in used),None)

def caption(x):
    return f"HAJJ 2027 | {x['category']}\n\n{x['question']}\n\n{x['answer']}\n\nPour préparer votre Hajj : labaykanusuk.com\n\n#Hajj2027 #Hajj #Nusuk #LABAYKANUSUK"

def render(x):
    OUT.mkdir(parents=True,exist_ok=True)
    env=Environment(loader=FileSystemLoader(str(TEMPLATE.parent)),autoescape=select_autoescape(['html']))
    q=x['question'];a=x['answer']
    # conservative font sizes; visual overflow is tested with browser JS
    qsize=63 if len(q)<80 else 55 if len(q)<115 else 46
    asize=39 if len(a)<165 else 34 if len(a)<230 else 29
    page=env.get_template('template.html').render(question=q,answer=a,category=x['category'],counter=f"{x['id']:03d} / 274",question_font=qsize,answer_font=asize)
    htmlfile=OUT/'post.html';htmlfile.write_text(page,encoding='utf-8')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        tab=browser.new_page(viewport={'width':1080,'height':1350},device_scale_factor=1)
        tab.goto(htmlfile.resolve().as_uri(),wait_until='load')
        tab.evaluate('document.fonts.ready')
        over=tab.evaluate("""() => {let q=document.querySelector('.question'), a=document.querySelector('.answer-text'), top=document.querySelector('.top'), footer=document.querySelector('.footer');return {q:q.getBoundingClientRect().bottom>top.getBoundingClientRect().bottom-25,a:a.getBoundingClientRect().bottom>footer.getBoundingClientRect().top-18}}""")
        if any(over.values()):
            browser.close();raise SystemExit(f'Layout overflow for item {x["id"]}: {over}. Correct before LIVE.')
        tab.locator('.post').screenshot(path=str(OUT/'post.jpg'),type='jpeg',quality=92)
        browser.close()
    (OUT/'caption.txt').write_text(caption(x),encoding='utf-8')
    (OUT/'selected.json').write_text(json.dumps(x,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"Rendered item {x['id']:03d}: {x['question']}")

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--mode',choices=['DRY','PREPARE','LIVE'],required=True);args=ap.parse_args()
    bank,h=load()
    if args.mode=='DRY':
        x=next_item(bank,h);assert x,'Library exhausted';render(x);return
    if args.mode=='PREPARE':
        x=next_item(bank,h);assert x,'Library exhausted';render(x)
        h['pending']={'id':x['id'],'created':datetime.now(timezone.utc).isoformat(),'state':'ready'}
        save(h);print('Pending item locked',x['id']);return
    pending=h.get('pending')
    if not pending or pending.get('state')!='ready':raise SystemExit('STOP: no prepared post')
    if os.environ.get('HAJJ2027_LIVE_ENABLED')!='true':raise SystemExit('STOP: LIVE disabled')
    if os.environ.get('GITHUB_REPOSITORY_VISIBILITY')!='public':raise SystemExit('STOP: repo must be public')
    media_url=os.environ.get('HAJJ2027_MEDIA_URL','')
    if not media_url.startswith('https://raw.githubusercontent.com/') or '/docs/media/hajj2027/' not in media_url:raise SystemExit('STOP: invalid media URL')
    from publish_instagram import publish
    x=next((z for z in bank if z['id']==pending['id']),None)
    if not x:raise SystemExit('STOP: pending item not found')
    # Once this point is reached, any error may be ambiguous; pending remains locked.
    result=publish(media_url,'IMAGE',caption(x),dry_run=False)
    if not result.get('id'):raise RuntimeError('Instagram returned no publication ID. Inspect Instagram manually.')
    h['published'].append(x['id']);h['pending']=None;save(h)
    print('PUBLISHED',x['id'],'IG ID',result['id'])
if __name__=='__main__':main()
