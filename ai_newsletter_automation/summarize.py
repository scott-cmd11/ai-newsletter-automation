import json
import time
from typing import List

import requests

from .config import get_settings
from .models import SummaryItem, VerifiedArticle


SYSTEM_PROMPT_BASE = """You are a concise analyst producing copy for a weekly AI briefing.
Requirements:
- Output ONLY valid JSON array (no prose) of objects with keys: "Headline", "Summary_Text", "Live_Link", "Relevance", "Date".
- "Summary_Text" must be 1-2 crisp sentences, neutral tone, no sales language.
- "Relevance" must be an integer 1-10 rating of how relevant and timely the article is for a weekly AI briefing. Rate 8-10 for breaking news or major announcements from the past week, 5-7 for useful but not urgent, 1-4 for old, generic, or off-topic content.
- "Date" must be the article's publication date in YYYY-MM-DD format. Use the Published date provided with each article. If no date is available, use your best estimate but never omit this field.
- Preserve URLs exactly as provided — use the specific article URL, never a homepage or generic domain.
- Do not invent facts or links; rely solely on provided article content.
- SKIP articles that are clearly outdated (mentioning dates from months/years ago as recent), generic overview pages (Wikipedia, "What is AI?" articles), or have no substantive news content.
- If an article has no meaningful content or the URL points to a homepage rather than a specific article, omit it entirely."""


def _build_prompt(articles: List[VerifiedArticle]) -> str:
    lines = []
    for art in articles:
        pub_line = f"\nPublished: {art.published}" if art.published else ""
        lines.append(f"Title: {art.title}\nURL: {art.url}{pub_line}\nSnippet: {art.snippet}\nContent: {art.content[:4000]}")
    return "\n\n".join(lines)


def _parse_json(raw: str) -> List[SummaryItem]:
    # Handle markdown-wrapped JSON
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    data = json.loads(cleaned)
    items: List[SummaryItem] = []
    for obj in data:
        relevance = int(obj.get("Relevance", 5))
        # Filter out low-relevance items (6+ = useful)
        if relevance < 6:
            continue
        items.append(
            SummaryItem(
                Headline=obj.get("Headline", "").strip(),
                Summary_Text=obj.get("Summary_Text", "").strip(),
                Live_Link=obj.get("Live_Link", "").strip(),
                Date=obj.get("Date"),
            )
        )
    return items


_MAX_RETRIES = 3
_RETRY_BASE_WAIT = 2  # seconds


def _groq_request(url: str, headers: dict, payload: dict) -> dict:
    """POST to Groq with exponential-backoff retry on 429 and 5xx errors."""
    for attempt in range(_MAX_RETRIES):
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = _RETRY_BASE_WAIT * (2 ** attempt)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    # Final attempt — let it raise on failure
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def summarize_section(
    section_name: str,
    articles: List[VerifiedArticle],
    require_date: bool = False,
    model: str = "llama-3.3-70b-versatile",
    section_key: str = "",
) -> List[SummaryItem]:
    if not articles:
        return []

    settings = get_settings()
    extra_rules = ""
    if section_key == "research_plain":
        extra_rules = (
            "\n- Make summaries plain-language for non-technical readers; avoid jargon; include a short 'Why it matters for public service' clause."
        )
    elif section_key == "ai_progress":
        extra_rules = (
            "\n- Mention the benchmark or metric and what improved; one sentence impact for government services."
        )
    elif section_key in {"events_public", "events"}:
        extra_rules = "\n- Highlight what/when/who in one sentence."
    system_prompt = SYSTEM_PROMPT_BASE + extra_rules

    user_prompt = f"Section: {section_name}\nToday's date: {time.strftime('%Y-%m-%d')}\nSummarize the following verified articles:\n{_build_prompt(articles)}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(2):
        data = _groq_request(
            url,
            headers,
            {
                "model": model,
                "temperature": 0.2,
                "max_tokens": 1500,
                "messages": messages,
            },
        )
        raw = data["choices"][0]["message"]["content"].strip()
        try:
            return _parse_json(raw)
        except Exception:
            messages.append(
                {
                    "role": "system",
                    "content": "Your previous output was not valid JSON. Respond with JSON only, no prose.",
                }
            )
            continue

    return []
