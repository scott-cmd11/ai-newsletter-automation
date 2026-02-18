import re
from typing import Iterable, Optional

import requests
from bs4 import BeautifulSoup

PAYWALL_PHRASES: Iterable[str] = (
    "subscribe to read",
    "log in to continue",
    "isaccessibleforfree\":false",
    "paywall",
    "subscriber-only",
    "subscription required",
    "already a subscriber",
)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AI-Newsletter/1.0; +https://example.com)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def is_paywalled(html: str) -> bool:
    haystack = html.lower()
    return any(phrase in haystack for phrase in PAYWALL_PHRASES)


def verify_link(url: str, timeout: int = 10) -> Optional[str]:
    """Fetch *url* and return the HTML if the page is reachable, is HTML,
    and is not behind a paywall.  Returns ``None`` on any failure so that
    the caller can reuse the already-fetched HTML instead of downloading
    the page a second time."""
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    content_type = resp.headers.get("Content-Type", "")
    if "text/html" not in content_type:
        return None

    text = resp.text
    sample = text[:100_000]
    if is_paywalled(sample):
        return None

    # Additional JSON-LD paywall flag
    soup = BeautifulSoup(sample, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            if "isAccessibleForFree" in script.text and '"isAccessibleForFree": false' in script.text.lower():
                return None
        except Exception:
            continue

    return text
