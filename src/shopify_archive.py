#!/usr/bin/env python3
"""
LABAYKANUSUK — Shopify Social Archive bridge.

Phase 1: safe health-check only.
- Obtains a short-lived Shopify Admin token using Client Credentials.
- Verifies the shop and read access to the two archive metaobject types.
- Never prints secrets or access tokens.
- Does not publish to Instagram.
- Does not write anything to Shopify.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request


API_VERSION_DEFAULT = "2026-07"


def require_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def normalize_shop(value: str) -> str:
    value = value.strip()
    value = value.removeprefix("https://").removeprefix("http://").strip("/")
    if value.endswith(".myshopify.com"):
        value = value[: -len(".myshopify.com")]
    if not value:
        raise RuntimeError("SHOPIFY_SHOP is empty after normalization")
    return value


def request_json(req: urllib.request.Request, timeout: int = 30) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)
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
            "User-Agent": "LABAYKANUSUK-Social-Archive/1.0",
        },
    )

    data = request_json(req)
    token = data.get("access_token")
    if not token:
        raise RuntimeError("Shopify did not return an access_token")
    return str(token)


def graphql(shop: str, token: str, api_version: str, query: str, variables: dict | None = None) -> dict:
    url = f"https://{shop}.myshopify.com/admin/api/{api_version}/graphql.json"
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
            "User-Agent": "LABAYKANUSUK-Social-Archive/1.0",
        },
    )

    data = request_json(req)
    if data.get("errors"):
        raise RuntimeError(
            "Shopify GraphQL error: "
            + json.dumps(data["errors"], ensure_ascii=False)
        )
    return data.get("data") or {}


HEALTH_QUERY = r"""
query CheckFilPelerinAccess {
  shop {
    name
    myshopifyDomain
  }
  publications: metaobjects(type: "publication_labaykanusuk", first: 1) {
    nodes {
      id
      handle
      type
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
  indexes: metaobjects(type: "fil_pelerin_index", first: 1) {
    nodes {
      id
      handle
      type
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


def health_check() -> int:
    shop = normalize_shop(require_env("SHOPIFY_SHOP"))
    client_id = require_env("SHOPIFY_CLIENT_ID")
    client_secret = require_env("SHOPIFY_CLIENT_SECRET")
    api_version = (os.environ.get("SHOPIFY_API_VERSION") or API_VERSION_DEFAULT).strip()

    token = get_access_token(shop, client_id, client_secret)
    data = graphql(shop, token, api_version, HEALTH_QUERY)

    shop_data = data.get("shop") or {}
    publication_connection = data.get("publications")
    index_connection = data.get("indexes")

    if publication_connection is None:
        raise RuntimeError("Cannot read publication_labaykanusuk metaobjects")
    if index_connection is None:
        raise RuntimeError("Cannot read fil_pelerin_index metaobjects")

    index_nodes = index_connection.get("nodes") or []
    index_main_found = any(node.get("handle") == "main" for node in index_nodes)

    result = {
        "ok": True,
        "shop": shop_data.get("name"),
        "myshopify_domain": shop_data.get("myshopifyDomain"),
        "api_version": api_version,
        "publication_archive_access": True,
        "publication_entries_found_in_sample": len(publication_connection.get("nodes") or []),
        "index_access": True,
        "index_main_found": index_main_found,
        "message": "SHOPIFY SOCIAL ARCHIVE CONNECTION OK",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not index_main_found:
        raise RuntimeError("fil_pelerin_index/main was not found")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LABAYKANUSUK Shopify Social Archive bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health-check", help="Test Shopify authentication and archive read access")
    args = parser.parse_args()

    if args.command == "health-check":
        return health_check()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
