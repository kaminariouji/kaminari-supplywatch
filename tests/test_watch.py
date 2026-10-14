"""End-to-end tests for the SupplyWatch pipeline (stdlib unittest, no deps).

Run from the repository root:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.extract import extract_fields, html_to_text  # noqa: E402
from agent.verify import verify_fields  # noqa: E402
from agent.watch import (  # noqa: E402
    lead_weeks,
    price_number,
    scan_watchlist,
    stock_status,
    template_alert,
)

FIXTURES = ROOT / "tests" / "fixtures"


def _watchlist_for(variant: str) -> dict:
    """Watchlist pointing at the given fixture variant (v1 or v2)."""
    fixture = str(FIXTURES / f"supplier_{variant}.html")
    return {
        "name": f"fixture-{variant}",
        "items": [
            {
                "id": "part-x",
                "name": "Fixture part X",
                "supplier": "Fixture Supplier GmbH",
                "url": fixture,
                "fields": {
                    "price": ["price"],
                    "lead_time": ["lead time"],
                    "stock": ["stock"],
                    "moq": ["minimum order"],
                },
            }
        ],
    }


class TestValueParsing(unittest.TestCase):
    def test_price_number_us_format(self) -> None:
        self.assertEqual(price_number("EUR 1,234.56"), 1234.56)

    def test_price_number_eu_format(self) -> None:
        self.assertEqual(price_number("1.234,56 EUR"), 1234.56)

    def test_price_number_simple(self) -> None:
        self.assertEqual(price_number("GBP 12.50"), 12.5)

    def test_price_number_none_on_garbage(self) -> None:
        self.assertIsNone(price_number("contact us"))

    def test_lead_weeks_weeks(self) -> None:
        self.assertEqual(lead_weeks("Lead time: 6 weeks"), 6.0)

    def test_lead_weeks_days(self) -> None:
        self.assertAlmostEqual(lead_weeks("5 days"), 5 / 7, places=4)

    def test_lead_weeks_range(self) -> None:
        self.assertAlmostEqual(lead_weeks("3-5 days"), 4 / 7, places=4)

    def test_stock_status_out(self) -> None:
        self.assertEqual(stock_status("Out of stock"), "out")

    def test_stock_status_backorder(self) -> None:
        self.assertEqual(stock_status("backordered"), "backorder")

    def test_stock_status_in(self) -> None:
        self.assertEqual(stock_status("in stock (480 units)"), "in")


class TestExtractionAndVerification(unittest.TestCase):
    def _text(self, variant: str) -> str:
        return html_to_text((FIXTURES / f"supplier_{variant}.html").read_text("utf-8"))

    def test_html_to_text_strips_scripts(self) -> None:
        text = html_to_text("<p>hello</p><script>var x = 1;</script><style>p{}</style>")
        self.assertIn("hello", text)
        self.assertNotIn("var x", text)
        self.assertNotIn("p{}", text)

    def test_extract_v1_fields(self) -> None:
        extraction = extract_fields(
            self._text("v1"),
            {
                "price": ["price"],
                "lead_time": ["lead time"],
                "stock": ["stock"],
                "moq": ["minimum order"],
            },
        )
        values = {hit.kind: hit.value for hit in extraction.hits}
        self.assertEqual(values["price"], "EUR 9.99")
        self.assertEqual(values["lead_time"], "3 weeks")
        self.assertEqual(values["stock"], "in stock (120 units)")
        self.assertEqual(values["moq"], "25")

    def test_every_hit_carries_a_quote(self) -> None:
        text = self._text("v1")
        extraction = extract_fields(text, {"price": ["price"], "stock": ["stock"]})
        self.assertTrue(extraction.hits)
        for hit in extraction.hits:
            self.assertTrue(hit.quote, f"{hit.kind} hit without a quote")

    def test_verify_keeps_true_quote_drops_fake(self) -> None:
        from agent.extract import FieldHit

        text = "Price: EUR 9.99 per unit"
        good = FieldHit(kind="price", value="EUR 9.99", quote="EUR 9.99", context=text)
        fake = FieldHit(kind="price", value="EUR 1.00", quote="EUR 1.00", context=text)
        report = verify_fields([good, fake], text)
        self.assertEqual([vf.value for vf in report.verified], ["EUR 9.99"])
        self.assertEqual([hit.value for hit in report.dropped], ["EUR 1.00"])

    def test_verify_tolerates_whitespace_and_entities(self) -> None:
        from agent.extract import FieldHit

        page = "<td>In&nbsp;stock (120 units)</td>"
        text = html_to_text(page)
        hit = FieldHit(kind="stock", value="in stock (120 units)", quote="in stock (120 units)", context=text)
        report = verify_fields([hit], text)
        self.assertEqual(len(report.verified), 1)


class TestScanPipeline(unittest.TestCase):
    def test_first_scan_records_no_changes(self) -> None:
        state = scan_watchlist(_watchlist_for("v1"), previous_state=None)
        self.assertEqual(state.changes, [])
        self.assertIsNone(state.alert_text)
        snap = state.items["part-x"]
        self.assertEqual(snap["error"], None)
        self.assertIn("price", snap["fields"])
        self.assertEqual(snap["fields"]["price"]["value"], "EUR 9.99")

    def test_second_scan_detects_verified_changes(self) -> None:
        first = scan_watchlist(_watchlist_for("v1"))
        second = scan_watchlist(_watchlist_for("v2"), previous_state={
            "items": first.items,
        })
        changes = {c["field"]: c for c in second.changes}
        self.assertEqual(set(changes), {"price", "lead_time", "stock"})

        price = changes["price"]
        self.assertEqual(price["severity"], "critical")  # +15.0%
        self.assertIn("+15.0%", price["detail"])
        self.assertEqual(price["quote"], "EUR 11.49")

        lead = changes["lead_time"]
        self.assertEqual(lead["severity"], "critical")  # 3 -> 7 weeks = +133%
        self.assertIn("+133%", lead["detail"])

        stock = changes["stock"]
        self.assertEqual(stock["severity"], "critical")
        self.assertIn("availability dropped", stock["detail"])

        # unchanged moq must NOT be reported
        self.assertNotIn("moq", changes)

    def test_changes_sorted_critical_first(self) -> None:
        first = scan_watchlist(_watchlist_for("v1"))
        second = scan_watchlist(_watchlist_for("v2"), previous_state={"items": first.items})
        severities = [c["severity"] for c in second.changes]
        self.assertEqual(severities, sorted(severities, key=["critical", "warning", "info"].index))

    def test_alert_falls_back_to_template_without_llm(self) -> None:
        first = scan_watchlist(_watchlist_for("v1"))
        second = scan_watchlist(_watchlist_for("v2"), previous_state={"items": first.items})
        self.assertFalse(second.llm_used)
        self.assertIsNotNone(second.alert_text)
        self.assertIn("critical", second.alert_text.lower())
        self.assertIn("Fixture part X", second.alert_text)

    def test_fetch_error_is_reported_not_crashed(self) -> None:
        broken = _watchlist_for("v1")
        broken["items"][0]["url"] = str(FIXTURES / "does-not-exist.html")
        state = scan_watchlist(broken, previous_state=None)
        snap = state.items["part-x"]
        self.assertIsNotNone(snap["error"])
        self.assertEqual(state.changes, [])

    def test_scan_state_serializes_to_json(self) -> None:
        state = scan_watchlist(_watchlist_for("v1"))
        payload = json.dumps(state.__dict__)
        restored = json.loads(payload)
        self.assertEqual(restored["items"]["part-x"]["fields"]["price"]["value"], "EUR 9.99")


class TestTemplateAlert(unittest.TestCase):
    def test_template_lists_changes_and_count(self) -> None:
        changes = [
            {
                "severity": "critical",
                "item_name": "Valve DN50",
                "supplier": "Acme",
                "detail": "price moved +19.2% (GBP 12.50 -> GBP 14.90)",
            },
            {
                "severity": "warning",
                "item_name": "Bearing 6205",
                "supplier": "Acme",
                "detail": "price moved +4.7% (GBP 3.20 -> GBP 3.35)",
            },
        ]
        text = template_alert(changes)
        self.assertIn("2 verified change(s)", text)
        self.assertIn("1 critical, 1 warning", text)
        self.assertIn("Valve DN50", text)
        self.assertIn("quotes attached", text)


if __name__ == "__main__":
    unittest.main()
