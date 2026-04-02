from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from js import Uint8Array
from workers import Response, WorkerEntrypoint, fetch

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
HTML_BODY = HTML_FILE.read_text(encoding="utf-8")


def json_response(payload: dict[str, Any], status: int = 200) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False),
        status=status,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
        },
    )


class WorkerJwcStore(JwcStore):
    async def _fetch_page(self, page: int) -> None:
        if page in self.page_cache:
            return

        result = extract_list_page(await fetch_text(list_page_url(page)))
        self.page_cache[page] = result.items
        self.total_pages = result.total_pages
        self.total_count = result.total_count
        self.last_list_fetch_at = int(time.time())
        self._rebuild_flat_items()

    async def ensure_loaded(self, until_index: int) -> None:
        if until_index < len(self.flat_items):
            return

        page = 1
        while len(self.flat_items) <= until_index:
            if self.total_pages is not None and page > self.total_pages:
                break
            await self._fetch_page(page)
            page += 1
            if self.total_pages is not None and page > self.total_pages and len(self.flat_items) <= until_index:
                break

    async def list_items(self, offset: int, limit: int, refresh: bool = False) -> dict[str, Any]:
        if refresh:
            self.reset()

        await self.ensure_loaded(offset + limit - 1)
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

    async def get_detail(self, item_id: str) -> dict[str, Any]:
        if item_id in self.detail_cache:
            return self.detail_cache[item_id]

        try:
            index = int(item_id)
        except ValueError as exc:
            raise KeyError("Invalid article id.") from exc

        await self.ensure_loaded(index)
        if index >= len(self.flat_items):
            raise KeyError("Article not found.")

        item = self.flat_items[index]
        detail = extract_article(await fetch_text(item["detail_url"]), item["detail_url"])
        detail["id"] = item_id
        detail["source_url"] = item["detail_url"]
        detail["fetched_at"] = int(time.time())
        self.detail_cache[item_id] = detail
        return detail


STORE = WorkerJwcStore()


async def fetch_text(url: str) -> str:
    response = await fetch(
        url,
        headers={
            "User-Agent": USER_AGENT,
        },
    )
    if not response.ok:
        raise ValueError(f"Upstream returned HTTP {response.status}.")

    content_type = response.headers.get("Content-Type") or response.headers.get("content-type")
    raw = Uint8Array.new(await response.arrayBuffer()).to_bytes()
    return decode_html(raw, content_type)


def parse_int(params: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(params.get(key, [str(default)])[0] or str(default))
    except ValueError:
        return default


class Default(WorkerEntrypoint):
    async def fetch(self, request):  # type: ignore[override]
        parsed = urlparse(str(request.url))
        route = parsed.path or "/"

        if route in {"/", "/jwc_messages.html"}:
            return Response(
                HTML_BODY,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "Cache-Control": "no-store",
                },
            )

        if route == "/favicon.ico":
            return Response("", status=204)

        if route == "/api/notices":
            params = parse_qs(parsed.query)
            offset = parse_int(params, "offset", 0)
            limit = parse_int(params, "limit", DEFAULT_PAGE_SIZE)
            refresh = params.get("refresh", ["0"])[0] == "1"
            try:
                return json_response(await STORE.list_items(offset=offset, limit=limit, refresh=refresh))
            except Exception as exc:  # noqa: BLE001
                return json_response({"error": str(exc)}, status=500)

        if route == "/api/article":
            params = parse_qs(parsed.query)
            item_id = params.get("id", [""])[0]
            try:
                return json_response(await STORE.get_detail(item_id))
            except KeyError as exc:
                return json_response({"error": str(exc)}, status=404)
            except Exception as exc:  # noqa: BLE001
                return json_response({"error": str(exc)}, status=500)

        if route == "/api/ping":
            return json_response({"ok": True})

        return json_response({"error": "Not Found"}, status=404)
