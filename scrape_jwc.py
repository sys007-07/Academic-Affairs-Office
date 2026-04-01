from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import time
from dataclasses import dataclass
from html import unescape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen


BASE_URL = "https://jwc.fjtcm.edu.cn/"
LIST_ROOT = urljoin(BASE_URL, "/955/")
LIST_SOURCE_URL = urljoin(LIST_ROOT, "list.htm")
HTML_FILE = Path(__file__).with_name("jwc_messages.html")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_PAGE_SIZE = 6
SECTION_NAME = "公示"


def make_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        context.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return context


def detect_encoding(raw: bytes, content_type: str | None) -> str:
    if content_type:
        match = re.search(r"charset=([^\s;]+)", content_type, re.I)
        if match:
            return match.group(1).strip("\"'")

    match = re.search(br"<meta[^>]+charset=['\"]?([\w-]+)", raw, re.I)
    if match:
        return match.group(1).decode("ascii", errors="ignore")

    return "utf-8"


def looks_mojibake(text: str) -> bool:
    suspicious = "忙氓莽猫茅锚卯茂冒帽貌贸么枚"
    bad_count = sum(text.count(char) for char in suspicious)
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    return bad_count >= 8 and cjk_count <= 5


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30, context=make_ssl_context()) as response:
        raw = response.read()
        encoding = detect_encoding(raw, response.headers.get("Content-Type"))

    text = raw.decode(encoding, errors="ignore")
    if looks_mojibake(text):
        text = raw.decode("gb18030", errors="ignore")
    return text


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return unescape(text).strip()


def list_page_url(page: int) -> str:
    if page <= 1:
        return LIST_SOURCE_URL
    return urljoin(LIST_ROOT, f"list{page}.htm")


@dataclass
class ListPageResult:
    items: list[dict[str, str]]
    total_pages: int
    total_count: int


def extract_list_page(html: str) -> ListPageResult:
    list_match = re.search(
        r'<ul class="news_list list2">(.*?)</ul>',
        html,
        re.S,
    )
    if not list_match:
        raise ValueError("未找到公示列表，网页结构可能已经变化。")

    items: list[dict[str, str]] = []
    for match in re.finditer(r'<li class="news[^"]* clearfix">(.*?)</li>', list_match.group(1), re.S):
        block = match.group(1)
        link_match = re.search(r"<a href='([^']+)'[^>]*title='([^']+)'", block, re.S)
        date_match = re.search(r'<span class="news_meta">\s*\[?([^<\]]+)\]?\s*</span>', block, re.S)
        if not link_match:
            continue

        href, title = link_match.groups()
        items.append(
            {
                "title": clean_text(title),
                "date": clean_text(date_match.group(1)) if date_match else "",
                "detail_url": urljoin(BASE_URL, href),
            }
        )

    total_pages_match = re.search(r'<em class="all_pages">(\d+)</em>', html)
    total_count_match = re.search(r'<em class="all_count">(\d+)</em>', html)
    total_pages = int(total_pages_match.group(1)) if total_pages_match else 1
    total_count = int(total_count_match.group(1)) if total_count_match else len(items)
    return ListPageResult(items=items, total_pages=total_pages, total_count=total_count)


