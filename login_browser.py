from __future__ import annotations

import argparse
import asyncio
import os

from playwright.async_api import async_playwright

from browser_engine import PLATFORM_KEYS, PLATFORMS, _profile_dir, normalize_platforms


async def login(platforms: list[str]) -> None:
    profile = _profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    channel = os.environ.get("ECOMMERCE_BROWSER_CHANNEL", "chrome").strip() or "chrome"
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile), channel=channel, headless=False,
            locale="zh-CN", viewport={"width": 1440, "height": 1000},
        )
        pages = context.pages
        first = pages[0] if pages else await context.new_page()
        for index, key in enumerate(platforms):
            page = first if index == 0 else await context.new_page()
            await page.goto(PLATFORMS[key].home_url, wait_until="domcontentloaded")
        names = "、".join(PLATFORMS[key].name for key in platforms)
        print(f"\n已打开专用 Chrome：{names}")
        print("请在窗口中手动登录并完成必要验证。不要把密码或验证码交给 MCP。")
        await asyncio.to_thread(input, "全部完成后回到此终端，按 Enter 保存并关闭浏览器：")
        await context.close()
    print(f"登录态已保存在：{profile}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create/update the dedicated local shopping browser profile"
    )
    parser.add_argument(
        "platforms", nargs="*", default=list(PLATFORM_KEYS),
        help="Any of: xianyu taobao jd pinduoduo (default: all)",
    )
    args = parser.parse_args()
    asyncio.run(login(normalize_platforms(args.platforms)))


if __name__ == "__main__":
    main()
