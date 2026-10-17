from __future__ import annotations

import argparse

from browser_engine import PLATFORM_KEYS, PLATFORMS, normalize_platforms
from local_chrome import launch_plain_chrome, profile_dir


def login(platforms: list[str]) -> None:
    urls = [PLATFORMS[key].home_url for key in platforms]
    cdp_url = launch_plain_chrome(urls)
    names = "、".join(PLATFORMS[key].name for key in platforms)
    print(f"已用普通 Chrome 打开：{names}")
    print("请在窗口中手动登录并完成必要验证，然后保持该 Chrome 运行。")
    print("密码、短信验证码和扫码过程不会进入 MCP。")
    print(f"登录态目录：{profile_dir()}")
    print(f"仅本机调试地址：{cdp_url}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Open the dedicated normal Chrome profile for marketplace login"
    )
    parser.add_argument(
        "platforms", nargs="*", default=list(PLATFORM_KEYS),
        help="Any of: xianyu taobao jd pinduoduo (default: all)",
    )
    args = parser.parse_args()
    login(normalize_platforms(args.platforms))


if __name__ == "__main__":
    main()