def rewrite_article_html(content_html: str, page_url: str) -> str:
    def replace_href(match: re.Match[str]) -> str:
        absolute = urljoin(page_url, match.group(2))
        safe = (
            absolute.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return f'href="{safe}" target="_blank" rel="noopener noreferrer"'

    def replace_src(match: re.Match[str]) -> str:
        absolute = urljoin(page_url, match.group(2))
        safe = (
            absolute.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return f'src="{safe}"'

    content_html = re.sub(
        r"\s(?:onclick|onerror|onload|onmouseover)=(['\"]).*?\1",
        "",
        content_html,
        flags=re.I | re.S,
    )
    content_html = re.sub(r'href=(["\'])(.*?)\1', replace_href, content_html, flags=re.I | re.S)
    content_html = re.sub(r'src=(["\'])(.*?)\1', replace_src, content_html, flags=re.I | re.S)
    content_html = re.sub(r"\s+target=(['\"]).*?\1", "", content_html, flags=re.I | re.S)
    return content_html


def extract_article(html: str, page_url: str) -> dict[str, str]:
    title_match = re.search(r'<h1 class="arti_title">\s*(.*?)\s*</h1>', html, re.S)
    metas_match = re.search(r'<p class="arti_metas">(.*?)</p>', html, re.S)
    read_match = re.search(r'<div class="read">(.*?)</div>\s*</div>\s*</div>', html, re.S)
    if not title_match or not read_match:
        raise ValueError("未找到文章正文结构，详情页可能已经变化。")

    metas_html = metas_match.group(1) if metas_match else ""
    date_match = re.search(r"发布时间[：:]\s*([^<]+)", metas_html)
    source_match = re.search(r"来源[：:]\s*([^<]+)", metas_html)
    return {
        "title": clean_text(title_match.group(1)),
        "date": clean_text(date_match.group(1)) if date_match else "",
        "source": clean_text(source_match.group(1)) if source_match else "",
        "content_html": rewrite_article_html(read_match.group(1), page_url),
    }


class JwcStore:
    def __init__(self) -> None:
        self.page_cache: dict[int, list[dict[str, str]]] = {}
        self.detail_cache: dict[str, dict[str, Any]] = {}
        self.total_pages: int | None = None
        self.total_count: int | None = None
        self.flat_items: list[dict[str, str]] = []
        self.last_list_fetch_at: int | None = None

    def reset(self) -> None:
        self.page_cache.clear()
        self.detail_cache.clear()
        self.flat_items = []
        self.total_pages = None
        self.total_count = None
        self.last_list_fetch_at = None

    def _rebuild_flat_items(self) -> None:
        combined: list[dict[str, str]] = []
        for page in sorted(self.page_cache):
            combined.extend(self.page_cache[page])

        self.flat_items = []
        for index, item in enumerate(combined):
            cloned = dict(item)
            cloned["id"] = str(index)
            self.flat_items.append(cloned)

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
            "section": SECTION_NAME,
            "items": items,
            "offset": offset,
            "limit": limit,
            "next_offset": next_offset,
            "has_more": has_more,
            "total_count": self.total_count or len(self.flat_items),
            "total_pages": self.total_pages or 1,
            "source_url": LIST_SOURCE_URL,
            "fetched_at": self.last_list_fetch_at,
        }

    def get_detail(self, item_id: str) -> dict[str, Any]:
        if item_id in self.detail_cache:
            return self.detail_cache[item_id]

        try:
            index = int(item_id)
        except ValueError as exc:
            raise KeyError("无效的文章编号。") from exc

        self.ensure_loaded(index)
        if index >= len(self.flat_items):
            raise KeyError("未找到对应的文章。")

        item = self.flat_items[index]
        detail = extract_article(fetch_text(item["detail_url"]), item["detail_url"])
        detail["id"] = item_id
        detail["source_url"] = item["detail_url"]
        detail["fetched_at"] = int(time.time())
        self.detail_cache[item_id] = detail
        return detail


STORE = JwcStore()


class Handler(BaseHTTPRequestHandler):
    server_version = "JwcMobileServer/2.1"

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
    print("本地 HTTP 服务已启动。")
    for url in get_local_urls(host, port):
        print(url)
    print("首页会先显示部分公示标题，点击“查看更多”后继续加载。")
    print("点击标题后，文章详情会在当前页面中即时抓取并展示。")
    server.serve_forever()


def run_check() -> None:
    result = STORE.list_items(offset=0, limit=DEFAULT_PAGE_SIZE, refresh=True)
    print(f"首批标题抓取成功，共返回 {len(result['items'])} 条。")
    if result["items"]:
        detail = STORE.get_detail(result["items"][0]["id"])
        print(f"详情抓取成功：{detail['title']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="福建中医药大学教务处公示手机查看器")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", default=int(os.environ.get("PORT", "8000")), type=int)
    parser.add_argument("--check", action="store_true", help="仅验证标题和详情抓取，不启动 HTTP 服务")
    args = parser.parse_args()

    if args.check:
        run_check()
        return

    run_server(args.host, args.port)


if __name__ == "__main__":
    main()
