#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

API_VERSION = (os.environ.get("SHOPIFY_API_VERSION") or "2026-07").strip()


def req_env(name: str) -> str:
    v = (os.environ.get(name) or "").strip()
    if not v:
        raise RuntimeError(f"Missing env: {name}")
    return v


def normalize_shop(v: str) -> str:
    v = v.removeprefix("https://").removeprefix("http://").strip().strip("/")
    if v.endswith(".myshopify.com"):
        v = v[:-len(".myshopify.com")]
    return v


def request_json(req):
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:1200]}") from e


def token_for(shop: str, client_id: str, client_secret: str) -> str:
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()

    req = urllib.request.Request(
        f"https://{shop}.myshopify.com/admin/oauth/access_token",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    data = request_json(req)
    tok = data.get("access_token")
    if not tok:
        raise RuntimeError("No access_token returned")
    return tok


def graphql(shop: str, token: str, query: str) -> dict:
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        f"https://{shop}.myshopify.com/admin/api/{API_VERSION}/graphql.json",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Shopify-Access-Token": token,
        },
    )
    return request_json(req)


QUERY = r"""
query Diagnostic {
  shop {
    name
    myshopifyDomain
  }

  currentAppInstallation {
    app {
      title
    }
    accessScopes {
      handle
    }
  }

  directIndex: metaobjectByHandle(
    handle: {type: "fil_pelerin_index", handle: "main"}
  ) {
    id
    handle
    type
  }

  indexList: metaobjects(type: "fil_pelerin_index", first: 10) {
    nodes {
      id
      handle
      type
    }
  }

  publicationList: metaobjects(type: "publication_labaykanusuk", first: 10) {
    nodes {
      id
      handle
      type
    }
  }
}
"""


def main():
    shop = normalize_shop(req_env("SHOPIFY_SHOP"))
    client_id = req_env("SHOPIFY_CLIENT_ID")
    client_secret = req_env("SHOPIFY_CLIENT_SECRET")

    token = token_for(shop, client_id, client_secret)
    raw = graphql(shop, token, QUERY)

    data = raw.get("data") or {}
    errors = raw.get("errors") or []

    install = data.get("currentAppInstallation") or {}
    scopes = sorted(
        x.get("handle")
        for x in (install.get("accessScopes") or [])
        if x.get("handle")
    )

    result = {
        "shop": (data.get("shop") or {}).get("name"),
        "app": (install.get("app") or {}).get("title"),
        "api_version": API_VERSION,
        "granted_scopes": scopes,
        "has_read_metaobjects": "read_metaobjects" in scopes,
        "has_write_metaobjects": "write_metaobjects" in scopes,
        "direct_index_main": data.get("directIndex"),
        "index_handles": [
            n.get("handle")
            for n in ((data.get("indexList") or {}).get("nodes") or [])
        ],
        "publication_handles": [
            n.get("handle")
            for n in ((data.get("publicationList") or {}).get("nodes") or [])
        ],
        "graphql_errors": errors,
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if "read_metaobjects" not in scopes or "write_metaobjects" not in scopes:
        raise RuntimeError(
            "Required scopes are not both granted to this installed app."
        )

    if data.get("directIndex") is None:
        raise RuntimeError(
            "The app is authenticated and scoped, but fil_pelerin_index/main "
            "is still not visible to this app."
        )

    print("SHOPIFY SOCIAL ARCHIVE DIAGNOSTIC OK")


if __name__ == "__main__":
    main()
