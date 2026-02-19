import json
import sys
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Add project root to path so the package can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_newsletter_automation.runner import process_section, SECTION_ORDER
from ai_newsletter_automation.models import SummaryItem


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        key = params.get("key", [None])[0]
        days = int(params.get("days", ["7"])[0])

        if not key:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": "Missing 'key' parameter",
                "valid_keys": SECTION_ORDER,
            }).encode())
            return

        if key not in SECTION_ORDER:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": f"Unknown section key: {key}",
                "valid_keys": SECTION_ORDER,
            }).encode())
            return

        try:
            items = process_section(key, days)
            result = {
                "section_key": key,
                "items": [
                    {
                        "Headline": item.Headline,
                        "Summary_Text": item.Summary_Text,
                        "Live_Link": item.Live_Link,
                        "Date": item.Date,
                    }
                    for item in items
                ],
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        except Exception as exc:
            # Return partial success (empty items) instead of hard-failing.
            # This prevents rate-limit cascades from marking every section red.
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "section_key": key,
                "items": [],
                "warning": str(exc),
            }).encode())
