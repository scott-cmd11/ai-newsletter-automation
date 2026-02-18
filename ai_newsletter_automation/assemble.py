from datetime import date
from pathlib import Path
from typing import Dict, List

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import SummaryItem


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_env() -> Environment:
    loader = FileSystemLoader(str(PROJECT_ROOT / "template"))
    env = Environment(loader=loader, autoescape=select_autoescape(["html", "xml"]))
    return env


def render_newsletter(sections: Dict[str, List[SummaryItem]], run_date: str | None = None) -> str:
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
    )
