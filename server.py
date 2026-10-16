from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


SERVER_NAME = "xianyu"
INDEX_SEARCH_URL = "https://www.so.com/s"
OFFICIAL_SEARCH_URL = "https://www.goofish.com/search"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)
MIN_REQUEST_INTERVAL_SECONDS = 0.8
CACHE_TTL_SECONDS = 600

OFFICIAL_HOST_SUFFIXES = ("goofish.com", "2.taobao.com")
PUBLIC_SOURCE_SUFFIXES = ("smzdm.com", "zol.com.cn")
DIRECT_QUERIES = (
    "site:goofish.com/item {keyword}",
    "site:2.taobao.com/item.htm {keyword}",
)
DISCOVERY_QUERY = "{keyword} 闲鱼 二手 价格"

RESULT_BLOCK_PATTERN = re.compile(
    r'<li class="res-list"[^>]*>(?P<body>.*?)</li>', re.DOTALL | re.IGNORECASE
)
TITLE_PATTERN = re.compile(
    r'<h3\s+class="[^"]*\bres-title\b[^"]*"[^>]*>.*?<a(?P<attrs>[^>]*)>'
    r"(?P<title>.*?)</a>",
    re.DOTALL | re.IGNORECASE,
)
DIRECT_URL_PATTERN = re.compile(r'data-mdurl="(?P<url>[^"]+)"', re.IGNORECASE)
HREF_PATTERN = re.compile(r'href="(?P<url>[^"]+)"', re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")
DATE_PATTERN = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
CURRENCY_PRICE_PATTERN = re.compile(r"[¥￥]\s*(?P<price>\d{1,7}(?:\.\d{1,2})?)")
NAMED_PRICE_PATTERN = re.compile(
    r"(?:闲鱼价|转卖价|现价|售价|出价|标价|到手价|价格)"
    r"[^\d]{0,8}(?P<price>\d{1,7}(?:\.\d{1,2})?)\s*元?"
)
YUAN_PRICE_PATTERN = re.compile(r"(?P<price>\d{1,7}(?:\.\d{1,2})?)\s*元")
ORIGINAL_PRICE_PATTERN = re.compile(
    r"(?:原价|购入价|入手价)[^\d]{0,8}(\d{1,7}(?:\.\d{1,2})?)\s*元?"
)
CONDITION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("全新未拆", re.compile(r"全新未拆|全新未开封")),
    ("全新", re.compile(r"全新")),
    ("未拆封", re.compile(r"未拆封|未开封")),
    ("仅拆封", re.compile(r"仅拆封|只拆封")),
    ("99新", re.compile(r"99\s*新")),
    ("95新", re.compile(r"95\s*新")),
    ("9成新", re.compile(r"9\s*成新")),
    ("个人自用", re.compile(r"个人自用|自用")),
    ("二手", re.compile(r"二手|闲置")),
)
TRANSACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("包邮", re.compile(r"包邮|免运费")),
    ("同城自提", re.compile(r"同城|自提|面交")),
    ("验货宝", re.compile(r"验货宝")),
    ("可议价", re.compile(r"可议价|可小刀|小刀")),
    ("不议价", re.compile(r"不议价|不刀")),
)

_cache: dict[tuple[str, int, float | None], tuple[float, dict[str, Any]]] = {}
_request_lock = asyncio.Lock()
_last_request_at = 0.0


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _validate_keyword(keyword: str) -> str:
    normalized = " ".join(keyword.split())
    if not normalized:
        raise ValueError("keyword must not be empty")
    if len(normalized) > 120:
        raise ValueError("keyword must be at most 120 characters")
    return normalized


def _official_search_link(keyword: str) -> str:
    return OFFICIAL_SEARCH_URL + "?" + urlencode({"q": keyword})


def _clean_html(value: str) -> str:
    return " ".join(html.unescape(TAG_PATTERN.sub(" ", value)).split())


