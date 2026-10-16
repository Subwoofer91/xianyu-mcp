from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
ENGINE_PATH = PROJECT_ROOT / "browser_engine.py"
SPEC = importlib.util.spec_from_file_location("browser_engine", ENGINE_PATH)
assert SPEC and SPEC.loader
ENGINE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ENGINE
SPEC.loader.exec_module(ENGINE)


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


if __name__ == "__main__":
    unittest.main()
