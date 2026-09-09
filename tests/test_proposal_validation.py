"""Offline tests for the Phase A proposal contract."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def thesis(symbol="EXAMPLE"):
    return {
        "date": "2026-09-08",
        "timestamp": "16:35:00",
        "symbol": symbol,
        "stage": "thesis",
        "direction": "long",
        "conviction": "medium",
        "conviction_rationale": "Confirmed evidence with unresolved limitations.",
        "executive_summary": "A sourced test thesis.",
        "catalyst_and_timeline": [],
        "news_evidence": [
            {"source": "Primary", "url": "https://example.com/a"},
            {"source": "Wire", "url": "https://example.com/b"},
        ],
        "technical_setup": {"signal_score": 2.0},
        "bull_case": ["Confirmed event"],
        "bear_case": ["Execution risk"],
        "invalidation": ["Event cancelled"],
        "risk_flags": [],
        "data_gaps": [],
        "current_price": 100.0,
        "quote_asof": "2026-09-08T20:00:00Z",
    }


class ProposalValidationTests(unittest.TestCase):
    def validate(self, records):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proposals.jsonl"
            path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            return subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "validate_proposals.py"), str(path)],
                text=True,
                capture_output=True,
                timeout=10,
            )

    def test_valid_thesis(self):
        process = self.validate([thesis()])
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_quote_metadata_is_required_and_finite(self):
        for field, value in [
            ("current_price", float("nan")),
            ("current_price", 0),
            ("quote_asof", "2026-09-08 20:00:00"),
        ]:
            with self.subTest(field=field, value=value):
                record = thesis()
                record[field] = value
                self.assertNotEqual(self.validate([record]).returncode, 0)

    def test_duplicate_thesis_symbol_is_rejected(self):
        process = self.validate([thesis("abc"), thesis("ABC")])
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("duplicate thesis symbol", process.stdout)


if __name__ == "__main__":
    unittest.main()
