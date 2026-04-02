from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import urljoin


BASE_URL = "https://jwc.fjtcm.edu.cn/"
LIST_ROOT = urljoin(BASE_URL, "/955/")
LIST_SOURCE_URL = urljoin(LIST_ROOT, "list.htm")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_PAGE_SIZE = 6
SECTION_NAME = "公告"


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
    suspicious = "蹇欐皳鑾界尗鑼呴敋鍗寕鍐掑附璨岃锤涔堟灇"
    bad_count = sum(text.count(char) for char in suspicious)
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    return bad_count >= 8 and cjk_count <= 5


def decode_html(raw: bytes, content_type: str | None) -> str:
    encoding = detect_encoding(raw, content_type)
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
        raise ValueError("未找到公告列表，网页结构可能已经变化。")

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
