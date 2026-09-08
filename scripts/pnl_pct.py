#!/usr/bin/env python3
# Part of FriesTrader (https://github.com/YizhiSong/FriesTrader)
# Copyright (c) 2026 Yizhi Song, MIT License -- see LICENSE
"""Compute Robinhood realized-P&L percentages and the entry-halt decision.

Phase B supplies daily and weekly realized profit/loss from Robinhood MCP. The
denominator remains the human-maintained starting_capital_usd.
"""
import argparse
import json
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--daily-pnl-usd", "--daily-realized-usd", dest="daily_pnl_usd",
                   type=float, required=True,
                   help="daily realized P&L from Robinhood MCP")
    p.add_argument("--weekly-pnl-usd", "--weekly-realized-usd", dest="weekly_pnl_usd",
                   type=float, required=True,
                   help="weekly realized P&L from Robinhood MCP")
    p.add_argument("--starting-capital-usd", type=float, required=True,
                    help="risk_rules.json starting_capital_usd")
    p.add_argument("--daily-limit-pct", type=float, required=True,
                    help="risk_rules.json loss_limits.daily_loss_limit_pct_of_account")
    p.add_argument("--weekly-limit-pct", type=float, required=True,
                    help="risk_rules.json loss_limits.weekly_loss_limit_pct_of_account")
    args = p.parse_args()

    if args.starting_capital_usd <= 0:
        print(json.dumps({"error": "starting_capital_usd must be positive"}), file=sys.stderr)
        sys.exit(1)

    daily_pnl_pct = args.daily_pnl_usd / args.starting_capital_usd
    weekly_pnl_pct = args.weekly_pnl_usd / args.starting_capital_usd

    daily_breach = (-daily_pnl_pct) >= args.daily_limit_pct
    weekly_breach = (-weekly_pnl_pct) >= args.weekly_limit_pct

    reasons = []
    if daily_breach:
        reasons.append(f"daily drawdown {(-daily_pnl_pct):.4%} >= daily limit {args.daily_limit_pct:.4%}")
    if weekly_breach:
        reasons.append(f"weekly drawdown {(-weekly_pnl_pct):.4%} >= weekly limit {args.weekly_limit_pct:.4%}")

    print(json.dumps({
        "daily_pnl_pct": round(daily_pnl_pct, 6),
        "weekly_pnl_pct": round(weekly_pnl_pct, 6),
        "entries_halted": daily_breach or weekly_breach,
        "halt_reason": "; ".join(reasons) if reasons else None,
    }))


if __name__ == "__main__":
    main()
