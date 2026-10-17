from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlparse
from urllib.request import ProxyHandler, Request, build_opener


DEFAULT_CDP_PORT = 9223
START_TIMEOUT_SECONDS = 15.0
FORBIDDEN_AUTOMATION_FLAGS = (
    "--enable-automation",
    "--remote-debugging-pipe",
    "--no-sandbox",
)


def profile_dir() -> Path:
    configured = os.environ.get("ECOMMERCE_BROWSER_PROFILE", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).with_name(".browser-profile")
    )


def cdp_port() -> int:
    raw = os.environ.get("ECOMMERCE_CDP_PORT", str(DEFAULT_CDP_PORT)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("ECOMMERCE_CDP_PORT must be an integer") from exc
    if not 1024 <= value <= 65535:
        raise ValueError("ECOMMERCE_CDP_PORT must be between 1024 and 65535")
    return value


def local_cdp_url() -> str:
    return f"http://127.0.0.1:{cdp_port()}"


def validate_loopback_cdp_url(url: str) -> str:
    normalized = url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ECOMMERCE_CDP_URL must be an http:// loopback address")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("ECOMMERCE_CDP_URL must not contain credentials, query or fragment")
    return normalized


def configured_cdp_url() -> str:
    configured = os.environ.get("ECOMMERCE_CDP_URL", "").strip()
    return validate_loopback_cdp_url(configured) if configured else local_cdp_url()


def find_chrome_executable() -> Path:
    configured = os.environ.get("ECOMMERCE_CHROME_EXECUTABLE", "").strip()
    candidates = [
        Path(configured) if configured else None,
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "Google Chrome was not found. Set ECOMMERCE_CHROME_EXECUTABLE to chrome.exe."
    )


def build_chrome_command(
    executable: Path,
    profile: Path,
    port: int,
    urls: list[str] | None = None,
) -> list[str]:
    command = [
        str(executable),
        f"--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    command.extend(urls or ["about:blank"])
    return command


def _no_proxy_opener() -> Any:
    return build_opener(ProxyHandler({}))


def read_cdp_version(url: str, timeout: float = 1.0) -> dict[str, Any] | None:
    endpoint = validate_loopback_cdp_url(url) + "/json/version"
    try:
        with _no_proxy_opener().open(endpoint, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not str(payload.get("Browser", "")).startswith("Chrome/"):
        return None
    return payload


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.4)
        return client.connect_ex(("127.0.0.1", port)) == 0


def open_cdp_tabs(cdp_url: str, urls: list[str]) -> None:
    base = validate_loopback_cdp_url(cdp_url)
    opener = _no_proxy_opener()
    for url in urls:
        request = Request(
            f"{base}/json/new?{quote(url, safe='')}",
            method="PUT",
        )
        with opener.open(request, timeout=3.0):
            pass


def _creation_flags() -> int:
    return (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
    )


def launch_plain_chrome(urls: list[str] | None = None) -> str:
    url = local_cdp_url()
    existing = read_cdp_version(url)
    if existing:
        if urls:
            open_cdp_tabs(url, urls)
        return url
    if _port_is_open(cdp_port()):
        raise RuntimeError(
            f"127.0.0.1:{cdp_port()} is already in use but is not a Chrome CDP endpoint"
        )

    profile = profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    command = build_chrome_command(
        find_chrome_executable(), profile, cdp_port(), urls
    )
    if any(flag in command for flag in FORBIDDEN_AUTOMATION_FLAGS):
        raise RuntimeError("Refusing to launch Chrome with automation-identifying flags")
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_creation_flags(),
        close_fds=True,
    )
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if read_cdp_version(url):
            return url
        time.sleep(0.2)
    raise RuntimeError(
        "Chrome started but its localhost CDP endpoint did not become ready within 15 seconds"
    )


def ensure_chrome_cdp() -> str:
    configured = os.environ.get("ECOMMERCE_CDP_URL", "").strip()
    if configured:
        url = validate_loopback_cdp_url(configured)
        if not read_cdp_version(url):
            raise RuntimeError(f"No Chrome CDP endpoint is available at {url}")
        return url
    return launch_plain_chrome()