def _hostname_matches(hostname: str, suffixes: tuple[str, ...]) -> bool:
    hostname = hostname.lower()
    return any(
        hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes
    )


def _extract_item_id(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    item_id = (query.get("id") or [None])[0]
    if item_id and re.fullmatch(r"\d+", item_id):
        return item_id
    match = re.search(r"/(?:item|detail)/(\d+)(?:[/?#]|$)", parsed.path)
    return match.group(1) if match else None


def _is_official_item_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if not _hostname_matches(hostname, OFFICIAL_HOST_SUFFIXES):
        return False
    return _extract_item_id(url) is not None and (
        "item" in parsed.path.lower()
        or "page-detail" in parsed.path.lower()
        or hostname == "2.taobao.com"
    )


def _allowed_source_url(url: str, query_kind: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if query_kind == "direct":
        return _hostname_matches(hostname, OFFICIAL_HOST_SUFFIXES)
    return _hostname_matches(
        hostname, (*OFFICIAL_HOST_SUFFIXES, *PUBLIC_SOURCE_SUFFIXES)
    )


def _extract_prices(text: str) -> list[float]:
    values: list[float] = []
    for pattern in (
        NAMED_PRICE_PATTERN,
        CURRENCY_PRICE_PATTERN,
        YUAN_PRICE_PATTERN,
    ):
        for match in pattern.finditer(text):
            value = float(match.group("price"))
            if 0 < value < 10_000_000 and value not in values:
                values.append(value)
    return values


def _displayed_price(text: str) -> float | None:
    for pattern in (
        NAMED_PRICE_PATTERN,
        CURRENCY_PRICE_PATTERN,
        YUAN_PRICE_PATTERN,
    ):
        match = pattern.search(text)
        if match:
            return float(match.group("price"))
    return None


def _matching_labels(
    text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]
) -> list[str]:
    return [label for label, pattern in patterns if pattern.search(text)]


def _condition_labels(text: str) -> list[str]:
    labels = _matching_labels(text, CONDITION_PATTERNS)
    if "全新未拆" in labels:
        labels = [label for label in labels if label not in {"全新", "未拆封"}]
    return labels


def _indexed_date(text: str) -> str | None:
    match = DATE_PATTERN.search(text)
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3))
        ).date().isoformat()
    except ValueError:
        return None


def _freshness(indexed_date: str | None) -> str:
    if not indexed_date:
        return "unknown"
    observed = datetime.fromisoformat(indexed_date).date()
    age_days = (datetime.now(timezone.utc).date() - observed).days
    if age_days < -2:
        return "future_dated_index_entry"
    if age_days <= 30:
        return "recent_index_entry"
    return "stale_index_entry"


def _extract_result_url(block: str, attrs: str) -> str | None:
    match = DIRECT_URL_PATTERN.search(block) or HREF_PATTERN.search(attrs)
    if not match:
        return None
    url = html.unescape(match.group("url")).strip()
    return "https://" + url[7:] if url.startswith("http://") else url


