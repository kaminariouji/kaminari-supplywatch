"""Optional OpenAI-compatible LLM client (works with an AMD-hosted endpoint).

The LLM drafts the human-readable alert text. It can never change a value or
add a claim that is not backed by the verified fields passed to it.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass


@dataclass
class LLMConfig:
    base_url: str          # e.g. https://api.example.com  (OpenAI-compatible)
    api_key: str
    model: str
    timeout: float = 60.0


SYSTEM_PROMPT = (
    "You are SupplyWatch, a procurement early-warning assistant. "
    "You receive VERIFIED change records (each with the exact quote from the "
    "supplier page). Write a short operational alert for a factory buyer: "
    "what changed, why it matters, one recommended action. "
    "Rules: use ONLY the provided values; never invent numbers, dates, or "
    "suppliers; keep it under 120 words; plain text, no markdown headers."
)


def draft_alert(changes: list[dict], cfg: LLMConfig) -> str | None:
    """Return LLM-drafted alert text, or None on any failure (caller falls back)."""
    user = "Verified change records (JSON):\n" + json.dumps(changes, indent=2)
    body = json.dumps(
        {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 300,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        cfg.base_url.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=cfg.timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        text = payload["choices"][0]["message"]["content"].strip()
        return text or None
    except Exception:  # noqa: BLE001 - LLM is best-effort, never fatal
        return None
