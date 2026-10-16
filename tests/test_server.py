from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SERVER_PATH = Path(__file__).parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("xianyu_server", SERVER_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def result_page(url: str, title: str, snippet: str) -> str:
    return f'''<ul class="result"><li class="res-list">
    <h3 class="res-title"><a href="https://www.so.com/link?x=1"
    data-mdurl="{url}">{title}</a></h3>
    <p class="res-desc">{snippet}</p>
    </li></ul>'''


class XianyuMcpTests(unittest.TestCase):
    def test_builds_official_search_link(self) -> None:
        payload = MODULE.build_xianyu_search_link("静音 游戏鼠标")
        self.assertEqual(payload["platform"], "xianyu")
        self.assertIn("www.goofish.com/search?", payload["url"])
        self.assertIn("%E9%9D%99%E9%9F%B3", payload["url"])
        self.assertFalse(payload["login_required"])

    def test_parses_current_item_link(self) -> None:
        payload = MODULE.parse_xianyu_item_link(
            "https://www.goofish.com/item?id=987654321"
        )
        self.assertTrue(payload["recognized"])
        self.assertEqual(payload["item_id"], "987654321")
        self.assertEqual(
            payload["canonical_url"],
            "https://www.goofish.com/item?id=987654321",
        )

    def test_parses_legacy_item_link(self) -> None:
        payload = MODULE.parse_xianyu_item_link(
            "https://2.taobao.com/item.htm?id=123456789"
        )
        self.assertTrue(payload["recognized"])
        self.assertEqual(payload["item_id"], "123456789")

    def test_rejects_deceptive_hostname(self) -> None:
        payload = MODULE.parse_xianyu_item_link(
            "https://www.goofish.com.example.org/item?id=123"
        )
        self.assertFalse(payload["recognized"])

    def test_parses_price_condition_tags_and_direct_link(self) -> None:
        page = result_page(
            "https://www.goofish.com/item?id=987654321",
            "罗技 GPW 二代静音微动 - 闲鱼",
            (
                "2026年10月10日 个人自用99新，原价899元，"
                "闲鱼价499元，支持验货宝、包邮，可小刀。"
            ),
        )
        results = MODULE._parse_index_results(page, "direct", 5, 600)
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item["displayed_price_cny"], 499.0)
        self.assertEqual(item["original_or_purchase_price_cny"], 899.0)
        self.assertEqual(item["item_id"], "987654321")
        self.assertEqual(item["link_type"], "direct_item")
        self.assertIn("99新", item["condition_tags"])
        self.assertIn("个人自用", item["condition_tags"])
        self.assertIn("验货宝", item["transaction_tags"])
        self.assertIn("包邮", item["transaction_tags"])
        self.assertIn("可议价", item["transaction_tags"])
        self.assertTrue(item["matches_budget"])

    def test_specific_unopened_condition_suppresses_duplicate_generic_tags(self) -> None:
        self.assertEqual(MODULE._condition_labels("全新未拆封"), ["全新未拆"])

    def test_budget_excludes_known_over_budget_result(self) -> None:
        page = result_page(
            "https://www.goofish.com/item?id=1",
            "游戏鼠标 - 闲鱼",
            "闲鱼价699元",
        )
        self.assertEqual(
            MODULE._parse_index_results(page, "direct", 5, 500), []
        )

    def test_discovery_result_must_name_xianyu(self) -> None:
        page = result_page(
            "https://www.smzdm.com/p/1234/",
            "二手鼠标价格",
            "其他平台售价99元",
        )
        self.assertEqual(
            MODULE._parse_index_results(page, "discovery", 5), []
        )

    def test_public_source_can_supply_indexed_price(self) -> None:
        page = result_page(
            "https://www.smzdm.com/p/1234/",
            "闲鱼二手显卡行情",
            "2026年10月1日 闲鱼价1200元，支持同城自提。",
        )
        item = MODULE._parse_index_results(page, "discovery", 5)[0]
        self.assertEqual(item["displayed_price_cny"], 1200.0)
        self.assertIsNone(item["item_url"])
        self.assertEqual(item["link_type"], "public_source")
        self.assertIn("同城自提", item["transaction_tags"])

    def test_http_item_link_is_upgraded_to_https(self) -> None:
        page = result_page(
            "http://www.goofish.com/item?id=42",
            "闲鱼测试商品",
            "售价88元",
        )
        item = MODULE._parse_index_results(page, "direct", 5)[0]
        self.assertEqual(item["item_url"], "https://www.goofish.com/item?id=42")


if __name__ == "__main__":
    unittest.main()