def _parse_index_results(
    page: str,
    query_kind: str,
    limit: int,
    max_price_cny: float | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for block_match in RESULT_BLOCK_PATTERN.finditer(page):
        block = block_match.group("body")
        title_match = TITLE_PATTERN.search(block)
        if not title_match:
            continue
        title = _clean_html(title_match.group("title"))
        source_url = _extract_result_url(block, title_match.group("attrs"))
        if not title or not source_url or source_url in seen_urls:
            continue
        if not _allowed_source_url(source_url, query_kind):
            continue

        snippet = _clean_html(block)
        if query_kind == "discovery" and "闲鱼" not in snippet:
            continue

        displayed_price = _displayed_price(snippet)
        if max_price_cny is not None and (
            displayed_price is not None and displayed_price > max_price_cny
        ):
            continue

        original_match = ORIGINAL_PRICE_PATTERN.search(snippet)
        official_url = source_url if _is_official_item_url(source_url) else None
        item_id = _extract_item_id(official_url) if official_url else None
        indexed = _indexed_date(snippet)
        results.append(
            {
                "platform": "xianyu",
                "platform_name": "闲鱼",
                "title": title,
                "displayed_price_cny": displayed_price,
                "price_kind": (
                    "public_index_listing_price"
                    if displayed_price is not None
                    else "unavailable"
                ),
                "original_or_purchase_price_cny": (
                    float(original_match.group(1)) if original_match else None
                ),
                "all_prices_cny": _extract_prices(snippet),
                "condition_tags": _condition_labels(snippet),
                "transaction_tags": _matching_labels(
                    snippet, TRANSACTION_PATTERNS
                ),
                "item_url": official_url,
                "source_url": source_url,
                "best_available_link": official_url or source_url,
                "link_type": "direct_item" if official_url else "public_source",
                "item_id": item_id,
                "source_host": (urlparse(source_url).hostname or "").lower(),
                "source": "360_public_web_index",
                "indexed_date": indexed,
                "freshness": _freshness(indexed),
                "price_confidence": (
                    "indexed_price_only"
                    if displayed_price is not None
                    else "unavailable"
                ),
                "matches_budget": (
                    None
                    if max_price_cny is None or displayed_price is None
                    else displayed_price <= max_price_cny
                ),
                "snippet": snippet[:400],
            }
        )
        seen_urls.add(source_url)

    results.sort(
        key=lambda item: (
            item["item_url"] is not None,
            item["displayed_price_cny"] is not None,
            item["freshness"] == "recent_index_entry",
            len(item["condition_tags"]) + len(item["transaction_tags"]),
        ),
        reverse=True,
    )
    return results[:limit]


async def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
        await asyncio.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)


async def _fetch_index_page(query: str) -> str:
    global _last_request_at
    async with _request_lock:
        await _throttle()
        async with httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            },
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            response = await client.get(INDEX_SEARCH_URL, params={"q": query})
            _last_request_at = time.monotonic()
            response.raise_for_status()
            return response.text


async def _search(keyword: str, limit: int, max_price_cny: float | None) -> dict[str, Any]:
    cache_key = (keyword, limit, max_price_cny)
    cached = _cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        payload = dict(cached[1])
        payload["cache_hit"] = True
        return payload

    specs = [
        ("direct", template.format(keyword=keyword)) for template in DIRECT_QUERIES
    ]
    specs.append(("discovery", DISCOVERY_QUERY.format(keyword=keyword)))
    listings: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_links: set[str] = set()

    for query_kind, query in specs:
        try:
            page = await _fetch_index_page(query)
            parsed = _parse_index_results(
                page, query_kind, limit, max_price_cny
            )
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"{query_kind}: {type(exc).__name__}: {exc}")
            continue
        for item in parsed:
            link = item["best_available_link"]
            if link in seen_links:
                continue
            listings.append(item)
            seen_links.add(link)

    listings.sort(
        key=lambda item: (
            item["item_url"] is not None,
            item["displayed_price_cny"] is not None,
            item["freshness"] == "recent_index_entry",
        ),
        reverse=True,
    )
    listings = listings[:limit]
    direct_item_links = [
        {
            "title": item["title"],
            "item_url": item["item_url"],
            "item_id": item["item_id"],
        }
        for item in listings
        if item["item_url"]
    ]
    payload = {
        "platform": "xianyu",
        "platform_name": "闲鱼",
        "keyword": keyword,
        "status": "ok" if listings else ("index_unavailable" if errors else "no_results"),
        "observed_at": _now_iso(),
        "source": "360 Search public web index",
        "official_search_url": _official_search_link(keyword),
        "direct_item_links": direct_item_links,
        "listings": listings,
        "errors": errors,
        "cache_hit": False,
        "login_required": False,
        "verification_required": True,
        "note": (
            "闲鱼网页版商品列表由动态接口加载且可能触发登录或风控；本工具不绕过"
            "风控，只返回公开搜索索引中的信息。索引可能滞后、商品可能已售出，"
            "请在闲鱼商品页核对价格、成色、卖家和运费。"
        ),
    }
    _cache[cache_key] = (time.monotonic(), payload)
    return payload


