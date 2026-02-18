from dataclasses import dataclass
from datetime import date
from typing import List, Optional


@dataclass
class ArticleHit:
    title: str
    url: str
    snippet: str
    source: Optional[str] = None
    published: Optional[str] = None


@dataclass
class VerifiedArticle:
    title: str
    url: str
    snippet: str
    content: str
    published: Optional[str] = None


@dataclass
class SummaryItem:
    Headline: str
    Summary_Text: str
    Live_Link: str
    Date: Optional[str] = None  # ISO string if present (events)


@dataclass
class SectionConfig:
    name: str
    query: str
    limit: int
    require_date: bool = False
    description: Optional[str] = None
    fallback: Optional[List[str]] = None
    days: Optional[int] = None  # override default search window for this section
