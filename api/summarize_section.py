"""Step 2 of 2: Summarize pre-verified articles using Gemini.

Receives articles from the frontend (which got them from /api/search_section)
and returns LLM-generated summaries. This keeps the Gemini call in its own
serverless invocation, well under the 60-second Vercel timeout.
"""
import json
import sys
import os
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_newsletter_automation.models import VerifiedArticle
from ai_newsletter_automation.summarize import summarize_section
from ai_newsletter_automation.runner import _filter_items_by_date


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        # Check Gemini key
        if not os.getenv("GEMINI_API_KEY"):
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "Missing GEMINI_API_KEY on server",
            }).encode())
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length))

            key = body.get("key", "")
            section_name = body.get("section_name", key)
            lang = body.get("lang", "en")
            days = body.get("days", 7)
            relevance_threshold = body.get("relevance_threshold", 6)
            articles_data = body.get("articles", [])

            # Reconstruct VerifiedArticle objects
            verified = [
                VerifiedArticle(
                    title=a.get("title", ""),
                    url=a.get("url", ""),
                    snippet=a.get("snippet", ""),
                    content=a.get("content", ""),
                    published=a.get("published", ""),
                )
                for a in articles_data
            ]

            if not verified:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "section_key": key,
                    "items": [],
                }).encode())
                return

            items = summarize_section(
                section_name, verified,
                section_key=key,
                lang=lang,
                relevance_threshold=relevance_threshold,
            )

            # Filter by date window
            pre_filter_count = len(items)
            items = _filter_items_by_date(items, days)
            post_filter_count = len(items)
            final_items = [item for item in items if item.Live_Link]

            result = {
                "section_key": key,
                "items": [
                    {
                        "Headline": item.Headline,
                        "Summary_Text": item.Summary_Text,
                        "Live_Link": item.Live_Link,
                        "Date": item.Date,
                        "Relevance": item.Relevance,
                        "Source": item.Source,
                    }
                    for item in final_items
                ],
                "debug": {
                    "articles_received": len(verified),
                    "llm_items_returned": pre_filter_count,
                    "after_date_filter": post_filter_count,
                    "after_link_filter": len(final_items),
                },
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
                "section_key": key if 'key' in dir() else "",
                "items": [],
                "error": str(exc),
            }).encode())
