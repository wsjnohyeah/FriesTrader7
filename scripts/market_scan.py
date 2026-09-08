#!/usr/bin/env python3
"""Build a broad Alpaca candidate universe and compute technical signals.

The script is deterministic: it combines Alpaca movers, most-actives, recent
news symbols, configured seeds, the persistent candidate pool, and Robinhood
watchlist/position symbols passed by the caller; then enriches them with SIP
snapshots and daily bars. It prints one JSON document and never places orders.
"""
import argparse
import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path

from alpaca_api import AlpacaClient, AlpacaError, csv_symbols


ROOT = Path(__file__).resolve().parent.parent


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def pct(numerator, denominator):
    return numerator / denominator if denominator not in (None, 0) else None


def mean(values):
    values = [value for value in values if value is not None]
    return statistics.fmean(values) if values else None


def sma(closes, length):
    return mean(closes[-length:]) if len(closes) >= length else None


def rsi14(closes):
    if len(closes) < 15:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(len(closes) - 14, len(closes))]
    avg_gain = mean([max(change, 0.0) for change in changes])
    avg_loss = mean([max(-change, 0.0) for change in changes])
    if avg_loss == 0:
        return 100.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def atr14_pct(bars, price):
    if len(bars) < 15 or not price:
        return None
    recent = bars[-15:]
    ranges = []
    for index in range(1, len(recent)):
        high = number(recent[index].get("h"))
        low = number(recent[index].get("l"))
        previous_close = number(recent[index - 1].get("c"))
        if None in (high, low, previous_close):
            continue
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    average_true_range = mean(ranges)
    return pct(average_true_range, price)


def snapshot_map(payload):
    if isinstance(payload, dict) and isinstance(payload.get("snapshots"), dict):
        return payload["snapshots"]
    return payload if isinstance(payload, dict) else {}


def collect_symbols(client, rules, held_symbols, watchlist_symbols, candidate_symbols):
    sources = {}

    def add(symbol, source):
        if symbol:
            sources.setdefault(str(symbol).upper(), set()).add(source)

    universe = rules["universe"]
    for symbol in universe.get("seed_symbols", []):
        add(symbol, "seed")

    if universe.get("include_open_positions", True):
        for symbol in held_symbols:
            add(symbol, "held")
    for symbol in watchlist_symbols:
        add(symbol, "watchlist")
    for symbol in candidate_symbols:
        add(symbol, "persistent_pool")

    discovery = universe["dynamic_discovery"]
    if discovery.get("enabled", True):
        movers = client.data_get(
            "/v1beta1/screener/stocks/movers", {"top": discovery["movers_top"]}
        )
        for row in movers.get("gainers", []):
            add(row.get("symbol"), "market_gainer")
        for row in movers.get("losers", []):
            add(row.get("symbol"), "market_loser")

        actives = client.data_get(
            "/v1beta1/screener/stocks/most-actives",
            {"top": discovery["most_actives_top"], "by": discovery["most_actives_by"]},
        )
        for row in actives.get("most_actives", []):
            add(row.get("symbol"), "most_active")

        news_start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
        recent_news = client.data_get(
            "/v1beta1/news",
            {
                "start": news_start, "limit": discovery["recent_news_limit"],
                "sort": "desc", "include_content": "false",
            },
        )
        for article in recent_news.get("news", []):
            for symbol in article.get("symbols", []):
                add(symbol, "recent_news")

    return sources


def fetch_market_data(client, symbols, history_days):
    snapshots = {}
    bars = {}
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=history_days)).isoformat()
    end = dt.datetime.now(dt.timezone.utc).isoformat()
    for batch in chunks(sorted(symbols), 100):
        snapshots.update(snapshot_map(client.data_get(
            "/v2/stocks/snapshots",
            {"symbols": ",".join(batch), "feed": client.feed},
        )))
        payload = client.data_get(
            "/v2/stocks/bars",
            {
                "symbols": ",".join(batch), "timeframe": "1Day", "start": start,
                "end": end, "limit": 10000, "adjustment": "all",
                "feed": client.feed, "sort": "asc",
            },
            paginate_key="bars",
        )
        bars.update(payload.get("bars", {}))
    return snapshots, bars


