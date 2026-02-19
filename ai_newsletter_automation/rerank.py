"""LLM-as-Judge pre-reranking — scores article relevance before summarization."""

import json
import logging
import time
from typing import List, Optional

import requests

from .config import get_settings
from .models import VerifiedArticle, SectionConfig

log = logging.getLogger(__name__)

_RERANK_SYSTEM = (
    "You are a relevance-scoring assistant. You will receive a section topic and a "
    "numbered list of article titles and snippets. For EACH article, return a JSON "
    "array of objects with keys: {\"index\": <int>, \"score\": <1-10>}.\n"
    "Score meaning:\n"
    "  1-3: Off-topic or very low relevance\n"
    "  4-5: Tangentially related\n"
    "  6-7: Relevant\n"
    "  8-10: Highly relevant, must-include\n"
    "Return ONLY valid JSON. No markdown, no commentary."
)


def _build_rerank_prompt(section_name: str, articles: List[VerifiedArticle]) -> str:
    lines = [f"Section topic: {section_name}\n\nArticles:"]
    for i, a in enumerate(articles):
        snippet = (a.snippet or a.content or "")[:200]
        lines.append(f"{i+1}. Title: {a.title}\n   Snippet: {snippet}")
    return "\n".join(lines)


def _parse_scores(raw: str, count: int) -> List[int]:
    """Parse LLM JSON response into a list of scores aligned by article index."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    data = json.loads(cleaned)
    scores = [5] * count  # default score for any missing entries
    for obj in data:
        idx = int(obj.get("index", 0)) - 1  # 1-indexed → 0-indexed
        if 0 <= idx < count:
            scores[idx] = int(obj.get("score", 5))
    return scores


def rerank_articles(
    articles: List[VerifiedArticle],
    section: SectionConfig,
    model: str = "llama-3.3-70b-versatile",
) -> List[VerifiedArticle]:
    """Score and filter articles by LLM-judged relevance.

    Only invoked when len(articles) > section.limit to avoid wasting tokens.
    On any error, returns articles unchanged (graceful fallback).
    """
    if len(articles) <= section.limit:
        return articles

    settings = get_settings()
    prompt = _build_rerank_prompt(section.name, articles)

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _RERANK_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 512,
            },
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        scores = _parse_scores(raw, len(articles))
    except Exception as e:
        log.warning("Reranking failed, returning articles unchanged: %s", e)
        return articles

    # Pair articles with scores, filter below threshold, sort descending
    scored = list(zip(articles, scores))
    scored = [(a, s) for a, s in scored if s >= section.relevance_threshold]
    scored.sort(key=lambda x: x[1], reverse=True)

    result = [a for a, _ in scored]
    log.info(
        "Reranked %d → %d articles for '%s' (threshold=%d)",
        len(articles), len(result), section.name, section.relevance_threshold,
    )
    return result
