from ai_newsletter_automation.assemble import render_newsletter
from ai_newsletter_automation.models import SummaryItem


def test_render_includes_headlines():
    sections = {
        "canadian": [
            SummaryItem(
                Headline="Test Headline",
                Summary_Text="Summary here",
                Live_Link="https://example.com",
            )
        ]
    }
    html = render_newsletter(sections, run_date="2026-02-18")
    assert "Test Headline" in html
    assert "2026-02-18" in html
