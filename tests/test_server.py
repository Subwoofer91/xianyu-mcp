from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
ENGINE_PATH = PROJECT_ROOT / "browser_engine.py"
SPEC = importlib.util.spec_from_file_location("browser_engine", ENGINE_PATH)
assert SPEC and SPEC.loader
ENGINE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ENGINE
SPEC.loader.exec_module(ENGINE)

import local_chrome


class BrowserEngineTests(unittest.TestCase):
    def test_builds_all_search_links(self) -> None:
        for platform in ENGINE.PLATFORM_KEYS:
            url = ENGINE.build_search_link(platform, "静音 游戏鼠标")
            self.assertTrue(url.startswith("https://"))
            self.assertIn("%E9%9D%99%E9%9F%B3", url)

    def test_normalizes_platforms_and_rejects_unknown(self) -> None:
        self.assertEqual(ENGINE.normalize_platforms(["JD", "jd"]), ["jd"])
        with self.assertRaises(ValueError):
            ENGINE.normalize_platforms(["amazon"])

    def test_parses_xianyu_link(self) -> None:
        result = ENGINE.parse_product_link(
            "https://www.goofish.com/item?spm=test&id=1090520463460"
        )
        self.assertTrue(result["recognized"])
        self.assertEqual(result["platform"], "xianyu")
        self.assertEqual(
            result["canonical_url"],
            "https://www.goofish.com/item?id=1090520463460",
        )

    def test_parses_taobao_tmall_jd_and_pinduoduo(self) -> None:
        cases = {
            "https://item.taobao.com/item.htm?id=123": ("taobao", "123"),
            "https://detail.tmall.com/item.htm?id=456": ("taobao", "456"),
            "https://item.jd.com/789.html?utm=x": ("jd", "789"),
            "https://mobile.yangkeduo.com/goods.html?goods_id=321&page=1": (
                "pinduoduo", "321"
            ),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                result = ENGINE.parse_product_link(url)
                self.assertTrue(result["recognized"])
                self.assertEqual((result["platform"], result["item_id"]), expected)

    def test_rejects_deceptive_hostname_and_non_product_page(self) -> None:
        self.assertFalse(
            ENGINE.parse_product_link(
                "https://item.jd.com.example.org/123.html"
            )["recognized"]
        )
        self.assertFalse(
            ENGINE.parse_product_link("https://www.jd.com/")["recognized"]
        )

    def test_extracts_price_promotions_images_and_canonical_link(self) -> None:
        listing = ENGINE.listing_from_dom(
            "xianyu",
            "https://www.goofish.com/item?spm=track&id=1090520463460",
            "iPhone 13 mini 国行原装 电池90% ¥ 1709 包邮 支持验货宝",
            ["//img.alicdn.com/main.webp", "https://img.alicdn.com/second.webp"],
        )
        assert listing
        self.assertEqual(listing["displayed_price_cny"], 1709.0)
        self.assertIn("包邮", listing["general_promotions"])
        self.assertIn("验货宝", listing["general_promotions"])
        self.assertEqual(
            listing["item_url"],
            "https://www.goofish.com/item?id=1090520463460",
        )
        self.assertEqual(
            listing["main_image_url"],
            "https://img.alicdn.com/main.webp",
        )

    def test_fallback_price_text(self) -> None:
        self.assertEqual(ENGINE.extract_price("券后价 88.20 元"), 88.2)

    def test_normalizes_prices_split_across_dom_nodes(self) -> None:
        text = ENGINE.normalize_card_text("券后\n¥\n954\n.2\n已拼63件")
        self.assertEqual(text, "券后 ¥ 954.2 已拼63件")
        self.assertEqual(ENGINE.extract_price(text), 954.2)

    def test_xianyu_detail_prefers_primary_bare_price_context(self) -> None:
        price = ENGINE.extract_detail_price(
            "xianyu",
            ["899", "¥ 1 .10 累计降价82%", "¥ 1 .25"],
            "担保交易 ¥ 899 商品介绍",
        )
        self.assertEqual(price, 899.0)

    def test_pinduoduo_detail_prefers_visible_primary_price(self) -> None:
        price = ENGINE.extract_detail_price(
            "pinduoduo",
            ["¥1 无关推荐"],
            "1/6 券后 ¥ 1399 已优惠50元",
        )
        self.assertEqual(price, 1399.0)
        self.assertIsNone(
            ENGINE.extract_detail_price(
                "pinduoduo", [], "券后 ¥ 1??9.?? 前往APP查看价格"
            )
        )

    def test_builds_pinduoduo_listing_from_runtime_card(self) -> None:
        listing = ENGINE.listing_from_dom(
            "pinduoduo",
            "https://mobile.yangkeduo.com/goods.html?goods_id=994562895078",
            "Apple/苹果13mini 券后 ¥1399 已拼84件 顺丰包邮",
            ["https://img.pddpic.com/example.jpeg"],
        )
        assert listing
        self.assertEqual(listing["item_id"], "994562895078")
        self.assertEqual(listing["displayed_price_cny"], 1399.0)
        self.assertIn("包邮", listing["general_promotions"])

    def test_plain_chrome_command_has_loopback_cdp_without_automation_flags(self) -> None:
        profile = Path("C:/shopping-profile")
        command = local_chrome.build_chrome_command(
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            profile,
            9223,
            ["https://www.jd.com/"],
        )
        self.assertIn("--remote-debugging-address=127.0.0.1", command)
        self.assertIn("--remote-debugging-port=9223", command)
        self.assertIn(f"--user-data-dir={profile}", command)
        for flag in local_chrome.FORBIDDEN_AUTOMATION_FLAGS:
            self.assertNotIn(flag, command)

    def test_cdp_url_must_be_loopback(self) -> None:
        self.assertEqual(
            local_chrome.validate_loopback_cdp_url("http://localhost:9223/"),
            "http://localhost:9223",
        )
        with self.assertRaises(ValueError):
            local_chrome.validate_loopback_cdp_url("http://192.168.1.50:9223")
        with self.assertRaises(ValueError):
            local_chrome.validate_loopback_cdp_url("https://127.0.0.1:9223")

    def test_cdp_port_validation(self) -> None:
        with patch.dict("os.environ", {"ECOMMERCE_CDP_PORT": "9224"}):
            self.assertEqual(local_chrome.cdp_port(), 9224)
        with patch.dict("os.environ", {"ECOMMERCE_CDP_PORT": "80"}):
            with self.assertRaises(ValueError):
                local_chrome.cdp_port()


if __name__ == "__main__":
    unittest.main()