def evaluate(symbol, sources, snapshot, history, rules, held):
    universe = rules["universe"]
    technical = rules["technical_scan"]
    current_bar = snapshot.get("dailyBar") or {}
    previous_bar = snapshot.get("prevDailyBar") or {}
    latest_trade = snapshot.get("latestTrade") or {}
    price = number(latest_trade.get("p")) or number(current_bar.get("c"))
    previous_close = number(previous_bar.get("c"))
    today_open = number(current_bar.get("o"))
    today_high = number(current_bar.get("h"))
    today_low = number(current_bar.get("l"))
    today_volume = number(current_bar.get("v"))

    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    prior_bars = [bar for bar in history if str(bar.get("t", ""))[:10] != today]
    closes = [number(bar.get("c")) for bar in prior_bars]
    closes = [value for value in closes if value is not None]
    volumes = [number(bar.get("v")) for bar in prior_bars[-20:]]
    dollar_volumes = [
        number(bar.get("c")) * number(bar.get("v"))
        for bar in prior_bars[-20:]
        if number(bar.get("c")) is not None and number(bar.get("v")) is not None
    ]
    average_volume_20d = mean(volumes)
    average_dollar_volume_20d = mean(dollar_volumes)

    daily_move = pct(price - previous_close, previous_close) if None not in (price, previous_close) else None
    opening_gap = pct(today_open - previous_close, previous_close) if None not in (today_open, previous_close) else None
    intraday_range = pct(today_high - today_low, previous_close) if None not in (today_high, today_low, previous_close) else None
    relative_volume = pct(today_volume, average_volume_20d)
    price_move_20d = pct(price - closes[-20], closes[-20]) if price and len(closes) >= 20 else None
    recent_highs = [number(bar.get("h")) for bar in prior_bars[-20:]]
    recent_highs = [value for value in recent_highs if value is not None]
    prior_high_20d = max(recent_highs, default=None)
    breakout_20d = pct(price - prior_high_20d, prior_high_20d) if None not in (price, prior_high_20d) else None
    closes_with_current = closes + ([price] if price else [])

    metrics = {
        "price": price,
        "previous_close": previous_close,
        "daily_move_pct": daily_move,
        "opening_gap_pct": opening_gap,
        "intraday_range_pct": intraday_range,
        "relative_volume": relative_volume,
        "price_move_20d_pct": price_move_20d,
        "breakout_vs_prior_20d_high_pct": breakout_20d,
        "average_daily_dollar_volume_20d": average_dollar_volume_20d,
        "sma20": sma(closes_with_current, 20),
        "sma50": sma(closes_with_current, 50),
        "sma200": sma(closes_with_current, 200),
        "rsi14": rsi14(closes_with_current),
        "atr14_pct": atr14_pct(prior_bars + ([current_bar] if current_bar else []), price),
    }

    failed = []
    if not price:
        failed.append("missing_current_price")
    elif price < universe["min_price_usd"]:
        failed.append("below_min_price")
    if average_dollar_volume_20d is None:
        failed.append("missing_liquidity_history")
    elif average_dollar_volume_20d < universe["min_avg_daily_dollar_volume_usd"]:
        failed.append("below_min_average_dollar_volume")

    signal_tests = {
        "daily_move": daily_move is not None and abs(daily_move) >= technical["daily_move_abs_pct"],
        "opening_gap": opening_gap is not None and abs(opening_gap) >= technical["opening_gap_abs_pct"],
        "intraday_range": intraday_range is not None and intraday_range >= technical["intraday_range_pct"],
        "relative_volume": relative_volume is not None and relative_volume >= technical["relative_volume_multiple"],
        "price_move_20d": price_move_20d is not None and abs(price_move_20d) >= technical["price_move_20d_abs_pct"],
        "breakout_20d": breakout_20d is not None and breakout_20d >= technical["breakout_20d_buffer_pct"],
    }
    thresholds = {
        "daily_move": technical["daily_move_abs_pct"],
        "opening_gap": technical["opening_gap_abs_pct"],
        "intraday_range": technical["intraday_range_pct"],
        "relative_volume": technical["relative_volume_multiple"],
        "price_move_20d": technical["price_move_20d_abs_pct"],
        "breakout_20d": technical["breakout_20d_buffer_pct"],
    }
    actuals = {
        "daily_move": abs(daily_move) if daily_move is not None else None,
        "opening_gap": abs(opening_gap) if opening_gap is not None else None,
        "intraday_range": intraday_range,
        "relative_volume": relative_volume,
        "price_move_20d": abs(price_move_20d) if price_move_20d is not None else None,
        "breakout_20d": max(breakout_20d or 0.0, 0.0),
    }
    score = 0.0
    for name, triggered in signal_tests.items():
        if triggered and thresholds[name] > 0:
            score += rules["technical_scan"]["rank_weights"][name] * actuals[name] / thresholds[name]

    passed_filters = not failed or held
    selected = held or (
        passed_filters and sum(signal_tests.values()) >= technical["minimum_signals_required"]
    )
    return {
        "symbol": symbol,
        "sources": sorted(sources),
        "held": held,
        "passed_filters": passed_filters,
        "filter_failures": failed,
        "signals": [name for name, triggered in signal_tests.items() if triggered],
        "signal_score": round(score, 6),
        "selected_for_research": selected,
        "metrics": {key: round(value, 6) if isinstance(value, float) else value for key, value in metrics.items()},
    }


