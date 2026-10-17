from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from local_chrome import configured_cdp_url, ensure_chrome_cdp, profile_dir


@dataclass(frozen=True)
class Platform:
    key: str
    name: str
    search_template: str
    home_url: str
    hosts: tuple[str, ...]
    item_selector: str


PLATFORMS: dict[str, Platform] = {
    "xianyu": Platform(
        "xianyu", "闲鱼", "https://www.goofish.com/search?q={query}",
        "https://www.goofish.com/", ("goofish.com", "2.taobao.com"),
        'a[href*="/item?"]',
    ),
    "taobao": Platform(
        "taobao", "淘宝/天猫", "https://s.taobao.com/search?q={query}",
        "https://www.taobao.com/", ("taobao.com", "tmall.com"),
        'a[href*="item.taobao.com/item.htm"], a[href*="detail.tmall.com/item.htm"]',
    ),
    "jd": Platform(
        "jd", "京东", "https://search.jd.com/Search?keyword={query}",
        "https://www.jd.com/", ("jd.com",), "[data-sku]",
    ),
    "pinduoduo": Platform(
        "pinduoduo", "拼多多",
        "https://mobile.yangkeduo.com/search_result.html?search_key={query}",
        "https://mobile.yangkeduo.com/", ("yangkeduo.com", "pinduoduo.com"),
        'img[src*="mms-goods-image"]',
    ),
}
PLATFORM_KEYS = tuple(PLATFORMS)

