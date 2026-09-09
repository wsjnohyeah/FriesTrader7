"""Offline CLI regression tests. No broker, network, or real account data."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRY = [
    "--fresh-ask", "100", "--thesis-price", "100",
    "--entry-price-gap-max-pct", "0.03", "--daily-closes", "100,100,100",
    "--max-extension-pct", "0.10", "--today", "2026-09-08",
]
PNL = [
    "--daily-pnl-usd", "0", "--weekly-pnl-usd", "0",
    "--starting-capital-usd", "500", "--daily-limit-pct", "0.05",
    "--weekly-limit-pct", "0.10",
]
STOP = [
    "--average-cost", "100", "--current-price", "92", "--mode", "fixed",
    "--hard-stop-pct", "0.07",
]
PROFIT = [
    "--average-cost", "100", "--current-price", "160", "--quantity", "10",
    "--tiers", "0.15:0.25,0.30:0.25,0.50:0.25",
]
TRIM = [
    "--conviction", "low", "--current-position-value", "100",
    "--target-size", "30", "--overweight-trigger-pct", "0.25",
    "--prior-consecutive-low-overweight-cycles", "2",
    "--min-low-conviction-cycles", "3",
]
SIZE = [
    "--total-value", "500", "--cash-start", "500",
    "--concurrent-positions-start", "0", "--max-position-pct", "0.20",
    "--max-concurrent-positions", "4", "--min-cash-buffer-pct", "0.10",
    "--min-top-up-usd", "5", "--min-top-up-pct-of-target", "0.10",
    "--conviction-pct", "high:0.20,medium:0.12,low:0.06",
]
CANDIDATE = {
    "symbol": "EXAMPLE", "conviction": "high", "group": "new",
    "risk_flags": [], "signal_score": 2.0,
}


def changed(args, option, value):
    result = args.copy()
    result[result.index(option) + 1] = value
    return result


class RiskCalculatorTests(unittest.TestCase):
    def invoke(self, name, args=(), data=None):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / name), *args],
            input=json.dumps(data) if data is not None else "",
            text=True,
            capture_output=True,
            timeout=10,
        )

    def result(self, name, args=(), data=None):
        process = self.invoke(name, args, data)
        self.assertEqual(process.returncode, 0, process.stderr)

        def bad_constant(value):
            raise AssertionError(f"nonstandard JSON constant: {value}")

        return json.loads(process.stdout, parse_constant=bad_constant)

    def rejected(self, name, args=(), data=None):
        process = self.invoke(name, args, data)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(
            process.stdout, "", "invalid input must not emit an actionable decision"
        )
        self.assertTrue(process.stderr)

    def test_nonfinite_cli_inputs_fail_closed(self):
        cases = [
            ("entry_gate.py", ENTRY, "--fresh-ask", None),
            ("pnl_pct.py", PNL, "--daily-pnl-usd", None),
            ("stop_loss.py", STOP, "--current-price", None),
            ("take_profit.py", PROFIT, "--quantity", None),
            ("conviction_trim.py", TRIM, "--target-size", None),
            ("position_sizing.py", SIZE, "--total-value", [CANDIDATE]),
        ]
        for name, args, option, data in cases:
            for bad in ["nan", "inf", "-inf"]:
                with self.subTest(script=name, value=bad):
                    self.rejected(name, changed(args, option, bad), data)

    def test_nonfinite_nested_inputs_fail_closed(self):
        self.rejected(
            "entry_gate.py", changed(ENTRY, "--daily-closes", "100,nan,100")
        )
        self.rejected("stop_loss.py", STOP + ["--daily-closes", "100,inf"])
        self.rejected("take_profit.py", changed(PROFIT, "--tiers", "0.15:nan"))
        self.rejected(
            "position_sizing.py",
            changed(SIZE, "--conviction-pct", "high:nan,medium:0.12,low:0.06"),
            [CANDIDATE],
        )
        for name in ["rank_candidates.py", "position_sizing.py"]:
            args = SIZE if name == "position_sizing.py" else []
            self.rejected(name, args, [{**CANDIDATE, "signal_score": float("nan")}])
        self.rejected(
            "position_sizing.py",
            SIZE,
            [{**CANDIDATE, "group": "held", "current_position_value": float("inf")}],
        )

    def test_invalid_domains_fail_closed(self):
        for bad in ["0", "-10"]:
            self.rejected("entry_gate.py", changed(ENTRY, "--fresh-ask", bad))
            self.rejected("stop_loss.py", changed(STOP, "--average-cost", bad))
            self.rejected(
                "position_sizing.py", changed(SIZE, "--total-value", bad), [CANDIDATE]
            )
        self.rejected("take_profit.py", changed(PROFIT, "--tiers", "0.15:1.25"))
        self.rejected(
            "take_profit.py", changed(PROFIT, "--tiers", "0.15:0.25,0.15:0.25")
        )
        self.rejected("stop_loss.py", changed(STOP, "--hard-stop-pct", "1.5"))
        self.rejected("pnl_pct.py", changed(PNL, "--daily-limit-pct", "0"))
        self.rejected(
            "position_sizing.py", changed(SIZE, "--cash-start", "-1"), [CANDIDATE]
        )

    def test_duplicate_and_invalid_candidates_fail_closed(self):
        for name, args in [("rank_candidates.py", []), ("position_sizing.py", SIZE)]:
            self.rejected(name, args, [CANDIDATE, CANDIDATE])
            self.rejected(name, args, {"candidate": CANDIDATE})
            self.rejected(name, args, [{**CANDIDATE, "risk_flags": "none"}])
            self.rejected(name, args, [{**CANDIDATE, "signal_score": -1}])
        self.rejected(
            "position_sizing.py",
            SIZE,
            [{**CANDIDATE, "group": "held", "current_position_value": True}],
        )

    def test_overflow_never_emits_nonfinite_decision(self):
        args = changed(
            changed(ENTRY, "--fresh-ask", "1e308"), "--thesis-price", "1e-308"
        )
        self.rejected("entry_gate.py", args)

    def test_entry_threshold_wash_sale_and_future_date(self):
        self.assertTrue(self.result("entry_gate.py", ENTRY)["passed"])
        self.assertFalse(
            self.result("entry_gate.py", changed(ENTRY, "--fresh-ask", "104"))["passed"]
        )
        args = ENTRY + [
            "--wash-sale-enabled", "--wash-sale-lookback-days", "30",
            "--loss-sale-dates", "2026-09-07",
        ]
        self.assertTrue(
            self.result("entry_gate.py", args)["wash_sale_avoidance"]["blocked"]
        )
        self.rejected(
            "entry_gate.py",
            changed(args, "--loss-sale-dates", "2026-09-09"),
        )

    def test_profitable_sell_lock_is_time_based(self):
        for price in ["99", "100", "101"]:
            for elapsed in ["1", "9", "10"]:
                with self.subTest(price=price, elapsed=elapsed):
                    args = changed(ENTRY, "--fresh-ask", price) + [
                        "--last-sell-reason", "take_profit",
                        "--last-sell-price", "100", "--last-sell-date", "2026-08-01",
                        "--last-sell-was-gain", "true",
                        "--reentry-lock-max-trading-days", "10",
                        "--trading-days-since-sell", elapsed,
                    ]
                    result = self.result("entry_gate.py", args)
                    self.assertEqual(
                        result["sell_reentry_lock"]["blocked"], int(elapsed) < 10
                    )

    def test_loss_sell_lock_requires_price_below_sale(self):
        for price, expected in [("99", False), ("100", True), ("101", True)]:
            args = changed(ENTRY, "--fresh-ask", price) + [
                "--last-sell-reason", "stop_loss", "--last-sell-price", "100",
                "--last-sell-date", "2026-08-01", "--last-sell-was-gain", "false",
            ]
            self.assertEqual(
                self.result("entry_gate.py", args)["sell_reentry_lock"]["blocked"],
                expected,
            )

    def test_daily_loss_boundary_is_inclusive(self):
        self.assertFalse(
            self.result(
                "pnl_pct.py", changed(PNL, "--daily-pnl-usd", "-24.99")
            )["entries_halted"]
        )
        self.assertTrue(
            self.result("pnl_pct.py", changed(PNL, "--daily-pnl-usd", "-25"))[
                "entries_halted"
            ]
        )

    def test_fixed_and_trailing_stops(self):
        self.assertTrue(self.result("stop_loss.py", STOP)["triggered"])
        args = [
            "--average-cost", "100", "--current-price", "128", "--mode", "fixed",
            "--hard-stop-pct", "0.08", "--take-profit-tier-fired",
            "--daily-highs", "130,140",
        ]
        result = self.result("stop_loss.py", args)
        self.assertEqual(result["stop_reference_price"], 140)
        self.assertTrue(result["triggered"])

    def test_take_profit_cascades_and_skips_completed_tier(self):
        result = self.result("take_profit.py", PROFIT)
        self.assertEqual(
            [row["quantity_sold"] for row in result["fired_this_cycle"]],
            [2.5, 1.875, 1.40625],
        )
        result = self.result("take_profit.py", PROFIT + ["--already-fired", "0.15"])
        self.assertEqual(
            [row["tier_gain_pct"] for row in result["fired_this_cycle"]],
            [0.30, 0.50],
        )

    def test_trim_trigger(self):
        result = self.result("conviction_trim.py", TRIM)
        self.assertTrue(result["triggered"])
        self.assertEqual(result["trim_dollar_amount"], 70)

    def test_rank_size_capacity_and_halt(self):
        candidates = [
            {**CANDIDATE, "symbol": "MEDIUM", "conviction": "medium", "signal_score": 9},
            CANDIDATE,
        ]
        ranked = self.result("rank_candidates.py", data=candidates)
        self.assertEqual([row["symbol"] for row in ranked], ["EXAMPLE", "MEDIUM"])
        result = self.result("position_sizing.py", SIZE, ranked)
        self.assertEqual([row["dollar_amount"] for row in result["results"]], [100, 60])
        self.assertEqual(result["cash_remaining_final"], 340)
        result = self.result(
            "position_sizing.py", SIZE + ["--entries-halted"], ranked
        )
        self.assertTrue(all(not row["passed"] for row in result["results"]))
        self.assertEqual(result["cash_remaining_final"], 500)


if __name__ == "__main__":
    unittest.main()
