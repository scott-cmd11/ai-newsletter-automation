import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .verify import DEFAULT_HEADERS


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "aside"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse whitespace
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def fetch_article(url: str, timeout: int = 10) -> Optional[str]:
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return None
        if "text/html" not in resp.headers.get("Content-Type", ""):
            return None
        return resp.text
    except requests.RequestException:
        return None


def scrape(url: str, html: Optional[str] = None) -> Optional[str]:
    if html is None:
        html = fetch_article(url)
    if not html:
        return None
    text = extract_text(html)
    # Keep it reasonably bounded for LLM cost
    return text[:20_000]
