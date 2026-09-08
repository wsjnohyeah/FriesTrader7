#!/usr/bin/env python3
"""Read-only, stdlib-only Alpaca Market Data client.

This module deliberately implements GET requests only and exposes no Alpaca
account, position, or order methods. Robinhood MCP is the sole execution
broker for FriesTrader7.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


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
        self.data_base = os.environ.get(
            "ALPACA_DATA_BASE_URL", DEFAULT_DATA_BASE
        ).rstrip("/")
        parsed = urllib.parse.urlparse(self.data_base)
        if parsed.scheme != "https" or parsed.hostname != "data.alpaca.markets":
            raise AlpacaError(
                "ALPACA_DATA_BASE_URL must use https://data.alpaca.markets"
            )
        self.feed = os.environ.get("ALPACA_DATA_FEED", "sip")

    @property
    def headers(self):
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret,
            "Accept": "application/json",
            "User-Agent": "FriesTrader7-Codex/1.0",
        }

    def data_get(self, path, params=None, paginate_key=None):
        params = dict(params or {})
        combined = None
        while True:
            query = urllib.parse.urlencode(
                [(key, value) for key, value in params.items() if value is not None]
            )
            url = f"{self.data_base}{path}"
            if query:
                url = f"{url}?{query}"
            request = urllib.request.Request(url, headers=self.headers, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read()
                    page = json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise AlpacaError(f"Alpaca HTTP {exc.code} for GET {path}: {detail}") from None
            except urllib.error.URLError as exc:
                raise AlpacaError(f"Alpaca request failed for GET {path}: {exc.reason}") from None

            if not paginate_key:
                return page
            if combined is None:
                combined = page
            elif isinstance(page.get(paginate_key), list):
                combined[paginate_key].extend(page[paginate_key])
            elif isinstance(page.get(paginate_key), dict):
                for symbol, values in page[paginate_key].items():
                    combined[paginate_key].setdefault(symbol, []).extend(values)
            token = page.get("next_page_token")
            if not token:
                combined.pop("next_page_token", None)
                return combined
            params["page_token"] = token


def csv_symbols(value):
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not symbols:
        raise argparse.ArgumentTypeError("at least one symbol is required")
    return symbols


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

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
    news.add_argument("--symbols", type=csv_symbols)
    news.add_argument("--start", required=True)
    news.add_argument("--end")
    news.add_argument("--limit", type=int, default=50)
    news.add_argument("--include-content", action="store_true")
    return parser


def run(args, client):
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
                "symbols": ",".join(args.symbols) if args.symbols else None,
                "start": args.start, "end": args.end, "limit": args.limit,
                "sort": "desc", "include_content": str(args.include_content).lower(),
            },
            paginate_key="news",
        )
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
