#!/usr/bin/env python3
"""Validate the richer Phase A JSONL contract before Phase B can consume it."""
import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path


REQUIRED_THESIS_FIELDS = {
    "date", "timestamp", "symbol", "stage", "direction", "conviction",
    "executive_summary", "catalyst_and_timeline", "news_evidence",
    "technical_setup", "bull_case", "bear_case", "invalidation",
    "risk_flags", "data_gaps", "conviction_rationale",
    "current_price", "quote_asof",
}
VALID_DIRECTIONS = {"long", "avoid", "exit_existing"}
VALID_CONVICTIONS = {"high", "medium", "low"}


def nonfinite_path(value, path="record"):
    if isinstance(value, float) and not math.isfinite(value):
        return path
    if isinstance(value, dict):
        for key, item in value.items():
            found = nonfinite_path(item, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = nonfinite_path(item, f"{path}[{index}]")
            if found:
                return found
    return None


def timezone_aware_rfc3339(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_record(record, line_number, minimum_sources):
    errors = []
    bad_number = nonfinite_path(record)
    if bad_number:
        errors.append(f"line {line_number}: {bad_number} must be finite")
    if record.get("stage") != "thesis":
        return errors
    missing = sorted(REQUIRED_THESIS_FIELDS - record.keys())
    if missing:
        errors.append(f"line {line_number}: missing fields: {', '.join(missing)}")
    if record.get("direction") not in VALID_DIRECTIONS:
        errors.append(f"line {line_number}: invalid direction")
    if record.get("conviction") not in VALID_CONVICTIONS:
        errors.append(f"line {line_number}: invalid conviction")
    current_price = record.get("current_price")
    if (
        isinstance(current_price, bool)
        or not isinstance(current_price, (int, float))
        or not math.isfinite(current_price)
        or current_price <= 0
    ):
        errors.append(f"line {line_number}: current_price must be positive and finite")
    if not timezone_aware_rfc3339(record.get("quote_asof")):
        errors.append(f"line {line_number}: quote_asof must be timezone-aware RFC 3339")
    if not isinstance(record.get("technical_setup"), dict):
        errors.append(f"line {line_number}: technical_setup must be an object")
    for field in ("catalyst_and_timeline", "news_evidence", "bull_case", "bear_case", "invalidation", "risk_flags", "data_gaps"):
        if not isinstance(record.get(field), list):
            errors.append(f"line {line_number}: {field} must be an array")
    evidence = record.get("news_evidence", [])
    if isinstance(evidence, list) and len(evidence) < minimum_sources:
        errors.append(
            f"line {line_number}: news_evidence has {len(evidence)} source(s), "
            f"minimum is {minimum_sources}"
        )
    if record.get("direction") == "long" and "pct_below_52wk_high" in record:
        errors.append(
            f"line {line_number}: pct_below_52wk_high is unsupported by the hybrid "
            "pipeline; use technical_setup.breakout_vs_prior_20d_high_pct"
        )
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--minimum-sources", type=int, default=2)
    args = parser.parse_args()
    errors = []
    thesis_count = 0
    thesis_symbols = set()
    with args.path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: invalid JSON: {exc}")
                continue
            thesis_count += record.get("stage") == "thesis"
            if record.get("stage") == "thesis":
                symbol = str(record.get("symbol", "")).strip().upper()
                if symbol in thesis_symbols:
                    errors.append(f"line {line_number}: duplicate thesis symbol: {symbol}")
                elif symbol:
                    thesis_symbols.add(symbol)
            errors.extend(validate_record(record, line_number, args.minimum_sources))
    result = {"valid": not errors, "thesis_count": thesis_count, "errors": errors}
    print(json.dumps(result))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
