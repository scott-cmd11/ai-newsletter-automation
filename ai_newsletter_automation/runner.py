import json
import os
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Callable, Optional

import click
import requests

from .assemble import render_newsletter
from .config import get_settings
from .models import SummaryItem, VerifiedArticle, ArticleHit, SectionConfig
from .scrape import scrape
from .search import (
    get_streams,
    search_stream,
    collect_trending,
    collect_events,
    collect_events_public,
    collect_research,
    collect_ai_progress,
    collect_canadian,
    collect_agri,
    collect_global,
    collect_deep_dive,
)
from .summarize import summarize_section, generate_tldr
from .verify import verify_link


SECTION_ORDER = [
    "trending",
    "canadian",
    "global",
    "events",
    "events_public",
    "agri",
    "ai_progress",
    "research_plain",
    "deep_dive",
]


def _log_skipped(reason: str, url: str, log: Path) -> None:
    """Best-effort logging — silently skip on read-only filesystems (Vercel)."""
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"reason": reason, "url": url}) + "\n")
    except OSError:
        # Vercel serverless: filesystem is read-only, try /tmp
        try:
            tmp_log = Path("/tmp") / "logs" / log.name
            tmp_log.parent.mkdir(parents=True, exist_ok=True)
            with tmp_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"reason": reason, "url": url}) + "\n")
        except OSError:
            pass  # Give up silently — logging is non-critical


def process_hits(hits: List[ArticleHit], limit: int, log_file: Path) -> List[VerifiedArticle]:
    verified: List[VerifiedArticle] = []
    for hit in hits:
        if len(verified) >= limit:
            break
        if not hit.url:
            _log_skipped("missing_url", "", log_file)
            continue
        html = verify_link(hit.url)
        if html is None:
            # Link unreachable — but if we have a good RSS snippet, use it
            if hit.snippet and len(hit.snippet) > 80:
                _log_skipped("verify_failed_using_snippet", hit.url, log_file)
                verified.append(
                    VerifiedArticle(
                        title=hit.title,
                        url=hit.url,
                        snippet=hit.snippet,
                        content=hit.snippet,
                        published=hit.published,
                    )
                )
            else:
                _log_skipped("verify_failed", hit.url, log_file)
            continue
        content = scrape(hit.url, html=html)
        if not content:
            # Scrape failed — fall back to RSS snippet
            if hit.snippet and len(hit.snippet) > 40:
                _log_skipped("scrape_failed_using_snippet", hit.url, log_file)
                content = hit.snippet
            else:
                _log_skipped("scrape_failed", hit.url, log_file)
                continue
        verified.append(
            VerifiedArticle(
                title=hit.title,
                url=hit.url,
                snippet=hit.snippet,
                content=content,
                published=hit.published,
            )
        )
    return verified


def process_section(key: str, days: int, max_per_stream: Optional[int] = None, lang: str = "en") -> List[SummaryItem]:
    """Generate summaries for a single newsletter section.

    This is the core function used by both the CLI and the Vercel API.
    Returns a list of SummaryItem dataclasses.
    """
    settings = get_settings()
    streams = get_streams(custom_limits=max_per_stream)

    if key not in streams:
        raise ValueError(f"Unknown section key: {key}")

    cfg = streams[key]

    collectors: Dict[str, Callable[[SectionConfig], List[ArticleHit]]] = {
        "trending": lambda c: collect_trending(days),
        "events": lambda c: collect_events(c.days or days),
        "events_public": lambda c: collect_events_public(c.days or days),
        "research_plain": lambda c: collect_research(c.days or days),
        "ai_progress": lambda c: collect_ai_progress(c.days or days),
        "canadian": lambda c: collect_canadian(c.days or days),
        "agri": lambda c: collect_agri(c.days or days),
        "global": lambda c: collect_global(c.days or days),
        "deep_dive": lambda c: collect_deep_dive(c.days or days),
    }

    log_file = settings.project_root / "logs" / f"run-{date.today().isoformat()}.jsonl"

    if key in collectors:
        hits = collectors[key](cfg)
    else:
        search_days = cfg.days or days
        hits = search_stream(cfg, search_days)

    _log_skipped(f"section_{key}_total_hits={len(hits)}", "", log_file)

    verified = process_hits(hits, cfg.limit, log_file)
    items = summarize_section(cfg.name, verified, require_date=cfg.require_date, section_key=key, lang=lang)

    # Strip out items with stale dates (the LLM may output old dates)
    items = _filter_items_by_date(items, days)

    # Links were already verified in process_hits — no redundant post-verify
    return [item for item in items if item.Live_Link]


def _filter_items_by_date(items: List[SummaryItem], days: int) -> List[SummaryItem]:
    """Remove SummaryItems whose LLM-generated Date is older than the window."""
    cutoff = date.today() - timedelta(days=days)
    filtered = []
    for item in items:
        if not item.Date:
            # No date on item — keep it (date wasn't available)
            filtered.append(item)
            continue
        try:
            # Try parsing common LLM date formats
            d = item.Date.strip()
            parsed = None
            for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
                try:
                    parsed = datetime.strptime(d, fmt).date()
                    break
                except ValueError:
                    continue
            if parsed and parsed < cutoff:
                # Date is too old — skip this item
                continue
        except Exception:
            pass
        filtered.append(item)
    return filtered


@click.command()
@click.option("--since-days", default=None, type=int, help="How many days back to search.")
@click.option("--date", "run_date", default=None, help="Override date string YYYY-MM-DD.")
@click.option("--max-per-stream", default=None, type=int, help="Override max items per stream.")
@click.option("--dry-run", is_flag=True, default=False, help="Write HTML only, skip Outlook.")
@click.option("--lang", default="en", type=click.Choice(["en", "fr"]), help="Output language.")
def main(since_days, run_date, max_per_stream, dry_run, lang):
    settings = get_settings()
    days = since_days or settings.run_days

    sections: Dict[str, List[SummaryItem]] = OrderedDict()
    for key in SECTION_ORDER:
        click.echo(f"  ▸ {key}...")
        sections[key] = process_section(key, days, max_per_stream, lang=lang)

    # Generate TL;DR from the top-relevance items across all sections
    click.echo("  ▸ generating TL;DR...")
    all_items = [item for items in sections.values() for item in items]
    all_items.sort(key=lambda x: x.Relevance or 0, reverse=True)
    tldr = generate_tldr(all_items[:6], lang=lang)

    html = render_newsletter(sections, run_date=run_date or date.today().isoformat(), tldr=tldr, lang=lang)

    suffix = f"-{lang}" if lang != "en" else ""
    output_path = settings.project_root / "output" / f"newsletter{suffix}.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    click.echo(f"Generated newsletter ({lang}) → {output_path}")


if __name__ == "__main__":
    main()

