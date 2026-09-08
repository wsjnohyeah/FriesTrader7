#!/usr/bin/env python3
"""Small stdlib-only Alpaca REST client for the scheduled Codex tasks.

Credentials are read only from ALPACA_API_KEY_ID and
ALPACA_API_SECRET_KEY. The order command has an additional hard guard: it
will submit only to Alpaca's paper-api hostname and only when
--confirm-paper is present.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


PAPER_HOST = "paper-api.alpaca.markets"
DEFAULT_TRADING_BASE = "https://paper-api.alpaca.markets/v2"
DEFAULT_DATA_BASE = "https://data.alpaca.markets"


class AlpacaError(RuntimeError):
    pass


class AlpacaClient:
    def __init__(self):
        self.key_id = os.environ.get("ALPACA_API_KEY_ID")
        self.secret = os.environ.get("ALPACA_API_SECRET_KEY")
        if not self.key_id or not self.secret:
            raise AlpacaError(
                "missing ALPACA_API_KEY_ID or ALPACA_API_SECRET_KEY; "
                "set both as environment variables"
            )
        self.trading_base = os.environ.get(
            "ALPACA_TRADING_BASE_URL", DEFAULT_TRADING_BASE
        ).rstrip("/")
        self.data_base = os.environ.get(
            "ALPACA_DATA_BASE_URL", DEFAULT_DATA_BASE
        ).rstrip("/")
        self.feed = os.environ.get("ALPACA_DATA_FEED", "sip")

    @property
    def headers(self):
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret,
            "Accept": "application/json",
            "User-Agent": "FriesTrader7-Codex/1.0",
        }

    def request(self, base, path, params=None, method="GET", body=None):
        query = urllib.parse.urlencode(
            [(k, item) for k, value in (params or {}).items()
             for item in (value if isinstance(value, (list, tuple)) else [value])
             if item is not None],
            doseq=True,
        )
        url = f"{base}{path}"
        if query:
            url = f"{url}?{query}"
        payload = None
        headers = dict(self.headers)
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AlpacaError(f"Alpaca HTTP {exc.code} for {method} {path}: {detail}") from None
        except urllib.error.URLError as exc:
            raise AlpacaError(f"Alpaca request failed for {method} {path}: {exc.reason}") from None

    def trading_get(self, path, params=None):
        return self.request(self.trading_base, path, params=params)

    def data_get(self, path, params=None, paginate_key=None):
        params = dict(params or {})
        if not paginate_key:
            return self.request(self.data_base, path, params=params)

        combined = None
        while True:
            page = self.request(self.data_base, path, params=params)
            if combined is None:
                combined = page
            else:
                if isinstance(page.get(paginate_key), list):
                    combined[paginate_key].extend(page[paginate_key])
                elif isinstance(page.get(paginate_key), dict):
                    for symbol, values in page[paginate_key].items():
                        combined[paginate_key].setdefault(symbol, []).extend(values)
            token = page.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        combined.pop("next_page_token", None)
        return combined

    def submit_paper_order(self, body):
        parsed = urllib.parse.urlparse(self.trading_base)
        if parsed.scheme != "https" or parsed.hostname != PAPER_HOST:
            raise AlpacaError(
                "order blocked: ALPACA_TRADING_BASE_URL is not the approved "
                f"paper endpoint ({PAPER_HOST})"
            )
        return self.request(self.trading_base, "/orders", method="POST", body=body)


def csv_symbols(value):
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not symbols:
        raise argparse.ArgumentTypeError("at least one symbol is required")
    return symbols


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("account")
    sub.add_parser("positions")
    sub.add_parser("assets")
    sub.add_parser("watchlists")
    sub.add_parser("clock")

    calendar = sub.add_parser("calendar")
    calendar.add_argument("--start")
    calendar.add_argument("--end")

    watchlist = sub.add_parser("watchlist")
    watchlist.add_argument("--id", required=True)

    movers = sub.add_parser("movers")
    movers.add_argument("--top", type=int, default=50)

    active = sub.add_parser("most-actives")
    active.add_argument("--top", type=int, default=50)
    active.add_argument("--by", choices=["volume", "trades"], default="volume")

    snapshots = sub.add_parser("snapshots")
    snapshots.add_argument("--symbols", type=csv_symbols, required=True)

    bars = sub.add_parser("bars")
    bars.add_argument("--symbols", type=csv_symbols, required=True)
    bars.add_argument("--timeframe", default="1Day")
    bars.add_argument("--start", required=True)
    bars.add_argument("--end")
    bars.add_argument("--limit", type=int, default=10000)

    news = sub.add_parser("news")
    news.add_argument("--symbols", type=csv_symbols, required=True)
    news.add_argument("--start", required=True)
    news.add_argument("--end")
    news.add_argument("--limit", type=int, default=50)
    news.add_argument("--include-content", action="store_true")

    history = sub.add_parser("portfolio-history")
    history.add_argument("--period", required=True)
    history.add_argument("--timeframe", default="1D")
    history.add_argument("--date-end")

    activities = sub.add_parser("activities")
    activities.add_argument("--activity-types", default="FILL")
    activities.add_argument("--after")
    activities.add_argument("--until")
    activities.add_argument("--direction", choices=["asc", "desc"], default="desc")
    activities.add_argument("--page-size", type=int, default=100)

    orders = sub.add_parser("orders")
    orders.add_argument("--status", default="all")
    orders.add_argument("--after")
    orders.add_argument("--until")
    orders.add_argument("--symbols", type=csv_symbols)

    order_status = sub.add_parser("order-status")
    order_status.add_argument("--id", required=True)

    order = sub.add_parser("order")
    order.add_argument("--symbol", required=True)
    order.add_argument("--side", choices=["buy", "sell"], required=True)
    amount = order.add_mutually_exclusive_group(required=True)
    amount.add_argument("--qty")
    amount.add_argument("--notional")
    order.add_argument("--type", choices=["market", "limit", "stop", "stop_limit"], default="market")
    order.add_argument("--time-in-force", default="day")
    order.add_argument("--limit-price")
    order.add_argument("--stop-price")
    order.add_argument("--client-order-id")
    order.add_argument("--confirm-paper", action="store_true")
    return parser


def run(args, client):
    if args.command == "account":
        return client.trading_get("/account")
    if args.command == "positions":
        return client.trading_get("/positions")
    if args.command == "assets":
        return client.trading_get("/assets", {"status": "active", "asset_class": "us_equity"})
    if args.command == "watchlists":
        return client.trading_get("/watchlists")
    if args.command == "clock":
        return client.trading_get("/clock")
    if args.command == "calendar":
        return client.trading_get("/calendar", {"start": args.start, "end": args.end})
    if args.command == "watchlist":
        return client.trading_get(f"/watchlists/{urllib.parse.quote(args.id)}")
    if args.command == "movers":
        return client.data_get("/v1beta1/screener/stocks/movers", {"top": args.top})
    if args.command == "most-actives":
        return client.data_get(
            "/v1beta1/screener/stocks/most-actives", {"top": args.top, "by": args.by}
        )
    if args.command == "snapshots":
        return client.data_get(
            "/v2/stocks/snapshots",
            {"symbols": ",".join(args.symbols), "feed": client.feed},
        )
    if args.command == "bars":
        return client.data_get(
            "/v2/stocks/bars",
            {
                "symbols": ",".join(args.symbols), "timeframe": args.timeframe,
                "start": args.start, "end": args.end, "limit": args.limit,
                "adjustment": "all", "feed": client.feed, "sort": "asc",
            },
            paginate_key="bars",
        )
    if args.command == "news":
        return client.data_get(
            "/v1beta1/news",
            {
                "symbols": ",".join(args.symbols), "start": args.start,
                "end": args.end, "limit": args.limit, "sort": "desc",
                "include_content": str(args.include_content).lower(),
            },
            paginate_key="news",
        )
    if args.command == "portfolio-history":
        return client.trading_get(
            "/account/portfolio/history",
            {"period": args.period, "timeframe": args.timeframe, "date_end": args.date_end},
        )
    if args.command == "activities":
        activity_types = urllib.parse.quote(args.activity_types, safe=",")
        return client.trading_get(
            f"/account/activities/{activity_types}",
            {
                "after": args.after, "until": args.until,
                "direction": args.direction, "page_size": args.page_size,
            },
        )
    if args.command == "orders":
        return client.trading_get(
            "/orders",
            {
                "status": args.status, "after": args.after, "until": args.until,
                "symbols": ",".join(args.symbols) if args.symbols else None,
                "nested": "true", "direction": "desc",
            },
        )
    if args.command == "order-status":
        return client.trading_get(f"/orders/{urllib.parse.quote(args.id)}")
    if args.command == "order":
        if not args.confirm_paper:
            raise AlpacaError("order blocked: pass --confirm-paper after all risk checks pass")
        body = {
            "symbol": args.symbol.upper(), "side": args.side, "type": args.type,
            "time_in_force": args.time_in_force,
        }
        for key in ("qty", "notional", "limit_price", "stop_price", "client_order_id"):
            value = getattr(args, key)
            if value is not None:
                body[key] = value
        return client.submit_paper_order(body)
    raise AlpacaError(f"unsupported command: {args.command}")


def main():
    args = build_parser().parse_args()
    try:
        print(json.dumps(run(args, AlpacaClient()), separators=(",", ":")))
    except AlpacaError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
