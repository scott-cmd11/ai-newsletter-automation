from ai_newsletter_automation.models import ArticleHit
from ai_newsletter_automation.search import (
    _filter_by_date,
    _is_blocked_url,
    _unwrap_google_redirect,
    DEFAULT_STREAMS,
    _TRUSTED_SOURCES,
)


def test_filter_by_date_rejects_dateless_untrusted():
    """Undated articles from unknown sources should be rejected."""
    hits = [
        ArticleHit(title="No Date", url="https://example.com/1", snippet="s"),
        ArticleHit(title="Has Date", url="https://example.com/2", snippet="s", published="2026-02-18"),
    ]
    filtered = _filter_by_date(hits, days=30)
    assert len(filtered) == 1
    assert filtered[0].title == "Has Date"


def test_filter_by_date_allows_trusted_undated():
    """Undated articles from trusted sources (Google Alert, arXiv, etc.) should pass."""
    hits = [
        ArticleHit(title="Google Alert", url="https://example.com/1", snippet="s", source="Google Alert"),
        ArticleHit(title="arXiv Paper", url="https://arxiv.org/abs/123", snippet="s", source="arXiv"),
        ArticleHit(title="Random Blog", url="https://example.com/2", snippet="s", source="SomeRandomSite"),
        ArticleHit(title="No Source", url="https://example.com/3", snippet="s"),
    ]
    filtered = _filter_by_date(hits, days=7)
    assert len(filtered) == 2
    titles = {h.title for h in filtered}
    assert "Google Alert" in titles
    assert "arXiv Paper" in titles
    assert "Random Blog" not in titles
    assert "No Source" not in titles


def test_filter_by_date_rejects_old():
    """Articles older than the window should be filtered out."""
    hits = [
        ArticleHit(title="Old", url="https://example.com/1", snippet="s", published="2020-01-01"),
        ArticleHit(title="Recent", url="https://example.com/2", snippet="s", published="2026-02-18"),
    ]
    filtered = _filter_by_date(hits, days=7)
    assert len(filtered) == 1
    assert filtered[0].title == "Recent"


def test_unwrap_google_redirect():
    """Google Alert redirect URLs should be unwrapped to the real article URL."""
    wrapped = "https://www.google.com/url?rct=j&sa=t&url=https%3A%2F%2Fwww.reuters.com%2Fai-article&ct=ga"
    result = _unwrap_google_redirect(wrapped)
    assert result == "https://www.reuters.com/ai-article"


def test_unwrap_google_redirect_passthrough():
    """Non-redirect URLs should pass through unchanged."""
    normal = "https://www.reuters.com/ai-article"
    assert _unwrap_google_redirect(normal) == normal


def test_is_blocked_url_wikipedia():
    assert _is_blocked_url("https://en.wikipedia.org/wiki/Artificial_intelligence") is True


def test_is_blocked_url_allowed():
    assert _is_blocked_url("https://www.reuters.com/article/123") is False


def test_default_queries_no_single_words():
    """All Tavily queries should be multi-word and targeted, not generic single-word."""
    for key, cfg in DEFAULT_STREAMS.items():
        words = cfg.query.strip().split()
        assert len(words) > 2, f"Section '{key}' query too short/generic: '{cfg.query}'"


def test_trusted_sources_set():
    """Trusted source set should contain expected RSS/feed source types."""
    assert "Google Alert" in _TRUSTED_SOURCES
    assert "arXiv" in _TRUSTED_SOURCES
    assert "PapersWithCode" in _TRUSTED_SOURCES
    assert "RSS" in _TRUSTED_SOURCES
