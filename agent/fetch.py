"""Minimal stdlib HTTP fetch with charset handling and a descriptive UA."""

from __future__ import annotations

import gzip
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_UA = (
    "SupplyWatch/0.1 (+https://github.com/kaminariouji/kaminari-supplywatch; "
    "research agent; contact: kaminariouji@gmail.com)"
)


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300


def fetch(
    url: str,
    timeout: float = 20.0,
    user_agent: str = DEFAULT_UA,
) -> FetchResult:
    """Fetch a URL and decode the body to text. Never raises.

    URLs without a scheme are treated as local paths (useful for the
    reproducible demo mirrors).
    """
    if "://" not in url:
        path = Path(url).resolve()
        if not path.exists():
            return FetchResult(url=url, status=0, text="", error=f"file not found: {url}")
        url = path.as_uri()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except OSError:
                    pass
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                text = raw.decode(charset, errors="replace")
            except LookupError:
                text = raw.decode("utf-8", errors="replace")
            status = response.status or 200  # file:// responses carry status None
            return FetchResult(url=url, status=status, text=text)
    except urllib.error.HTTPError as exc:
        return FetchResult(url=url, status=exc.code, text="", error=f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 - defensive: fetch must never crash the scan
        return FetchResult(url=url, status=0, text="", error=f"{type(exc).__name__}: {exc}")
