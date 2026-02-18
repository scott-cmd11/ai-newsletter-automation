from dataclasses import replace
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Iterable

import requests
import feedparser
from urllib.parse import urlparse, urlunparse

from .config import get_settings
from .models import ArticleHit, SectionConfig


# ── Domain blocklist — evergreen / non-news pages that pollute results ──

BLOCKED_DOMAINS = {
    "en.wikipedia.org",
    "wikipedia.org",
    "investopedia.com",
    "techopedia.com",
    "builtin.com",
    "coursera.org",
    "udemy.com",
    "medium.com",       # often paywalled or generic
    "quora.com",
}

BLOCKED_URL_PATTERNS = (
    "/wiki/",
    "/about",
    "/contact",
    "/careers",
    "/privacy",
    "/terms",
)


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


# ── Helpers ──


def _since_timestamp(days: int) -> str:
    cutoff = datetime.utcnow() - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%d")


def _is_blocked_url(url: str) -> bool:
    """Return True if the URL belongs to a blocked domain or matches a blocked pattern."""
    try:
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        if any(domain.endswith(b) for b in BLOCKED_DOMAINS):
            return True
        path = parsed.path.lower()
        if any(p in path for p in BLOCKED_URL_PATTERNS):
            return True
    except Exception:
        pass
    return False


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


def _parse_date_str(date_str: Optional[str]) -> Optional[datetime]:
    """Best-effort parse of a date string into a datetime object."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(date_str.strip()[:25], fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _filter_by_date(hits: List[ArticleHit], days: int) -> List[ArticleHit]:
    """Remove hits whose published date is outside the search window.
    Hits with no parseable date are kept (benefit of doubt)."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    filtered = []
    for h in hits:
        pub = _parse_date_str(h.published)
        if pub is not None and pub < cutoff:
            continue
        filtered.append(h)
    return filtered


def _filter_blocked(hits: List[ArticleHit]) -> List[ArticleHit]:
    """Remove hits from blocked domains or URL patterns."""
    return [h for h in hits if not _is_blocked_url(h.url)]


# ── Tavily search ──


def search_stream(section: SectionConfig, days: int) -> List[ArticleHit]:
    settings = get_settings()
    max_results = settings.max_per_stream or section.limit * 3

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

    # Post-filter: date + blocked domains
    hits = _filter_blocked(hits)
    hits = _filter_by_date(hits, days)
    return hits


# ── Trending Collectors ──


HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
AI_KEYWORDS = ("ai", "artificial", "llm", "model", "gpt", "transformer", "openai", "anthropic", "gemini")


def fetch_hn_trending(limit: int = 30, days: int = 7) -> List[ArticleHit]:
    cutoff_ts = (datetime.utcnow() - timedelta(days=days)).timestamp()
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
            # Date filter: reject items older than the search window
            item_time = item.get("time", 0)
            if item_time < cutoff_ts:
                continue
            url = item.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
            if _is_blocked_url(url):
                continue
            hits.append(ArticleHit(title=title, url=url, snippet="Hacker News trending"))
            if len(hits) >= limit:
                break
        except Exception:
            continue
    return hits


def fetch_producthunt_trending(limit: int = 10, days: int = 7) -> List[ArticleHit]:
    rss_url = "https://www.producthunt.com/feeds/topic/artificial-intelligence"
    cutoff = datetime.utcnow() - timedelta(days=days)
    try:
        feed = feedparser.parse(rss_url)
    except Exception:
        return []
    hits: List[ArticleHit] = []
    for entry in feed.entries[:limit * 2]:
        # Date filter
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        if published:
            pub_dt = datetime(*published[:6])
            if pub_dt < cutoff:
                continue
        elif not published:
            # No date available — skip to avoid stale content
            continue
        url = entry.get("link", "")
        if _is_blocked_url(url):
            continue
        hits.append(
            ArticleHit(
                title=entry.get("title", ""),
                url=url,
                snippet="Product Hunt AI launch",
                published=entry.get("published"),
            )
        )
        if len(hits) >= limit:
            break
    return hits


CURATED_FEEDS = [
    "https://blog.openai.com/rss/",
    "https://feedproxy.feedly.com/5b36e586-cfce-45df-9d64-1cf9fed78e5b",  # Anthropic
    "https://deepmind.com/blog/feed/basic/",
    "http://research.microsoft.com/rss/news.xml",
    "http://www.technologyreview.com/rss/rss.aspx",
    "https://ai.facebook.com/blog/rss",
    "https://ai.googleblog.com/atom.xml",
    "https://www.oecd.ai/feed",
    "https://hai.stanford.edu/rss.xml",
]


# ── Google Alert RSS feeds (from Feedly subscriptions) ──

