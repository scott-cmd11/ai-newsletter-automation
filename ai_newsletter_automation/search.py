from dataclasses import replace
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Iterable

import requests
import feedparser
from urllib.parse import urlparse, urlunparse

from .config import get_settings
from .models import ArticleHit, SectionConfig


DEFAULT_STREAMS: Dict[str, SectionConfig] = {
    "trending": SectionConfig(
        name="Trending AI",
        query="AI artificial intelligence top news of the week",
        limit=8,
    ),
    "canadian": SectionConfig(
        name="Canadian News",
        query='"Artificial Intelligence" AND (Canada OR "federal government" OR "public service")',
        limit=5,
    ),
    "global": SectionConfig(
        name="Global News",
        query='("AI" OR "Artificial Intelligence") AND (release OR policy OR workforce)',
        limit=5,
    ),
    "events": SectionConfig(
        name="Events",
        query='AI webinar OR AI conference OR "artificial intelligence" talk',
        limit=4,
        require_date=True,
    ),
    "events_public": SectionConfig(
        name="Public-Servant Events",
        query='"artificial intelligence" training OR webinar site:csps-efpc.gc.ca',
        limit=4,
        require_date=True,
        days=30,
    ),
    "agri": SectionConfig(
        name="Grain / Agri-Tech",
        query='("Machine Learning" OR "AI") AND ("Grain Quality" OR Agriculture)',
        limit=3,
    ),
    "ai_progress": SectionConfig(
        name="AI Progress",
        query="AI benchmark results",
        limit=3,
        days=30,
    ),
    "research_plain": SectionConfig(
        name="Plain-Language Research",
        query="arXiv AI",
        limit=3,
    ),
    "deep_dive": SectionConfig(
        name="Deep Dive",
        query='(OECD OR Anthropic OR MIT OR METR) AND ("AI" OR "Artificial Intelligence") report',
        limit=2,
    ),
}


def _since_timestamp(days: int) -> str:
    cutoff = datetime.utcnow() - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%d")


