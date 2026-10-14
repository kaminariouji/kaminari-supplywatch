"""HTML text extraction and structured field detection.

Every extracted field carries the exact quote (a contiguous span of the page
text) that it came from, so a later verifier can check the quote against the
fetched page. No quote, no field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCK_TAGS = {
    "p", "div", "li", "tr", "td", "th", "section", "article", "header",
    "footer", "h1", "h2", "h3", "h4", "h5", "h6", "br", "table", "ul", "ol",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        lines = [re.sub(r"\s+", " ", line).strip() for line in joined.splitlines()]
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML must not kill the scan
        pass
    return parser.text()


# ---------------------------------------------------------------------------
# Field detection
# ---------------------------------------------------------------------------

@dataclass
class FieldHit:
    kind: str          # price | lead_time | stock | moq
    value: str         # normalized display value, e.g. "GBP 12.50", "6 weeks"
    quote: str         # the exact page text span the value came from
    context: str       # the full line around the quote


@dataclass
class Extraction:
    hits: list[FieldHit] = field(default_factory=list)

    def best(self, kind: str) -> FieldHit | None:
        for hit in self.hits:
            if hit.kind == kind:
                return hit
        return None


_CURRENCY = r"(?:USD|EUR|GBP|IDR|JPY|CNY|AUD|CAD|CHF|SGD|MYR|INR|[$€£¥])"
_PRICE_RE = re.compile(
    _CURRENCY + r"\s?[\d][\d.,]*|\d[\d.,]*\s?" + _CURRENCY,
    re.IGNORECASE,
)
_LEAD_TIME_RE = re.compile(
    r"(?:lead\s*time|delivery|ships?|dispatch|backorder)[^\n.]{0,40}?"
    r"(\d{1,3})\s*(?:-|–|to)?\s*(\d{1,3})?\s*(business\s+)?(day|week|month)s?",
    re.IGNORECASE,
)
_LEAD_TIME_FALLBACK_RE = re.compile(
    r"\b(\d{1,3})\s*(?:-|–|to)?\s*(\d{1,3})?\s*(business\s+)?(day|week|month)s?\b",
    re.IGNORECASE,
)
_STOCK_RE = re.compile(
    r"\b(out\s+of\s+stock|in\s+stock|backorder(?:ed)?|discontinued|"
    r"available|on\s+order|sold\s+out|pre-?order)\b"
    r"(?:\s*[:(]\s*(\d[\d,]*)\s*(?:units?|pcs?|pieces?)?\)?)?",
    re.IGNORECASE,
)
_MOQ_RE = re.compile(
    r"(?:min(?:imum)?\.?\s*(?:order|qty|quantity)|moq)\s*[:\-]?\s*(\d[\d,]*)",
    re.IGNORECASE,
)

_MONEY_RE = re.compile(r"(\d[\d.,]*)")

_STOPWORDS = {"the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "with"}


def _keywords(selector: str) -> list[str]:
    """Turn a human selector like 'lead time' into match keywords."""
    words = re.findall(r"[a-z0-9]+", selector.lower())
    return [w for w in words if w not in _STOPWORDS]


def _line_of(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def _score_line(line: str, keywords: list[str]) -> int:
    lower = line.lower()
    return sum(1 for kw in keywords if kw in lower)


def _normalize_price(raw: str) -> str:
    raw = raw.strip()
    if raw[0].isdigit():  # "12.50 GBP" -> "GBP 12.50"
        parts = raw.split()
        if len(parts) == 2:
            raw = f"{parts[1]} {parts[0]}"
    symbol_map = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
    for symbol, code in symbol_map.items():
        if symbol in raw:
            raw = raw.replace(symbol, code + " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def extract_fields(text: str, selectors: dict[str, list[str]]) -> Extraction:
    """Detect the fields a watchlist item asked for.

    selectors maps kind -> list of human hints ("price", "lead time", ...).
    Detection is heuristic; the returned quote is what gets verified later.
    """
    extraction = Extraction()
    lines = text.splitlines()

    for kind, hints in selectors.items():
        keywords: list[str] = []
        for hint in hints:
            keywords.extend(_keywords(hint))

        if kind == "price":
            best: tuple[int, FieldHit] | None = None
            for line in lines:
                for match in _PRICE_RE.finditer(line):
                    score = _score_line(line, keywords) + 1
                    hit = FieldHit(
                        kind="price",
                        value=_normalize_price(match.group(0)),
                        quote=match.group(0).strip(),
                        context=line,
                    )
                    if best is None or score > best[0]:
                        best = (score, hit)
            if best:
                extraction.hits.append(best[1])

        elif kind == "lead_time":
            best = None
            for line in lines:
                match = _LEAD_TIME_RE.search(line)
                if not match:
                    match = _LEAD_TIME_FALLBACK_RE.search(line)
                if match:
                    low = match.group(1)
                    high = match.group(2)
                    unit = match.group(4).lower()
                    if unit.endswith("s"):
                        unit = unit[:-1]
                    if not unit.endswith("s"):
                        unit += "s"
                    value = f"{low}-{high} {unit}" if high else f"{low} {unit}"
                    score = _score_line(line, keywords)
                    hit = FieldHit(
                        kind="lead_time",
                        value=value,
                        quote=match.group(0).strip(),
                        context=line,
                    )
                    if best is None or score > best[0]:
                        best = (score, hit)
            if best:
                extraction.hits.append(best[1])

        elif kind == "stock":
            best = None
            for line in lines:
                match = _STOCK_RE.search(line)
                if match:
                    status = re.sub(r"\s+", " ", match.group(1)).lower()
                    if match.group(2):
                        status = f"{status} ({match.group(2)} units)"
                    score = _score_line(line, keywords) + 1
                    hit = FieldHit(
                        kind="stock",
                        value=status,
                        quote=match.group(0).strip(),
                        context=line,
                    )
                    if best is None or score > best[0]:
                        best = (score, hit)
            if best:
                extraction.hits.append(best[1])

        elif kind == "moq":
            best = None
            for line in lines:
                match = _MOQ_RE.search(line)
                if match:
                    hit = FieldHit(
                        kind="moq",
                        value=match.group(1),
                        quote=match.group(0).strip(),
                        context=line,
                    )
                    score = _score_line(line, keywords) + 1
                    if best is None or score > best[0]:
                        best = (score, hit)
            if best:
                extraction.hits.append(best[1])

    return extraction