GOOGLE_ALERT_FEEDS: Dict[str, List[str]] = {
    "trending": [
        "https://www.google.com/alerts/feeds/03030665084568507357/6619274340374812968",       # AGI
        "https://www.google.com/alerts/feeds/03030665084568507357/5237285988387868375",       # ASI
        "https://www.google.com/alerts/feeds/03030665084568507357/11653796448320099668",      # Job Replacement - AI
    ],
    "canadian": [
        "https://www.google.com/alerts/feeds/03030665084568507357/8343122122122789666",       # Canada - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/5306089899663451631",       # Public Sector - AI
    ],
    "global": [
        "https://www.google.com/alerts/feeds/03030665084568507357/2891758781116511337",       # Ethics - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/2278791836030122678",       # Governance - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/16866512384761599386",      # Privacy - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/7622957089141856354",       # Regulation - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/15942459126004772098",      # Security - AI
    ],
    "agri": [
        "https://www.google.com/alerts/feeds/03030665084568507357/15755737833312608799",      # Agriculture - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/3281559451078185126",       # Crops - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/5761797510369087166",       # Grain - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/9368672943932362999",       # Grain Industry - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/12957642636281638741",      # Oil seeds - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/12957642636281637464",      # Wheat - AI
        "https://www.google.com/alerts/feeds/03030665084568507357/14624198102712688249",      # Canadian Grain Commission
        "https://www.google.com/alerts/feeds/03030665084568507357/13610686801601706073",      # Canadian Grain Industry
        "https://www.google.com/alerts/feeds/03030665084568507357/17711904352499016105",      # Grain Discovery/Inarix
    ],
}


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
            else:
                # No date — skip to avoid stale content
                continue
            link = entry.get("link", "")
            if _is_blocked_url(link):
                continue
            hits.append(
                ArticleHit(
                    title=entry.get("title", ""),
                    url=link,
                    snippet=entry.get("summary", "")[:500],
                    published=entry.get("published"),
                )
            )
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    return hits


def fetch_google_alerts(section_key: str, limit: int = 15, days: int = 7) -> List[ArticleHit]:
    """Fetch articles from Google Alert RSS feeds mapped to a newsletter section."""
    urls = GOOGLE_ALERT_FEEDS.get(section_key, [])
    if not urls:
        return []
    hits: List[ArticleHit] = []
    cutoff = datetime.utcnow() - timedelta(days=days)
    for url in urls:
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
            else:
                continue
            link = entry.get("link", "")
            if _is_blocked_url(link):
                continue
            hits.append(
                ArticleHit(
                    title=entry.get("title", ""),
                    url=link,
                    snippet=entry.get("summary", "")[:500],
                    published=entry.get("published"),
                    source="Google Alert",
                )
            )
            if len(hits) >= limit:
                return _dedupe(hits)
    return _dedupe(hits)


def collect_trending(days: int) -> List[ArticleHit]:
    hits: List[ArticleHit] = []
    hits.extend(fetch_google_alerts("trending", limit=10, days=days))
    hits.extend(fetch_hn_trending(limit=20, days=days))
    hits.extend(fetch_producthunt_trending(limit=10, days=days))
    hits.extend(fetch_curated_feeds(limit=15, days=days))
    # Tavily fallback
    trending_cfg = SectionConfig(name="Trending AI", query='"AI" AND ("top news" OR trending) AND week', limit=8)
    hits.extend(search_stream(trending_cfg, days))
    return _dedupe(hits)


# ── Events Public (CSPS) ──


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
        url = res.get("url", "")
        if _is_blocked_url(url):
            continue
        hits.append(
            ArticleHit(
                title=res.get("title", "").strip(),
                url=url,
                snippet=res.get("content", "").strip(),
                source=res.get("source"),
                published=res.get("published_date"),
            )
        )
    return _dedupe(hits)


# ── Research Plain ──


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
            else:
                # No date — skip
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


# ── AI Progress ──


def _fetch_pwc_trending(limit: int = 10) -> List[ArticleHit]:
    rss_url = "https://paperswithcode.com/trending?format=rss"
    try:
        feed = feedparser.parse(rss_url)
    except Exception:
        return []
    hits: List[ArticleHit] = []
    for entry in feed.entries[:limit]:
        url = entry.get("link", "")
        if _is_blocked_url(url):
            continue
        hits.append(
            ArticleHit(
                title=entry.get("title", ""),
                url=url,
                snippet=entry.get("summary", "")[:400],
                source="PapersWithCode",
            )
        )
    return hits


def collect_ai_progress(days: int) -> List[ArticleHit]:
    hits: List[ArticleHit] = []
    hits.extend(_fetch_pwc_trending(limit=15))
    return _dedupe(hits)


# ── Canadian News (Google Alerts + Tavily) ──


def collect_canadian(days: int) -> List[ArticleHit]:
    """Prioritise Google Alert RSS for Canadian AI news, Tavily as fallback."""
    hits: List[ArticleHit] = []
    hits.extend(fetch_google_alerts("canadian", limit=10, days=days))
    hits.extend(search_stream(DEFAULT_STREAMS["canadian"], days))
    return _dedupe(hits)


# ── Agriculture / Grain-Tech (Google Alerts + Tavily) ──


def collect_agri(days: int) -> List[ArticleHit]:
    """Prioritise Google Alert RSS for agri-tech news, Tavily as fallback."""
    hits: List[ArticleHit] = []
    hits.extend(fetch_google_alerts("agri", limit=15, days=days))
    hits.extend(search_stream(DEFAULT_STREAMS["agri"], days))
    return _dedupe(hits)


# ── Global News (Google Alerts + Tavily) ──


def collect_global(days: int) -> List[ArticleHit]:
    """Prioritise Google Alert RSS for global AI policy news, Tavily as fallback."""
    hits: List[ArticleHit] = []
    hits.extend(fetch_google_alerts("global", limit=10, days=days))
    hits.extend(search_stream(DEFAULT_STREAMS["global"], days))
    return _dedupe(hits)



def get_streams(custom_limits: int | None = None) -> Dict[str, SectionConfig]:
    streams: Dict[str, SectionConfig] = {}
    for key, cfg in DEFAULT_STREAMS.items():
        new_cfg = replace(cfg)
        if custom_limits:
            new_cfg.limit = custom_limits
        streams[key] = new_cfg
    return streams
