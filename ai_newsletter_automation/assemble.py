from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import SummaryItem
from .search import DEFAULT_STREAMS


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Build display-name lookup from the canonical SectionConfig definitions
SECTION_LABELS: Dict[str, str] = {key: cfg.name for key, cfg in DEFAULT_STREAMS.items()}

SECTION_DESCRIPTIONS: Dict[str, str] = {
    "trending": "The biggest AI stories everyone is talking about this week.",
    "canadian": "AI developments directly affecting Canadian federal and provincial policy.",
    "global": "International AI governance, regulation, and workforce policy.",
    "events": "Upcoming AI conferences, summits, and workshops.",
    "events_public": "Training and learning opportunities for federal public servants.",
    "agri": "AI-driven innovation in agriculture, grain quality, and supply chains.",
    "ai_progress": "Notable benchmark results and technical capability milestones.",
    "research_plain": "Cutting-edge research explained in plain language.",
    "deep_dive": "In-depth reports and analyses from leading AI organizations.",
}


def _get_env() -> Environment:
    loader = FileSystemLoader(str(PROJECT_ROOT / "template"))
    env = Environment(loader=loader, autoescape=select_autoescape(["html", "xml"]))
    return env


def render_newsletter(
    sections: Dict[str, List[SummaryItem]],
    run_date: str | None = None,
    tldr: Optional[List[str]] = None,
) -> str:
    env = _get_env()
    template = env.get_template("newsletter.html.j2")

    # Sort events by date when available
    if "events" in sections:
        events = sections["events"]
        sections["events"] = sorted(
            events,
            key=lambda x: x.Date or "",
        )

    return template.render(
        run_date=run_date or date.today().isoformat(),
        sections=sections,
        section_labels=SECTION_LABELS,
        section_descriptions=SECTION_DESCRIPTIONS,
        tldr=tldr or [],
    )

