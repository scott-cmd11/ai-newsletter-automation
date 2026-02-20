"""Step 1 of 2: Search, scrape, and verify articles for a newsletter section.

Returns verified articles (not yet summarized) so the LLM call happens in a
separate serverless invocation, keeping each function well under the 60-second
Vercel timeout.
"""
import json
import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from dataclasses import replace
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_newsletter_automation.config import get_settings
from ai_newsletter_automation.search import (
    get_streams,
    collect_trending, collect_events, collect_events_public,
    collect_research, collect_ai_progress, collect_canadian,
    collect_agri, collect_global, collect_deep_dive,
    search_stream,
    _filter_by_keywords,
    _boost_by_keywords,
    _boost_by_source_quality,
    _sort_by_source_priority,
    _apply_time_decay,
)
from ai_newsletter_automation.runner import (
    process_hits,
    _filter_verified_articles_by_date,
    SECTION_ORDER,
)
from ai_newsletter_automation.dedup import deduplicate
from ai_newsletter_automation.rerank import rerank_articles

from pathlib import Path


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        key = params.get("key", [None])[0]
        days = int(params.get("days", ["7"])[0])
        limit_override = params.get("limit", [None])[0]

        if not key or key not in SECTION_ORDER:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": f"Invalid or missing 'key': {key}",
            }).encode())
            return

        # Check Tavily key
        if not os.getenv("TAVILY_API_KEY"):
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "Missing TAVILY_API_KEY on server",
            }).encode())
            return

        try:
            settings = get_settings()
            streams = get_streams(custom_limits=int(limit_override) if limit_override else None)
            cfg = streams[key]

            collectors = {
                "trending": lambda c: collect_trending(c.days or days),
                "events": lambda c: collect_events(c.days or days),
                "events_public": lambda c: collect_events_public(c.days or days),
                "research_plain": lambda c: collect_research(c.days or days),
                "ai_progress": lambda c: collect_ai_progress(c.days or days),
                "canadian": lambda c: collect_canadian(c.days or days),
                "agri": lambda c: collect_agri(c.days or days),
                "global": lambda c: collect_global(c.days or days),
                "deep_dive": lambda c: collect_deep_dive(c.days or days),
            }

            log_file = Path("/tmp") / "logs" / f"run-{date.today().isoformat()}.jsonl"

            # Collection
            if key in collectors:
                hits = collectors[key](cfg)
            else:
                hits = search_stream(cfg, cfg.days or days)

            # Curation pipeline
            hits = _filter_by_keywords(hits, cfg.reject_keywords)
            hits = _boost_by_keywords(hits, cfg.boost_keywords)
            hits = _boost_by_source_quality(hits)
            hits = _sort_by_source_priority(hits)
            hits = _apply_time_decay(hits, cfg.days or days)

            # Verification (parallel)
            verified = process_hits(hits, cfg.limit * 2, log_file)
            verified = deduplicate(verified)
            verified = _filter_verified_articles_by_date(verified, cfg.days or days)
            verified = rerank_articles(verified, cfg)
            verified = verified[:cfg.limit]

            result = {
                "section_key": key,
                "articles": [
                    {
                        "title": v.title,
                        "url": v.url,
                        "snippet": v.snippet or "",
                        "content": v.content[:4000] if v.content else "",
                        "published": v.published or "",
                    }
                    for v in verified
                ],
                "count": len(verified),
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except Exception as exc:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "section_key": key,
                "articles": [],
                "count": 0,
                "error": str(exc),
            }).encode())