def scan(rules, held_symbols, watchlist_symbols, candidate_symbols):
    client = AlpacaClient()
    sources = collect_symbols(
        client, rules, held_symbols, watchlist_symbols, candidate_symbols
    )
    snapshots, histories = fetch_market_data(
        client, sources, rules["technical_scan"]["daily_history_calendar_days"]
    )
    held_symbols = set(held_symbols)
    candidates = [
        evaluate(
            symbol, source_set, snapshots.get(symbol, {}),
            histories.get(symbol, []), rules, symbol in held_symbols,
        )
        for symbol, source_set in sources.items()
    ]
    candidates.sort(key=lambda row: (not row["held"], -row["signal_score"], row["symbol"]))

    dynamic_limit = rules["universe"]["dynamic_discovery"]["max_dynamic_candidates_after_ranking"]
    protected = [row for row in candidates if row["held"] or any(
        source in row["sources"] for source in ("seed", "watchlist", "persistent_pool")
    )]
    dynamic = [row for row in candidates if row not in protected and row["selected_for_research"]]
    kept_symbols = {row["symbol"] for row in protected + dynamic[:dynamic_limit]}
    candidates = [row for row in candidates if row["symbol"] in kept_symbols]

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "feed": client.feed,
        "candidate_count": len(candidates),
        "selected_for_research_count": sum(row["selected_for_research"] for row in candidates),
        "candidates": candidates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "risk_rules.json")
    parser.add_argument("--held-symbols", type=csv_symbols, default=[],
                        help="comma-separated symbols from Robinhood MCP positions")
    parser.add_argument("--watchlist-symbols", type=csv_symbols, default=[],
                        help="comma-separated symbols from the configured Robinhood watchlist")
    parser.add_argument("--candidate-symbols", type=csv_symbols, default=[],
                        help="comma-separated symbols from candidate_universe.json")
    args = parser.parse_args()
    try:
        with args.config.open(encoding="utf-8") as handle:
            rules = json.load(handle)
        print(json.dumps(
            scan(rules, args.held_symbols, args.watchlist_symbols, args.candidate_symbols),
            separators=(",", ":"),
        ))
    except (AlpacaError, KeyError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