def search_stream(section: SectionConfig, days: int) -> List[ArticleHit]:
    settings = get_settings()
    max_results = settings.max_per_stream or section.limit * 2

    payload = {
        "query": section.query,
        "api_key": settings.tavily_api_key,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_domains": None,
        "days": days,
    }

    resp = requests.post("https://api.tavily.com/search", json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    hits: List[ArticleHit] = []
    for res in data.get("results", []):
        hits.append(
            ArticleHit(
                title=res.get("title", "").strip(),
                url=res.get("url", ""),
                snippet=res.get("content", "").strip(),
                source=res.get("source"),
                published=res.get("published_date"),
            )
        )
    return hits


# ---------- Helpers ----------


def _normalize_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        cleaned = parsed._replace(query="", fragment="")
        return urlunparse(cleaned)
    except Exception:
        return url


def _dedupe(hits: List[ArticleHit]) -> List[ArticleHit]:
    seen = set()
    unique = []
    for h in hits:
        key = (_normalize_url(h.url).lower(), h.title.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
    return unique


# ---------- Trending Collectors ----------


HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
AI_KEYWORDS = ("ai", "artificial", "llm", "model", "gpt", "transformer")


def fetch_hn_trending(limit: int = 30) -> List[ArticleHit]:
    try:
        top_ids = requests.get(f"{HN_API_BASE}/topstories.json", timeout=10).json()[: limit * 2]
        best_ids = requests.get(f"{HN_API_BASE}/beststories.json", timeout=10).json()[: limit]
        ids = list(dict.fromkeys(top_ids + best_ids))
    except Exception:
        return []

    hits: List[ArticleHit] = []
    for story_id in ids:
        try:
            item = requests.get(f"{HN_API_BASE}/item/{story_id}.json", timeout=5).json()
            title = item.get("title", "")
            if not title or not any(k in title.lower() for k in AI_KEYWORDS):
                continue
            url = item.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
            hits.append(ArticleHit(title=title, url=url, snippet="Hacker News trending"))
            if len(hits) >= limit:
                break
        except Exception:
            continue
    return hits


def fetch_producthunt_trending(limit: int = 10) -> List[ArticleHit]:
    rss_url = "https://www.producthunt.com/feeds/topic/artificial-intelligence"
    try:
        feed = feedparser.parse(rss_url)
    except Exception:
        return []
    hits: List[ArticleHit] = []
    for entry in feed.entries[:limit]:
        hits.append(
            ArticleHit(
                title=entry.get("title", ""),
                url=entry.get("link", ""),
                snippet="Product Hunt AI launch",
            )
        )
    return hits


CURATED_FEEDS = [
    "https://openai.com/blog/rss",
    "https://www.anthropic.com/news/rss",
    "https://deepmind.google/discover/rss.xml",
    "https://ai.facebook.com/blog/rss",
    "https://ai.googleblog.com/atom.xml",
    "https://www.microsoft.com/en-us/research/feed/",
    "https://www.oecd.ai/feed",
    "https://www.govtech.com/rss",
    "https://hai.stanford.edu/rss.xml",
]


def fetch_curated_feeds(limit: int = 10, days: int = 7) -> List[ArticleHit]:
    hits: List[ArticleHit] = []
    cutoff = datetime.utcnow() - timedelta(days=days)
    for url in CURATED_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for entry in feed.entries:
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                pub_dt = datetime(*published[:6])
                if pub_dt < cutoff:
                    continue
            hits.append(
                ArticleHit(
                    title=entry.get("title", ""),
                    url=entry.get("link", ""),
                    snippet=entry.get("summary", "")[:500],
                    published=entry.get("published"),
                )
            )
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    return hits


def collect_trending(days: int) -> List[ArticleHit]:
    hits: List[ArticleHit] = []
    hits.extend(fetch_hn_trending(limit=20))
    hits.extend(fetch_producthunt_trending(limit=10))
    hits.extend(fetch_curated_feeds(limit=15, days=days))
    # Tavily fallback
    trending_cfg = SectionConfig(name="Trending AI", query='"AI" AND ("top news" OR trending) AND week', limit=8)
    hits.extend(search_stream(trending_cfg, days))
    return _dedupe(hits)


# ---------- Events Public (CSPS) ----------


CSPS_DOMAINS = ["csps-efpc.gc.ca", "canada.ca"]


def collect_events_public(days: int) -> List[ArticleHit]:
    settings = get_settings()
    payload = {
        "query": '"artificial intelligence" AND (webinar OR event OR course OR training) site:csps-efpc.gc.ca',
        "api_key": settings.tavily_api_key,
        "max_results": 20,
        "search_depth": "advanced",
        "include_domains": CSPS_DOMAINS,
        "days": days,
    }
    try:
        resp = requests.post("https://api.tavily.com/search", json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    hits: List[ArticleHit] = []
    for res in data.get("results", []):
        hits.append(
            ArticleHit(
                title=res.get("title", "").strip(),
                url=res.get("url", ""),
                snippet=res.get("content", "").strip(),
                source=res.get("source"),
                published=res.get("published_date"),
            )
        )
    return _dedupe(hits)


# ---------- Research Plain ----------


def collect_research(days: int) -> List[ArticleHit]:
    # arXiv recent AI/ML
    max_results = 25
    query = "cat:cs.AI+OR+cat:cs.LG+OR+cat:stat.ML"
    url = f"http://export.arxiv.org/api/query?search_query={query}&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    cutoff = datetime.utcnow() - timedelta(days=days)
    hits: List[ArticleHit] = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            published = entry.get("published_parsed")
            if published:
                pub_dt = datetime(*published[:6])
                if pub_dt < cutoff:
                    continue
            hits.append(
                ArticleHit(
                    title=entry.get("title", "").replace("\n", " ").strip(),
                    url=entry.get("link", ""),
                    snippet=entry.get("summary", "").replace("\n", " ")[:600],
                    published=entry.get("published"),
                    source="arXiv",
                )
            )
            if len(hits) >= 12:
                break
    except Exception:
        pass
    return _dedupe(hits)


# ---------- AI Progress ----------


def _fetch_pwc_trending(limit: int = 10) -> List[ArticleHit]:
    rss_url = "https://paperswithcode.com/trending?format=rss"
    try:
        feed = feedparser.parse(rss_url)
    except Exception:
        return []
    hits: List[ArticleHit] = []
    for entry in feed.entries[:limit]:
        hits.append(
            ArticleHit(
                title=entry.get("title", ""),
                url=entry.get("link", ""),
                snippet=entry.get("summary", "")[:400],
                source="PapersWithCode",
            )
        )
    return hits


def collect_ai_progress(days: int) -> List[ArticleHit]:
    hits: List[ArticleHit] = []
    hits.extend(_fetch_pwc_trending(limit=15))
    # Future: add LMSYS/MLPerf/Epoch if reachable; safe to keep minimal for now.
    return _dedupe(hits)



def get_streams(custom_limits: int | None = None) -> Dict[str, SectionConfig]:
    streams: Dict[str, SectionConfig] = {}
    for key, cfg in DEFAULT_STREAMS.items():
        new_cfg = replace(cfg)
        if custom_limits:
            new_cfg.limit = custom_limits
        streams[key] = new_cfg
    return streams
