from datetime import date
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs, urljoin

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import SummaryItem
from .search import DEFAULT_STREAMS


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Build display-name lookup from the canonical SectionConfig definitions
SECTION_LABELS: Dict[str, str] = {key: cfg.name for key, cfg in DEFAULT_STREAMS.items()}

SECTION_LABELS_FR: Dict[str, str] = {
    "trending": "IA en vedette",
    "canadian": "Nouvelles canadiennes",
    "global": "Nouvelles internationales",
    "events": "Événements",
    "events_public": "Événements pour fonctionnaires",
    "agri": "Céréales / Agritech",
    "ai_progress": "Progrès en IA",
    "research_plain": "Recherche vulgarisée",
    "deep_dive": "Analyse approfondie",
}

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

SECTION_DESCRIPTIONS_FR: Dict[str, str] = {
    "trending": "Les plus grandes nouvelles en IA dont tout le monde parle cette semaine.",
    "canadian": "Développements en IA touchant directement les politiques fédérales et provinciales canadiennes.",
    "global": "Gouvernance, réglementation et politiques internationales en matière d'IA.",
    "events": "Conférences, sommets et ateliers en IA à venir.",
    "events_public": "Formations et occasions d'apprentissage pour les fonctionnaires fédéraux.",
    "agri": "Innovation en IA dans l'agriculture, la qualité des céréales et les chaînes d'approvisionnement.",
    "ai_progress": "Résultats de référence et jalons techniques notables.",
    "research_plain": "Recherche de pointe expliquée en langage simple.",
    "deep_dive": "Rapports et analyses approfondis des grandes organisations en IA.",
}

# UI strings for template chrome
UI_STRINGS = {
    "en": {
        "title": "AI This Week",
        "date_label": "Date:",
        "tldr_title": "⚡ TL;DR — This Week's Top 3",
        "top_story": "🔥 Top Story",
        "read_more": "Read more →",
        "footer_line1": "🍁 AI This Week — Automated AI briefing for Canadian public servants.",
        "footer_line2": "Curated with care. Powered by open-source intelligence.",
    },
    "fr": {
        "title": "IA cette semaine",
        "date_label": "Date :",
        "tldr_title": "⚡ En bref — Les 3 faits saillants",
        "top_story": "🔥 À la une",
        "read_more": "Lire la suite →",
        "footer_line1": "🍁 IA cette semaine — Bulletin automatisé sur l'IA pour les fonctionnaires canadiens.",
        "footer_line2": "Sélectionné avec soin. Propulsé par l'intelligence ouverte.",
    },
}


def _add_utm(url: str, section_key: str, run_date: str) -> str:
    """Append UTM tracking parameters to a URL for engagement analytics."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        existing_params = parse_qs(parsed.query)
        utm_params = {
            "utm_source": "ai_this_week",
            "utm_medium": "email",
            "utm_campaign": run_date,
            "utm_content": section_key,
        }
        # Don't overwrite existing UTM params
        for k, v in utm_params.items():
            if k not in existing_params:
                existing_params[k] = [v]
        new_query = urlencode(existing_params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url  # on any parsing error, return original


def _get_env() -> Environment:
    loader = FileSystemLoader(str(PROJECT_ROOT / "template"))
    env = Environment(loader=loader, autoescape=select_autoescape(["html", "xml"]))
    return env


def render_newsletter(
    sections: Dict[str, List[SummaryItem]],
    run_date: str | None = None,
    tldr: Optional[List[str]] = None,
    lang: str = "en",
) -> str:
    env = _get_env()
    template = env.get_template("newsletter.html.j2")

    effective_date = run_date or date.today().isoformat()

    # Sort events by date when available
    if "events" in sections:
        events = sections["events"]
        sections["events"] = sorted(
            events,
            key=lambda x: x.Date or "",
        )

    # Apply UTM tracking to all Live_Link URLs
    for section_key, items in sections.items():
        for item in items:
            if item.Live_Link:
                item.Live_Link = _add_utm(item.Live_Link, section_key, effective_date)

    # Select language-specific resources
    labels = SECTION_LABELS_FR if lang == "fr" else SECTION_LABELS
    descriptions = SECTION_DESCRIPTIONS_FR if lang == "fr" else SECTION_DESCRIPTIONS
    strings = UI_STRINGS.get(lang, UI_STRINGS["en"])

    return template.render(
        run_date=effective_date,
        sections=sections,
        section_labels=labels,
        section_descriptions=descriptions,
        tldr=tldr or [],
        lang=lang,
        ui=strings,
    )


