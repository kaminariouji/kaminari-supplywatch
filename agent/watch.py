"""Core scan loop: fetch -> extract -> verify -> diff -> alert.

Usage from code:
    from agent.watch import scan_watchlist, load_json
    state = scan_watchlist(load_json("watchlist.demo.json"), previous_state)
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .extract import extract_fields
from .fetch import fetch
from .llm import LLMConfig, draft_alert
from .verify import VerifiedField, verify_fields


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Value normalization for comparison
# ---------------------------------------------------------------------------

_PRICE_NUM = re.compile(r"([\d][\d.,]*)")
_UNIT_WEEKS = {"day": 1 / 7, "week": 1, "month": 4.345}
_LEAD_RE = re.compile(r"(\d{1,3})\s*(?:-\s*(\d{1,3}))?\s*(day|week|month)s?")


def price_number(value: str) -> float | None:
    match = _PRICE_NUM.search(value)
    if not match:
        return None
    raw = match.group(1)
    # "1.234,56" (EU) vs "1,234.56" (US) — pick the last separator as decimal.
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        tail = raw.split(",")[-1]
        raw = raw.replace(",", "." if len(tail) <= 2 else "")
    try:
        return float(raw)
    except ValueError:
        return None


def lead_weeks(value: str) -> float | None:
    match = _LEAD_RE.search(value.lower())
    if not match:
        return None
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) else low
    unit = _UNIT_WEEKS.get(match.group(3), 1.0)
    return (low + high) / 2 * unit


def stock_status(value: str) -> str:
    lower = value.lower()
    if "out of stock" in lower or "sold out" in lower or "discontinued" in lower:
        return "out"
    if "backorder" in lower:
        return "backorder"
    if "pre-order" in lower or "preorder" in lower:
        return "preorder"
    return "in"


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

@dataclass
class Change:
    item_id: str
    item_name: str
    supplier: str
    url: str
    field: str
    old: str | None
    new: str
    quote: str
    severity: str          # critical | warning | info
    detail: str = ""


@dataclass
class ItemSnapshot:
    id: str
    name: str
    supplier: str
    url: str
    fetched_at: str
    status: int
    error: str | None
    fields: dict[str, dict]
    dropped_quotes: int


@dataclass
class ScanState:
    generated_at: str
    items: dict[str, dict] = field(default_factory=dict)
    changes: list[dict] = field(default_factory=list)
    alert_text: str | None = None
    llm_used: bool = False


def _diff_item(item: dict, current: ItemSnapshot, previous: dict | None) -> list[Change]:
    changes: list[Change] = []
    prev_fields = (previous or {}).get("fields", {})
    for kind, payload in current.fields.items():
        new_value = payload["value"]
        old_value = (prev_fields.get(kind) or {}).get("value")
        if old_value is None:
            continue  # first observation, not a change
        if old_value == new_value:
            continue

        severity = "info"
        detail = f"{kind} changed from '{old_value}' to '{new_value}'"
        if kind == "price":
            old_n, new_n = price_number(old_value), price_number(new_value)
            if old_n and new_n and old_n > 0:
                pct = (new_n - old_n) / old_n * 100
                detail = f"price moved {pct:+.1f}% ({old_value} -> {new_value})"
                if pct >= 10:
                    severity = "critical"
                elif pct >= 3:
                    severity = "warning"
        elif kind == "lead_time":
            old_w, new_w = lead_weeks(old_value), lead_weeks(new_value)
            if old_w and new_w and old_w > 0:
                pct = (new_w - old_w) / old_w * 100
                detail = f"lead time moved {pct:+.0f}% ({old_value} -> {new_value})"
                if pct >= 50:
                    severity = "critical"
                elif pct >= 20:
                    severity = "warning"
        elif kind == "stock":
            old_s, new_s = stock_status(old_value), stock_status(new_value)
            if old_s == "in" and new_s in {"out", "backorder"}:
                severity = "critical"
                detail = f"availability dropped: {old_value} -> {new_value}"
            elif new_s == "out":
                severity = "warning"
                detail = f"availability: {old_value} -> {new_value}"
        changes.append(
            Change(
                item_id=item["id"],
                item_name=item["name"],
                supplier=item.get("supplier", ""),
                url=item["url"],
                field=kind,
                old=old_value,
                new=new_value,
                quote=payload["quote"],
                severity=severity,
                detail=detail,
            )
        )
    return changes


def scan_item(item: dict) -> ItemSnapshot:
    """Fetch one watchlist item and return a verified snapshot."""
    result = fetch(item["url"])
    if not result.ok:
        return ItemSnapshot(
            id=item["id"],
            name=item["name"],
            supplier=item.get("supplier", ""),
            url=item["url"],
            fetched_at=now_iso(),
            status=result.status,
            error=result.error or f"HTTP {result.status}",
            fields={},
            dropped_quotes=0,
        )

    from .extract import html_to_text

    text = html_to_text(result.text)
    extraction = extract_fields(text, item.get("fields", {}))
    report = verify_fields(extraction.hits, text)
    fields = {
        vf.kind: {"value": vf.value, "quote": vf.quote, "context": vf.context}
        for vf in report.verified
    }
    return ItemSnapshot(
        id=item["id"],
        name=item["name"],
        supplier=item.get("supplier", ""),
        url=item["url"],
        fetched_at=now_iso(),
        status=result.status,
        error=None,
        fields=fields,
        dropped_quotes=len(report.dropped),
    )


def scan_watchlist(
    watchlist: dict,
    previous_state: dict | None = None,
    llm: LLMConfig | None = None,
) -> ScanState:
    """Scan every item, diff against the previous state, and draft an alert."""
    previous_items = (previous_state or {}).get("items", {})
    state = ScanState(generated_at=now_iso())
    all_changes: list[Change] = []

    for item in watchlist.get("items", []):
        snapshot = scan_item(item)
        state.items[item["id"]] = asdict(snapshot)
        if snapshot.error is None:
            all_changes.extend(_diff_item(item, snapshot, previous_items.get(item["id"])))

    order = {"critical": 0, "warning": 1, "info": 2}
    all_changes.sort(key=lambda c: order.get(c.severity, 3))
    state.changes = [asdict(change) for change in all_changes]

    if all_changes and llm is not None:
        draft = draft_alert(state.changes, llm)
        if draft:
            state.alert_text = draft
            state.llm_used = True
    if state.alert_text is None and all_changes:
        state.alert_text = template_alert(state.changes)

    return state


def template_alert(changes: list[dict]) -> str:
    """Deterministic fallback alert (always available, no LLM required)."""
    critical = [c for c in changes if c["severity"] == "critical"]
    warnings = [c for c in changes if c["severity"] == "warning"]
    lines = [
        f"SupplyWatch detected {len(changes)} verified change(s) across the watchlist "
        f"({len(critical)} critical, {len(warnings)} warning)."
    ]
    for change in changes[:6]:
        lines.append(
            f"- [{change['severity'].upper()}] {change['item_name']} "
            f"({change['supplier']}): {change['detail']}"
        )
    if len(changes) > 6:
        lines.append(f"...and {len(changes) - 6} more.")
    lines.append("Every value above was verified against the supplier page text (quotes attached).")
    return "\n".join(lines)
