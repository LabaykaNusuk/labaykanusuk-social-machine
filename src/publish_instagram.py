from pathlib import Path
import argparse, os, sys, time, json, urllib.parse, urllib.request

DRY_RUN_DEFAULT = True

def require_env(name):
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"Missing environment variable: {name}")
    return v

def post_form(url, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def get_json(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))

def publish(media_url, media_kind, caption="", share_to_feed=True, dry_run=True):
    ig_user_id = require_env("IG_USER_ID")
    token = require_env("IG_ACCESS_TOKEN")
    api_version = require_env("IG_API_VERSION")
    host = os.environ.get("IG_GRAPH_HOST", "https://graph.instagram.com")
    base = f"{host}/{api_version}/{ig_user_id}"

    params = {"access_token": token}
    kind = media_kind.upper()
    if kind == "IMAGE":
        params.update({"image_url": media_url, "caption": caption})
    elif kind == "STORY":
        # Instagram Stories via API are available for eligible professional/business accounts.
        # For a static story we send image_url + media_type=STORIES.
        params.update({"image_url": media_url, "media_type": "STORIES"})
    elif kind == "REEL":
        params.update({"video_url": media_url, "media_type": "REELS", "caption": caption, "share_to_feed": str(bool(share_to_feed)).lower()})
    else:
        raise SystemExit("media_kind must be IMAGE, STORY, or REEL")

    if dry_run:
        safe = {k:("***" if k=="access_token" else v) for k,v in params.items()}
        print(json.dumps({"dry_run":True,"create_container":f"{base}/media","params":safe}, ensure_ascii=False, indent=2))
        return {"dry_run": True}

    created = post_form(f"{base}/media", params)
    creation_id = created.get("id")
    if not creation_id:
        raise RuntimeError(f"Container creation failed: {created}")

    # Videos need processing. Static images usually finish immediately.
    if kind in {"REEL","STORY"}:
        for _ in range(60):
            status = get_json(f"{host}/{api_version}/{creation_id}", {"fields":"status_code,status", "access_token":token})
            code = status.get("status_code")
            if code == "FINISHED": break
            if code in {"ERROR","EXPIRED"}: raise RuntimeError(status)
            time.sleep(5)

    published = post_form(f"{base}/media_publish", {"creation_id": creation_id, "access_token": token})
    return published

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Publicly accessible media URL")
    ap.add_argument("--kind", required=True, choices=["IMAGE","STORY","REEL"])
    ap.add_argument("--caption", default="")
    ap.add_argument("--live", action="store_true", help="Actually publish. Without this flag, dry-run only.")
    ap.add_argument("--no-share-to-feed", action="store_true")
    args = ap.parse_args()
    print(json.dumps(publish(args.url,args.kind,args.caption,not args.no_share_to_feed,not args.live), ensure_ascii=False, indent=2))
