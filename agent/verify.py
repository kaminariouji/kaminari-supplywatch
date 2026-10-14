"""Quote verification: a field is only trusted if its quote appears on the page."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .extract import FieldHit


def normalize(text: str) -> str:
    """Collapse whitespace and decode entities for comparison."""
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


@dataclass
class VerifiedField:
    kind: str
    value: str
    quote: str
    context: str


@dataclass
class VerificationReport:
    verified: list[VerifiedField]
    dropped: list[FieldHit]


def verify_fields(hits: list[FieldHit], page_text: str) -> VerificationReport:
    """Keep only hits whose quote provably occurs in the fetched page text."""
    haystack = normalize(page_text)
    verified: list[VerifiedField] = []
    dropped: list[FieldHit] = []
    for hit in hits:
        needle = normalize(hit.quote)
        if needle and needle in haystack:
            verified.append(
                VerifiedField(
                    kind=hit.kind,
                    value=hit.value,
                    quote=hit.quote,
                    context=hit.context,
                )
            )
        else:
            dropped.append(hit)
    return VerificationReport(verified=verified, dropped=dropped)
