#!/usr/bin/env python3
"""Deterministically maintain the repository-owned candidate universe.

The LLM proposes sourced upsert/remove records in JSONL. This script validates,
expires, deduplicates, and caps them before atomically writing the pool.
"""
import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STATUS_PRIORITY = {
    "held": 0,
    "upcoming_catalyst": 1,
    "active_opportunity": 2,
    "monitor": 3,
}


def parse_date(value, field, symbol):
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{symbol}: {field} must be an ISO date") from None


def read_json(path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path):
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"updates line {line_number}: invalid JSON: {exc}") from None
    return records


def validate_upsert(update, today, rules):
    symbol = str(update.get("symbol", "")).strip().upper()
    if not symbol:
        raise ValueError("upsert missing symbol")
    status = update.get("status")
    if status not in rules["allowed_statuses"]:
        raise ValueError(f"{symbol}: invalid status {status!r}")
    for field in ("thesis_summary", "opportunity_type", "next_review_date", "expires_on"):
        if not update.get(field):
            raise ValueError(f"{symbol}: upsert missing {field}")
    next_review = parse_date(update["next_review_date"], "next_review_date", symbol)
    expires = parse_date(update["expires_on"], "expires_on", symbol)
    if expires < next_review:
        raise ValueError(f"{symbol}: expires_on precedes next_review_date")
    if status != "held" and expires < today:
        raise ValueError(f"{symbol}: cannot upsert an already-expired candidate")
    catalyst_date = update.get("catalyst_date")
    if status == "upcoming_catalyst" and not catalyst_date:
        raise ValueError(f"{symbol}: upcoming_catalyst requires catalyst_date")
    if catalyst_date:
        catalyst = parse_date(catalyst_date, "catalyst_date", symbol)
        if catalyst > today + dt.timedelta(days=rules["max_upcoming_catalyst_days"]):
            raise ValueError(f"{symbol}: catalyst_date exceeds configured horizon")
        if expires < catalyst:
            raise ValueError(f"{symbol}: expires_on must not precede catalyst_date")
    source_urls = update.get("source_urls", [])
    if status != "held" and not source_urls:
        raise ValueError(f"{symbol}: non-held candidate requires at least one source URL")
    if not isinstance(source_urls, list):
        raise ValueError(f"{symbol}: source_urls must be an array")
    if any(not isinstance(url, str) or not url.startswith(("https://", "http://"))
           for url in source_urls):
        raise ValueError(f"{symbol}: every source URL must be http(s)")
    return {
        "symbol": symbol,
        "status": status,
        "thesis_summary": update["thesis_summary"],
        "opportunity_type": update["opportunity_type"],
        "added_date": update.get("added_date", today.isoformat()),
        "last_reviewed_date": update.get("last_reviewed_date", today.isoformat()),
        "next_review_date": next_review.isoformat(),
        "expires_on": expires.isoformat(),
        "catalyst_date": catalyst_date,
        "source_urls": source_urls,
        "discovery_sources": update.get("discovery_sources", []),
        "last_signal_score": update.get("last_signal_score"),
    }


def update_pool(existing, updates, held_symbols, today, rules):
    candidates = {
        str(row["symbol"]).upper(): row
        for row in existing.get("candidates", [])
        if row.get("symbol")
    }
    for update in updates:
        action = update.get("action")
        symbol = str(update.get("symbol", "")).strip().upper()
        if action == "remove":
            candidates.pop(symbol, None)
        elif action == "upsert":
            candidates[symbol] = validate_upsert(update, today, rules)
        else:
            raise ValueError(f"{symbol or '<unknown>'}: action must be upsert or remove")

    held_symbols = {symbol.upper() for symbol in held_symbols}
    for symbol in held_symbols:
        if symbol in candidates:
            candidates[symbol]["status"] = "held"
            candidates[symbol]["expires_on"] = max(
                candidates[symbol].get("expires_on", today.isoformat()), today.isoformat()
            )
        else:
            candidates[symbol] = {
                "symbol": symbol,
                "status": "held",
                "thesis_summary": "Current Robinhood position; always review.",
                "opportunity_type": "position_monitoring",
                "added_date": today.isoformat(),
                "last_reviewed_date": today.isoformat(),
                "next_review_date": today.isoformat(),
                "expires_on": today.isoformat(),
                "catalyst_date": None,
                "source_urls": [],
                "discovery_sources": ["robinhood_position"],
                "last_signal_score": None,
            }

    for symbol in list(candidates):
        row = candidates[symbol]
        if symbol not in held_symbols and row.get("status") == "held":
            row["status"] = "monitor"
        expiry = parse_date(row["expires_on"], "expires_on", symbol)
        if symbol not in held_symbols and expiry < today:
            del candidates[symbol]

    ordered = sorted(
        candidates.values(),
        key=lambda row: (
            STATUS_PRIORITY.get(row.get("status"), 99),
            row.get("next_review_date", "9999-12-31"),
            -(row.get("last_signal_score") or 0),
            row["symbol"],
        ),
    )
    held = [row for row in ordered if row["status"] == "held"]
    non_held_limit = max(0, rules["max_candidates"] - len(held))
    non_held = [row for row in ordered if row["status"] != "held"][:non_held_limit]
    return {
        "version": 1,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidates": held + non_held,
    }


def atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "risk_rules.json")
    parser.add_argument("--universe", type=Path, default=ROOT / "candidate_universe.json")
    parser.add_argument("--updates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--today", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--held-symbols", default="")
    args = parser.parse_args()
    try:
        config = read_json(args.config, {})["persistent_candidate_pool"]
        existing = read_json(args.universe, {"version": 1, "candidates": []})
        updates = read_jsonl(args.updates)
        held = [value.strip().upper() for value in args.held_symbols.split(",") if value.strip()]
        payload = update_pool(existing, updates, held, args.today, config)
        atomic_write(args.output, payload)
        print(json.dumps({"valid": True, "candidate_count": len(payload["candidates"])}))
    except (KeyError, OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
