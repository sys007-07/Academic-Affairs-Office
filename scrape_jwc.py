from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from jwc_core import (
    DEFAULT_PAGE_SIZE,
    JwcStore,
    USER_AGENT,
    decode_html,
    extract_article,
    extract_list_page,
    list_page_url,
)


HTML_FILE = Path(__file__).with_name("jwc_messages.html")


def make_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        context.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return context


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30, context=make_ssl_context()) as response:
        raw = response.read()
        return decode_html(raw, response.headers.get("Content-Type"))


class LocalJwcStore(JwcStore):
    def _fetch_page(self, page: int) -> None:
        if page in self.page_cache:
            return

        result = extract_list_page(fetch_text(list_page_url(page)))
        self.page_cache[page] = result.items
        self.total_pages = result.total_pages
        self.total_count = result.total_count
        self.last_list_fetch_at = int(time.time())
        self._rebuild_flat_items()

    def ensure_loaded(self, until_index: int) -> None:
        if until_index < len(self.flat_items):
            return

        page = 1
        while len(self.flat_items) <= until_index:
            if self.total_pages is not None and page > self.total_pages:
                break
            self._fetch_page(page)
            page += 1
            if self.total_pages is not None and page > self.total_pages and len(self.flat_items) <= until_index:
                break

    def list_items(self, offset: int, limit: int, refresh: bool = False) -> dict[str, Any]:
        if refresh:
            self.reset()

        self.ensure_loaded(offset + limit - 1)
        items = self.flat_items[offset : offset + limit]
        has_more = bool(self.total_count is None or offset + limit < self.total_count)
        next_offset = offset + len(items)
        return {
            "section": "公告",
            "items": items,
            "offset": offset,
            "limit": limit,
            "next_offset": next_offset,
            "has_more": has_more,
            "total_count": self.total_count or len(self.flat_items),
            "total_pages": self.total_pages or 1,
            "source_url": list_page_url(1),
            "fetched_at": self.last_list_fetch_at,
        }

    def get_detail(self, item_id: str) -> dict[str, Any]:
        if item_id in self.detail_cache:
            return self.detail_cache[item_id]

        try:
            index = int(item_id)
        except ValueError as exc:
            raise KeyError("Invalid article id.") from exc

        self.ensure_loaded(index)
        if index >= len(self.flat_items):
            raise KeyError("Article not found.")

        item = self.flat_items[index]
        detail = extract_article(fetch_text(item["detail_url"]), item["detail_url"])
        detail["id"] = item_id
        detail["source_url"] = item["detail_url"]
        detail["fetched_at"] = int(time.time())
        self.detail_cache[item_id] = detail
        return detail


STORE = LocalJwcStore()


class Handler(BaseHTTPRequestHandler):
    server_version = "JwcMobileServer/3.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self) -> None:
        body = HTML_FILE.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path

        if route in {"/", "/jwc_messages.html"}:
            self.send_html()
            return

        if route == "/api/notices":
            params = parse_qs(parsed.query)
            offset = int(params.get("offset", ["0"])[0] or "0")
            limit = int(params.get("limit", [str(DEFAULT_PAGE_SIZE)])[0] or str(DEFAULT_PAGE_SIZE))
            refresh = params.get("refresh", ["0"])[0] == "1"
            try:
                self.send_json(STORE.list_items(offset=offset, limit=limit, refresh=refresh))
            except Exception as exc:  # noqa: BLE001
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if route == "/api/article":
            params = parse_qs(parsed.query)
            item_id = params.get("id", [""])[0]
            try:
                self.send_json(STORE.get_detail(item_id))
            except KeyError as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except Exception as exc:  # noqa: BLE001
                self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if route == "/api/ping":
            self.send_json({"ok": True})
            return

        self.send_json({"error": "Not Found"}, HTTPStatus.NOT_FOUND)


def get_local_urls(host: str, port: int) -> list[str]:
    if host not in {"0.0.0.0", ""}:
        return [f"http://{host}:{port}/"]

    urls = [f"http://127.0.0.1:{port}/"]
    candidates: set[str] = set()
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip and not ip.startswith("127."):
                candidates.add(ip)
    except OSError:
        pass

    for ip in sorted(candidates):
        urls.append(f"http://{ip}:{port}/")
    return urls


def run_server(host: str, port: int) -> None:
    STORE.list_items(offset=0, limit=DEFAULT_PAGE_SIZE, refresh=True)
    server = ThreadingHTTPServer((host, port), Handler)
    print("Local server started.")
    for url in get_local_urls(host, port):
        print(url)
    server.serve_forever()


def run_check() -> None:
    result = STORE.list_items(offset=0, limit=DEFAULT_PAGE_SIZE, refresh=True)
    print(f"Fetched {len(result['items'])} notices.")
    if result["items"]:
        detail = STORE.get_detail(result["items"][0]["id"])
        print(f"First article: {detail['title']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="FJTCM JWC notice viewer")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", default=int(os.environ.get("PORT", "8000")), type=int)
    parser.add_argument("--check", action="store_true", help="Fetch list and one detail without starting HTTP server")
    args = parser.parse_args()

    if args.check:
        run_check()
        return

    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