server = FastMCP(
    SERVER_NAME,
    instructions=(
        "Login-free, read-only Xianyu product discovery from public web indexes. "
        "Return indexed listing prices, condition and transaction tags, source links, "
        "and the official Xianyu search URL. Never represent indexed values as live "
        "availability or checkout prices."
    ),
    stateless_http=True,
)

READ_ONLY_OPEN = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
READ_ONLY_LOCAL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@server.tool(
    name="search_xianyu",
    title="免登录搜索闲鱼商品",
    description=(
        "无需登录，搜索闲鱼公开商品索引，返回索引价格、成色、包邮/自提/验货宝/"
        "议价标签、可确认的商品链接、来源链接和闲鱼官方搜索入口。索引不是实时库存"
        "或最终成交价。"
    ),
    annotations=READ_ONLY_OPEN,
    structured_output=True,
)
async def search_xianyu(
    keyword: str,
    limit: int = 8,
    max_price_cny: float | None = None,
) -> dict[str, Any]:
    normalized = _validate_keyword(keyword)
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    if max_price_cny is not None and not 0 < max_price_cny <= 10_000_000:
        raise ValueError("max_price_cny must be greater than 0")
    return await _search(normalized, limit, max_price_cny)


@server.tool(
    name="build_xianyu_search_link",
    title="生成闲鱼官方搜索链接",
    description="为关键词生成闲鱼网页版官方搜索链接；不访问网络，也不需要登录。",
    annotations=READ_ONLY_LOCAL,
    structured_output=True,
)
def build_xianyu_search_link(keyword: str) -> dict[str, Any]:
    normalized = _validate_keyword(keyword)
    return {
        "platform": "xianyu",
        "platform_name": "闲鱼",
        "keyword": normalized,
        "login_required": False,
        "url": _official_search_link(normalized),
    }


@server.tool(
    name="parse_xianyu_item_link",
    title="解析闲鱼商品链接",
    description="识别闲鱼或旧版 2.taobao.com 商品链接并提取商品 ID；不访问网络。",
    annotations=READ_ONLY_LOCAL,
    structured_output=True,
)
def parse_xianyu_item_link(url: str) -> dict[str, Any]:
    original = url.strip()
    parsed = urlparse(original)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return {
            "recognized": False,
            "platform": None,
            "item_id": None,
            "canonical_url": None,
            "url": url,
            "reason": "A valid http(s) URL is required.",
        }
    hostname = parsed.hostname.lower()
    recognized_host = _hostname_matches(hostname, OFFICIAL_HOST_SUFFIXES)
    item_id = _extract_item_id(original) if recognized_host else None
    recognized = recognized_host and item_id is not None
    return {
        "recognized": recognized,
        "platform": "xianyu" if recognized else None,
        "platform_name": "闲鱼" if recognized else None,
        "item_id": item_id,
        "canonical_url": (
            f"https://www.goofish.com/item?id={item_id}" if recognized else None
        ),
        "url": original,
        "reason": None if recognized else "Unsupported host or missing numeric item ID.",
    }


async def _live_test(keyword: str) -> int:
    payload = await search_xianyu(keyword=keyword, limit=5)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["official_search_url"] else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Login-free Xianyu MCP")
    parser.add_argument(
        "--live-test",
        metavar="KEYWORD",
        help="Run a live public-index search instead of starting the MCP server.",
    )
    args = parser.parse_args()
    if args.live_test:
        raise SystemExit(asyncio.run(_live_test(args.live_test)))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
