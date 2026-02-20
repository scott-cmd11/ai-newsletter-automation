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

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Attempt to auto-close truncated arrays
        try:
            if not cleaned.endswith("]"):
                if cleaned.endswith("}"):
                    cleaned += "]"
                elif cleaned.endswith('"'):
                    cleaned += "}]"
                else:
                    cleaned += '"]'
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            print("Warning: LLM returned invalid JSON. Skipping section.")
            return []

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


import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from google.api_core import exceptions

def _configure_gemini():
    settings = get_settings()
    genai.configure(api_key=settings.gemini_api_key)

def _gemini_request(
    system_prompt: str,
    user_prompt: str,
    model_name: str = "gemini-3-flash-preview",
    temperature: float = 0.1,
) -> str:
    _configure_gemini()
    
    generation_config = {
        "temperature": temperature,
        "max_output_tokens": 4096,
        "response_mime_type": "application/json",
    }
    
    # Safety settings: block few things to avoid over-filtering news
    safety_settings = {
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
    }

    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system_prompt,
        generation_config=generation_config,
        safety_settings=safety_settings,
    )

    # Retry logic for rate limits (429) & server errors (500/503)
    # Retry logic for rate limits (429) & server errors (500/503)
    # Fast Fail for Vercel: Reduce retries to avoid timeouts (60s limit)
    max_retries = 2
    base_delay = 2

    for attempt in range(max_retries):
        try:
            response = model.generate_content(user_prompt)
            return response.text
        except (exceptions.ResourceExhausted, exceptions.ServiceUnavailable, exceptions.InternalServerError):
            if attempt == max_retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt)) # Exponential backoff: 5, 10, 20, 40, 80
        except Exception as e:
            # Other errors (e.g. 400 Bad Request) fail immediately
            raise e
    return ""

def summarize_section(
    section_name: str,
    articles: List[VerifiedArticle],
    require_date: bool = False,
    model: str = "gemini-3-flash-preview",
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
    elif section_key == "events":
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

    # Default to Gemini 2.0 Flash if unspecified or old model name passed
    if "llama" in model:
        model = "gemini-3-flash-preview"

    raw = _gemini_request(system_prompt, user_prompt, model_name=model)
    return _parse_json(raw, relevance_threshold=relevance_threshold)


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
    model: str = "gemini-3-flash-preview",
    lang: str = "en",
) -> List[str]:
    """Generate 3-bullet TL;DR from the highest-relevance newsletter items."""
    if not top_items:
        return []

    items_text = "\n".join(
        f"- {it.Headline}: {it.Summary_Text}" for it in top_items[:6]
    )
    user_prompt = f"Pick the 3 most important and produce 3 bullets:\n{items_text}"

    sys_prompt = TLDR_SYSTEM_PROMPT
    if lang == "fr":
        sys_prompt += _TLDR_FRENCH_MODIFIER

    if "llama" in model:
        model = "gemini-3-flash-preview"

    try:
        raw = _gemini_request(sys_prompt, user_prompt, model_name=model)
        bullets = json.loads(raw)
        if isinstance(bullets, list):
            return [str(b).strip() for b in bullets[:3]]
    except Exception:
        pass
    return []
