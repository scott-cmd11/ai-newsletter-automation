import json
import time
from typing import List

import requests

from .config import get_settings
from .models import SummaryItem, VerifiedArticle


SYSTEM_PROMPT_BASE = """You are the senior editorial analyst for "AI This Week,"
a trusted weekly AI briefing read by Canadian federal public servants.

VOICE & TONE:
- Authoritative but approachable — like a smart colleague briefing you over coffee.
- Write for a Deputy Minister who has 3 minutes. Be razor-sharp.
- No hype, no sales language, no "groundbreaking" or "revolutionary."
- Plain language first; define technical terms in parentheses if unavoidable.

OUTPUT FORMAT:
- Output ONLY a valid JSON array (no prose) of objects with these keys:
  "Headline", "Summary_Text", "Live_Link", "Relevance", "Date", "Source"
- "Headline": max 12 words. Punchy. Lead with the IMPACT, not the organization.
  BAD: "OpenAI Releases New Model"
  GOOD: "New GPT-5 Scores 92% on Federal Policy Benchmarks"
- "Summary_Text": exactly 2 sentences. Sentence 1 = what happened.
  Sentence 2 = why a Canadian public servant should care (operational impact,
  policy implication, or learning opportunity).
- "Date": article's publication date in YYYY-MM-DD format from the Published
  field provided. If unavailable, give best estimate but never omit.
- "Relevance": integer 1-10. Score 8-10 for: Canadian government impact, federal
  policy changes, AI tools usable in public service, security/privacy implications
  for government data. Score 5-7 for: general industry news with indirect relevance.
  Score 1-4 for: old news, generic explainers, or content with no public-sector angle.
- "Source": short label for where this article comes from (e.g. "Reuters",
  "arXiv", "TBS", "OECD", "Hacker News", "OpenAI Blog"). Use the domain or
  organization name — keep it under 3 words.

LINK INTEGRITY:
- Preserve URLs EXACTLY as provided — use the specific article URL, never a homepage.
- Do not fabricate or modify URLs.

QUALITY GATES — SKIP articles that are:
- Clearly outdated (mentioning past dates as "recent")
- Generic explainers ("What is AI?", Wikipedia-style content)
- Press releases with no substantive news
- Paywalled with no useful snippet
- Homepage URLs rather than specific articles"""


def _build_prompt(articles: List[VerifiedArticle]) -> str:
    lines = []
    for art in articles:
        pub_line = f"\nPublished: {art.published}" if art.published else ""
        lines.append(f"Title: {art.title}\nURL: {art.url}{pub_line}\nSnippet: {art.snippet}\nContent: {art.content[:4000]}")
    return "\n\n".join(lines)


def _parse_json(raw: str, relevance_threshold: int = 6) -> List[SummaryItem]:
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
        # Filter out items below the section's relevance threshold
        if relevance < relevance_threshold:
            continue
        items.append(
            SummaryItem(
                Headline=obj.get("Headline", "").strip(),
                Summary_Text=obj.get("Summary_Text", "").strip(),
                Live_Link=obj.get("Live_Link", "").strip(),
                Date=obj.get("Date"),
                Relevance=relevance,
                Source=obj.get("Source", "").strip() or None,
            )
        )
    return items


_MAX_RETRIES = 3
_RETRY_BASE_WAIT = 3  # seconds — tuned to stay under Vercel 60s timeout
_MAX_WAIT = 15        # cap single wait to avoid exceeding function timeout


def _groq_request(url: str, headers: dict, payload: dict) -> dict:
    """POST to Groq with exponential-backoff retry on 429 and 5xx errors."""
    for attempt in range(_MAX_RETRIES):
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 429 or resp.status_code >= 500:
            # Respect Retry-After header if Groq provides one
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = min(max(float(retry_after), 1.0), _MAX_WAIT)
                except (ValueError, TypeError):
                    wait = min(_RETRY_BASE_WAIT * (2 ** attempt), _MAX_WAIT)
            else:
                wait = min(_RETRY_BASE_WAIT * (2 ** attempt), _MAX_WAIT)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    # Final attempt — let it raise on failure
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


_FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",       # much higher rate limit on Groq free tier
]


# ── French language modifier ──

_FRENCH_PROMPT_MODIFIER = """

LANGUAGE REQUIREMENT — FRENCH:
- Write ALL "Headline" and "Summary_Text" values in fluent, professional Canadian French.
- Use proper Canadian French terminology (e.g., "fonction publique" not "service public",
  "gouvernement fédéral" not "gouvernement central").
- Keep "Source", "Live_Link", and "Date" fields in their original format (do not translate).
- Maintain the same quality, tone, and brevity standards as the English version."""


