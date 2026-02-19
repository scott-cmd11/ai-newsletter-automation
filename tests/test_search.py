from ai_newsletter_automation.models import ArticleHit
from ai_newsletter_automation.search import (
    _filter_by_date,
    _is_blocked_url,
    _unwrap_google_redirect,
)


def test_filter_by_date_rejects_dateless():
    """Articles with no published date should be rejected to avoid stale content."""
    hits = [
        ArticleHit(title="No Date", url="https://example.com/1", snippet="s"),
        ArticleHit(title="Has Date", url="https://example.com/2", snippet="s", published="2026-02-18"),
    ]
    filtered = _filter_by_date(hits, days=30)
    assert len(filtered) == 1
    assert filtered[0].title == "Has Date"


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