PRICE_PATTERN = re.compile(r"[¥￥]\s*(\d{1,8}(?:\.\d{1,2})?)")
FALLBACK_PRICE_PATTERN = re.compile(
    r"(?:到手价|券后价|现价|售价|价格)[^\d]{0,8}(\d{1,8}(?:\.\d{1,2})?)"
)
PROMOTION_WORDS = (
    "满减", "优惠券", "平台券", "店铺券", "券后", "优惠后", "到手价",
    "补贴", "百亿补贴", "直降", "立减", "秒杀", "限时", "包邮",
    "跨店", "以旧换新", "赠品", "验货宝", "可议价", "小刀",
)
LOGIN_MARKERS = (
    "请登录", "扫码登录", "账号登录", "短信登录", "登录后查看",
    "登录后继续", "立即登录", "登录/注册", "请使用手机闲鱼",
)
CHALLENGE_MARKERS = (
    "滑动验证", "安全验证", "访问过于频繁", "验证码", "异常访问", "访问异常",
    "当前页面异常", "系统检测到当前访问存在异常", "非法访问",
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def validate_keyword(keyword: str) -> str:
    normalized = " ".join(keyword.split())
    if not normalized:
        raise ValueError("keyword must not be empty")
    if len(normalized) > 120:
        raise ValueError("keyword must be at most 120 characters")
    return normalized


def normalize_platforms(platforms: list[str] | None) -> list[str]:
    requested = list(PLATFORM_KEYS) if platforms is None else platforms
    normalized: list[str] = []
    for value in requested:
        key = value.strip().lower()
        if key not in PLATFORMS:
            raise ValueError(
                f"unsupported platform {value!r}; choose from {', '.join(PLATFORM_KEYS)}"
            )
        if key not in normalized:
            normalized.append(key)
    if not normalized:
        raise ValueError("platforms must not be empty")
    return normalized


def build_search_link(platform: str, keyword: str) -> str:
    return PLATFORMS[platform].search_template.format(query=quote(keyword, safe=""))


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    host = host.lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


def identify_platform(url: str) -> str | None:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return None
    for key, config in PLATFORMS.items():
        if _host_matches(host, config.hosts):
            return key
    return None


def _numeric_query_value(parsed: Any, key: str) -> str | None:
    value = (parse_qs(parsed.query).get(key) or [None])[0]
    return value if value and re.fullmatch(r"\d+", value) else None


def parse_product_link(url: str) -> dict[str, Any]:
    original = url.strip()
    parsed = urlparse(original)
    platform = identify_platform(original)
    if not platform:
        return {
            "recognized": False, "platform": None, "item_id": None,
            "canonical_url": None, "url": original,
            "reason": "Only official Xianyu, Taobao/Tmall, JD and Pinduoduo product URLs are allowed.",
        }

    item_id: str | None = None
    canonical: str | None = None
    if platform == "xianyu":
        item_id = _numeric_query_value(parsed, "id")
        if item_id:
            canonical = f"https://www.goofish.com/item?id={item_id}"
    elif platform == "taobao":
        item_id = _numeric_query_value(parsed, "id")
        if item_id:
            host = "detail.tmall.com" if "tmall.com" in (parsed.hostname or "") else "item.taobao.com"
            canonical = f"https://{host}/item.htm?id={item_id}"
    elif platform == "jd":
        match = re.search(r"/(\d+)\.html(?:[?#]|$)", original)
        item_id = match.group(1) if match else None
        if item_id:
            canonical = f"https://item.jd.com/{item_id}.html"
    elif platform == "pinduoduo":
        item_id = _numeric_query_value(parsed, "goods_id")
        if item_id:
            canonical = f"https://mobile.yangkeduo.com/goods.html?goods_id={item_id}"

    return {
        "recognized": canonical is not None,
        "platform": platform if canonical else None,
        "platform_name": PLATFORMS[platform].name if canonical else None,
        "item_id": item_id,
        "canonical_url": canonical,
        "url": original,
        "reason": None if canonical else "The URL is on a supported host but is not a recognized product page.",
    }


def extract_price(text: str) -> float | None:
    for pattern in (PRICE_PATTERN, FALLBACK_PRICE_PATTERN):
        match = pattern.search(text)
        if match:
            value = float(match.group(1))
            if 0 < value < 10_000_000:
                return value
    return None


def extract_prices(text: str) -> list[float]:
    values: list[float] = []
    for pattern in (FALLBACK_PRICE_PATTERN, PRICE_PATTERN):
        for match in pattern.finditer(text):
            value = float(match.group(1))
            if 0 < value < 10_000_000 and value not in values:
                values.append(value)
    return values


def extract_promotions(text: str) -> list[str]:
    return [word for word in PROMOTION_WORDS if word in text]


def normalize_card_text(text: str) -> str:
    cleaned = " ".join(text.split())
    return re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", cleaned)


def extract_detail_price(platform: str, price_contexts: list[str], visible_text: str) -> float | None:
    normalized_contexts = [normalize_card_text(value) for value in price_contexts]
    if platform == "pinduoduo":
        if re.search(r"[¥￥]\s*[\d?]*\?", visible_text):
            return None
        return extract_price(visible_text) or extract_price(" ".join(normalized_contexts))
    if platform == "xianyu":
        for context in normalized_contexts:
            if re.fullmatch(r"\d{1,8}(?:\.\d{1,2})?", context):
                value = float(context)
                if 0 < value < 10_000_000:
                    return value
    return extract_price(" ".join(normalized_contexts)) or extract_price(visible_text)


def listing_from_dom(platform: str, href: str, text: str, images: list[str]) -> dict[str, Any] | None:
    parsed = parse_product_link(href)
    if not parsed["recognized"]:
        return None
    cleaned = normalize_card_text(text)
    if not cleaned:
        return None
    price = extract_price(cleaned)
    clean_images: list[str] = []
    for image in images:
        value = "https:" + image if image.startswith("//") else image
        if value.startswith("http") and value not in clean_images:
            clean_images.append(value)
    return {
        "platform": platform,
        "platform_name": PLATFORMS[platform].name,
        "item_id": parsed["item_id"],
        "title_or_description": cleaned[:1200],
        "displayed_price_cny": price,
        "all_displayed_prices_cny": extract_prices(cleaned),
        "price_kind": "logged_in_search_card_price" if price is not None else "unavailable",
        "general_promotions": extract_promotions(cleaned),
        "item_url": parsed["canonical_url"],
        "source_url": href,
        "main_image_url": clean_images[0] if clean_images else None,
        "image_urls": clean_images[:8],
        "observed_at": _now_iso(),
        "verification_required": True,
    }


class BrowserController:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._browser: Browser | None = None

    async def context(self) -> BrowserContext:
        async with self._lock:
            if self._context is not None:
                return self._context
            self._playwright = await async_playwright().start()
            cdp_url = await asyncio.to_thread(ensure_chrome_cdp)
            self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            if not self._browser.contexts:
                raise RuntimeError("The CDP browser has no usable context.")
            self._context = self._browser.contexts[0]
            return self._context

    async def new_page(self) -> Page:
        return await (await self.context()).new_page()

    async def close(self) -> None:
        async with self._lock:
            if self._playwright is not None:
                await self._playwright.stop()
            self._context = None
            self._browser = None
            self._playwright = None


_controller = BrowserController()


async def close_browser_session() -> None:
    await _controller.close()


async def _page_state(page: Page) -> tuple[str, str]:
    text = await page.locator("body").inner_text(timeout=10_000)
    sample = text[:20_000]
    if any(marker in sample for marker in CHALLENGE_MARKERS):
        return "challenge_required", sample
    if any(marker in sample for marker in LOGIN_MARKERS):
        return "login_required", sample
    return "ok", sample


async def browser_status_payload() -> dict[str, Any]:
    context = await _controller.context()
    statuses = []
    for key, platform in PLATFORMS.items():
        cookies = await context.cookies([platform.home_url])
        statuses.append({
            "platform": key,
            "platform_name": platform.name,
            "cookie_count": len(cookies),
            "session_cookie_present": bool(cookies),
        })
    return {
        "status": "ok",
        "connection_mode": "plain_chrome_loopback_cdp",
        "profile_dir": str(profile_dir()),
        "cdp_url": configured_cdp_url(),
        "platforms": statuses,
        "note": (
            "Cookie values are never returned. Presence is not proof that a session is still valid. "
            "The CDP endpoint is restricted to this computer's loopback interface."
        ),
    }


async def _goto(page: Page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=35_000)
    await page.wait_for_timeout(2_000)


async def _wait_for_cards(page: Page, selector: str) -> None:
    try:
        await page.locator(selector).first.wait_for(state="attached", timeout=8_000)
    except Exception:
        # Some marketplace pages legitimately return an empty result set. State and
        # extraction below distinguish that from login and challenge pages.
        pass


async def _extract_raw_items(page: Page, platform_key: str) -> list[dict[str, Any]]:
    if platform_key == "pinduoduo":
        return await page.evaluate(
            """() => {
                const seenCards = new Set();
                const cards = Array.from(document.images)
                    .filter((img) => /mms-goods-image/.test(img.currentSrc || img.src || ''))
                    .map((img) => img.closest('[data-uniqid]'))
                    .filter((card) => card && !seenCards.has(card) && seenCards.add(card));
                const findGoods = (root) => {
                    const stack = [root];
                    const seen = new WeakSet();
                    let visited = 0;
                    while (stack.length && visited++ < 300) {
                        const value = stack.pop();
                        if (!value || typeof value !== 'object' || seen.has(value)) continue;
                        seen.add(value);
                        if ((value.goodsID || value.goodsId || value.goods_id) &&
                            (value.goodsName || value.title || value.name)) return value;
                        for (const child of Object.values(value)) {
                            if (child && typeof child === 'object') stack.push(child);
                        }
                    }
                    return null;
                };
                return cards.slice(0, 120).map((card) => {
                    let goods = null;
                    for (const key of Object.getOwnPropertyNames(card)) {
                        if (key.startsWith('__reactProps$') || key.startsWith('__reactFiber$')) {
                            goods = findGoods(card[key]);
                            if (goods) break;
                        }
                    }
                    if (!goods) return null;
                    const id = goods.goodsID || goods.goodsId || goods.goods_id;
                    const price = goods.priceInfo ||
                        (Number.isFinite(Number(goods.price)) ? String(Number(goods.price) / 100) : '');
                    const text = [
                        goods.goodsName || goods.title || goods.name,
                        goods.pricePrefixInfo || goods.priceDesc,
                        price ? `¥${price}` : '',
                        goods.salesTip,
                        card.innerText || card.textContent || '',
                    ].filter(Boolean).join(' ');
                    const image = goods.imgUrl || goods.imageUrl ||
                        card.querySelector('img')?.currentSrc || card.querySelector('img')?.src || '';
                    return {
                        href: `https://mobile.yangkeduo.com/goods.html?goods_id=${id}`,
                        text,
                        images: image ? [image] : [],
                    };
                }).filter(Boolean);
            }"""
        )

    if platform_key == "jd":
        return await page.locator(PLATFORMS[platform_key].item_selector).evaluate_all(
            """(cards) => cards.slice(0, 120).map((card) => {
                const sku = card.getAttribute('data-sku');
                const images = Array.from(card.querySelectorAll('img'))
                    .map((img) => img.currentSrc || img.src || img.getAttribute('data-lazy-img') || '')
                    .filter(Boolean);
                return {
                    href: sku ? `https://item.jd.com/${sku}.html` : '',
                    text: (card.innerText || card.textContent || '').trim(),
                    images,
                };
            })"""
        )

    return await page.locator(PLATFORMS[platform_key].item_selector).evaluate_all(
        """(anchors) => anchors.slice(0, 120).map((anchor) => ({
            href: anchor.href,
            text: (anchor.innerText || anchor.textContent || anchor.getAttribute('aria-label') || '').trim(),
            images: Array.from(anchor.querySelectorAll('img'))
                .map((img) => img.currentSrc || img.src || img.getAttribute('data-src') || '')
                .filter(Boolean),
        }))"""
    )


async def search_platform(
    keyword: str, platform_key: str, limit: int, max_price_cny: float | None,
) -> dict[str, Any]:
    config = PLATFORMS[platform_key]
    search_url = build_search_link(platform_key, keyword)
    page: Page | None = None
    try:
        page = await _controller.new_page()
        await _goto(page, search_url)
        await _wait_for_cards(page, config.item_selector)
        status, body_text = await _page_state(page)
        if status != "ok":
            return {
                "platform": platform_key, "platform_name": config.name,
                "status": status, "search_url": page.url, "listings": [],
                "note": "Run login_browser.py and complete any verification in the dedicated Chrome profile.",
            }
        raw_items = await _extract_raw_items(page, platform_key)
        listings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_items:
            item = listing_from_dom(
                platform_key, str(raw.get("href", "")), str(raw.get("text", "")),
                [str(value) for value in raw.get("images", [])],
            )
            if not item or item["item_url"] in seen:
                continue
            if max_price_cny is not None and item["displayed_price_cny"] is not None and item["displayed_price_cny"] > max_price_cny:
                continue
            item["matches_budget"] = (
                None if max_price_cny is None or item["displayed_price_cny"] is None
                else item["displayed_price_cny"] <= max_price_cny
            )
            listings.append(item)
            seen.add(item["item_url"])
            if len(listings) >= limit:
                break
        return {
            "platform": platform_key, "platform_name": config.name,
            "status": "ok" if listings else "no_results", "search_url": page.url,
            "observed_at": _now_iso(), "listings": listings,
            "note": None if listings else "The page loaded but no recognized official product cards were found.",
        }
    except Exception as exc:
        return {
            "platform": platform_key, "platform_name": config.name,
            "status": "browser_error", "search_url": search_url, "listings": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if page is not None:
            await page.close()


async def search_many(
    keyword: str, platforms: list[str], limit: int, max_price_cny: float | None,
) -> dict[str, Any]:
    results = []
    for platform in platforms:
        results.append(await search_platform(keyword, platform, limit, max_price_cny))
    return {
        "keyword": keyword, "login_required": True, "browser_required": True,
        "observed_at": _now_iso(), "platforms": results,
        "note": (
            "Prices and promotions are observations from the current logged-in page and selected card/SKU. "
            "They may change by account, region, variant, coupon eligibility and checkout time."
        ),
    }


async def inspect_product_page(url: str, max_images: int) -> dict[str, Any]:
    parsed = parse_product_link(url)
    platform = parsed["platform"]
    page: Page | None = None
    try:
        page = await _controller.new_page()
        await _goto(page, parsed["canonical_url"])
        status, body_text = await _page_state(page)
        data = await page.evaluate(
            """() => {
                const meta = (name) => document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.content || '';
                const heading = document.querySelector('h1')?.innerText || '';
                const pddTitle = Array.from(document.querySelectorAll('span')).map((el) => ({
                    text: (el.innerText || el.textContent || '').trim(),
                    top: el.getBoundingClientRect().top,
                    visible: getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden',
                })).filter((item) => item.visible && item.top >= 0 && item.text.length >= 15 &&
                    item.text.length <= 300 && !item.text.includes('\\n') &&
                    !/退货包运费|无门槛|恢复原价|拼单即将结束|不满意包|^已选[：:]/.test(item.text))
                    .sort((a, b) => a.top - b.top)[0]?.text || '';
                const images = Array.from(document.images).filter((img) => img.naturalWidth >= 160 && img.naturalHeight >= 120).map((img) => img.currentSrc || img.src).filter(Boolean);
                const priceTexts = Array.from(document.querySelectorAll('[data-price], [class*="price" i]')).filter((el) => {
                    const style = getComputedStyle(el);
                    return style.visibility !== 'hidden' && style.display !== 'none';
                }).map((el) => `${el.getAttribute('data-price') || ''} ${el.innerText || el.textContent || ''}`.trim()).filter(Boolean).slice(0, 100);
                const selectedOptions = Array.from(document.querySelectorAll('[aria-selected="true"], [aria-checked="true"], [class*="selected" i], [class*="checked" i]')).map((el) => (el.innerText || el.textContent || '').trim()).filter((text) => text && text.length <= 120).slice(0, 50);
                return {title: meta('og:title') || meta('twitter:title') || document.title || heading,
                    pddTitle,
                    description: meta('og:description') || meta('description'),
                    ogImage: meta('og:image'), images, priceTexts, selectedOptions,
                    visibleText: ((document.querySelector('main, [role="main"]') || document.body)?.innerText || '').slice(0, 20000)};
            }"""
        )
        visible_text = " ".join(str(data.get("visibleText", body_text)).split())
        images: list[str] = []
        for image in [data.get("ogImage"), *data.get("images", [])]:
            if not image:
                continue
            value = "https:" + image if str(image).startswith("//") else str(image)
            if value.startswith("http") and value not in images:
                images.append(value)
            if len(images) >= max_images:
                break
        price_contexts = [" ".join(str(value).split()) for value in data.get("priceTexts", [])]
        title = normalize_card_text(str(data.get("title", "")))[:500]
        if platform == "pinduoduo" and title in {"拼多多", "拼多多商城"}:
            title = normalize_card_text(str(data.get("pddTitle", "")))[:500] or title
        displayed_price = extract_detail_price(platform, price_contexts, visible_text)
        return {
            "platform": platform, "platform_name": PLATFORMS[platform].name,
            "status": status, "item_id": parsed["item_id"],
            "item_url": parsed["canonical_url"], "final_url": page.url,
            "title": title,
            "description": " ".join(str(data.get("description", "")).split())[:2000],
            "displayed_price_cny": displayed_price,
            "all_price_candidates_cny": extract_prices(" ".join(price_contexts)),
            "price_contexts": price_contexts[:30],
            "price_kind": (
                "logged_in_product_page_observation"
                if displayed_price is not None else "masked_or_unavailable"
            ),
            "general_promotions": extract_promotions(visible_text),
            "image_urls": images,
            "selected_options_or_states": list(dict.fromkeys(data.get("selectedOptions", [])))[:30],
            "page_text_excerpt": visible_text[:6000],
            "observed_at": _now_iso(), "verification_required": True,
        }
    finally:
        if page is not None:
            await page.close()