def summarize_section(
    section_name: str,
    articles: List[VerifiedArticle],
    require_date: bool = False,
    model: str = "llama-3.3-70b-versatile",
    section_key: str = "",
    lang: str = "en",
    relevance_threshold: int = 6,
) -> List[SummaryItem]:
    if not articles:
        return []

    settings = get_settings()
    extra_rules = ""
    if section_key == "research_plain":
        extra_rules = (
            "\n- Translate academic findings into plain language a non-technical "
            "executive can act on. Replace jargon with everyday equivalents."
            "\n- Sentence 2 MUST answer: 'How could this change how we deliver "
            "services or make policy decisions?'"
        )
    elif section_key == "ai_progress":
        extra_rules = (
            "\n- Lead with the benchmark name and the improvement metric "
            "(e.g., '12% accuracy gain on…')."
            "\n- Sentence 2: one concrete implication for government operations "
            "(e.g., 'could reduce manual document review time by…')."
        )
    elif section_key in {"events_public", "events"}:
        extra_rules = (
            "\n- Format: What event → When (date) → Who it's for → "
            "How to register (if URL available)."
            "\n- Prioritize free/government-accessible events. Flag cost if applicable."
        )
    elif section_key == "canadian":
        extra_rules = (
            "\n- Prioritize: federal policy announcements, TBS directives, "
            "provincial AI strategies, Canadian AI company milestones."
            "\n- Always mention which level of government (federal/provincial/municipal) "
            "or which department is involved."
        )
    elif section_key == "global":
        extra_rules = (
            "\n- Focus on AI governance, regulation, and workforce policy from "
            "G7/OECD/EU/US that could influence Canadian federal policy."
            "\n- Sentence 2: note any direct relevance to Canada's AI strategy "
            "or existing GC directives."
        )
    elif section_key == "agri":
        extra_rules = (
            "\n- Focus on precision agriculture, grain quality AI, crop prediction, "
            "and supply chain optimization."
            "\n- Sentence 2: connect to Canadian agricultural priorities "
            "(CGC, canola, wheat, grain logistics)."
        )
    elif section_key == "deep_dive":
        extra_rules = (
            "\n- These are long-form reports. Summarize the single most important "
            "finding or recommendation."
            "\n- Sentence 2: what action or awareness shift this demands from "
            "a Canadian federal policy lens."
        )
    elif section_key == "trending":
        extra_rules = (
            "\n- Capture the biggest AI stories of the week that everyone is talking about."
            "\n- Sentence 2: why this matters beyond the tech — workforce, policy, or "
            "service delivery implications."
        )
    system_prompt = SYSTEM_PROMPT_BASE + extra_rules

    # Add French language modifier if needed
    if lang == "fr":
        system_prompt += _FRENCH_PROMPT_MODIFIER

    user_prompt = f"Section: {section_name}\nToday's date: {time.strftime('%Y-%m-%d')}\nSummarize the following verified articles:\n{_build_prompt(articles)}"

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }

    # Try each model in order — fall back to smaller/faster models on rate limits
    models_to_try = [model] + [m for m in _FALLBACK_MODELS if m != model]

    for current_model in models_to_try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            for attempt in range(2):
                data = _groq_request(
                    url,
                    headers,
                    {
                        "model": current_model,
                        "temperature": 0.2,
                        "max_tokens": 1500,
                        "messages": messages,
                    },
                )
                raw = data["choices"][0]["message"]["content"].strip()
                try:
                    return _parse_json(raw, relevance_threshold=relevance_threshold)
                except Exception:
                    messages.append(
                        {
                            "role": "system",
                            "content": "Your previous output was not valid JSON. Respond with JSON only, no prose.",
                        }
                    )
                    continue
        except requests.exceptions.HTTPError:
            # Rate-limited even after retries — try next model
            continue

    return []


# ── TL;DR Executive Summary ──

TLDR_SYSTEM_PROMPT = """You produce a 3-bullet executive summary for a weekly AI briefing
read by Canadian federal public servants.

Rules:
- Output ONLY a valid JSON array of exactly 3 strings.
- Each string is one punchy sentence (max 25 words).
- Lead each bullet with a strong verb or the key impact.
- Cover the 3 most important stories from the items provided.
- Write for a Deputy Minister scanning on their phone."""

_TLDR_FRENCH_MODIFIER = "\n- Write ALL bullets in fluent, professional Canadian French."


def generate_tldr(
    top_items: List[SummaryItem],
    model: str = "llama-3.3-70b-versatile",
    lang: str = "en",
) -> List[str]:
    """Generate 3-bullet TL;DR from the highest-relevance newsletter items."""
    if not top_items:
        return []

    settings = get_settings()
    items_text = "\n".join(
        f"- {it.Headline}: {it.Summary_Text}" for it in top_items[:6]
    )
    user_prompt = f"Pick the 3 most important and produce 3 bullets:\n{items_text}"

    sys_prompt = TLDR_SYSTEM_PROMPT
    if lang == "fr":
        sys_prompt += _TLDR_FRENCH_MODIFIER

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }

    try:
        data = _groq_request(
            url, headers,
            {
                "model": model,
                "temperature": 0.2,
                "max_tokens": 400,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
        )
        raw = data["choices"][0]["message"]["content"].strip()
        cleaned = raw
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
        bullets = json.loads(cleaned)
        if isinstance(bullets, list):
            return [str(b).strip() for b in bullets[:3]]
    except Exception:
        pass
    return []
