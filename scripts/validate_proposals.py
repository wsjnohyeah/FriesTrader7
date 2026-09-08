#!/usr/bin/env python3
"""Validate the richer Phase A JSONL contract before Phase B can consume it."""
import argparse
import json
import sys
from pathlib import Path


REQUIRED_THESIS_FIELDS = {
    "date", "timestamp", "symbol", "stage", "direction", "conviction",
    "executive_summary", "catalyst_and_timeline", "news_evidence",
    "technical_setup", "bull_case", "bear_case", "invalidation",
    "risk_flags", "data_gaps", "conviction_rationale",
}
VALID_DIRECTIONS = {"long", "avoid", "exit_existing"}
VALID_CONVICTIONS = {"high", "medium", "low"}


def validate_record(record, line_number, minimum_sources):
    errors = []
    if record.get("stage") != "thesis":
        return errors
    missing = sorted(REQUIRED_THESIS_FIELDS - record.keys())
    if missing:
        errors.append(f"line {line_number}: missing fields: {', '.join(missing)}")
    if record.get("direction") not in VALID_DIRECTIONS:
        errors.append(f"line {line_number}: invalid direction")
    if record.get("conviction") not in VALID_CONVICTIONS:
        errors.append(f"line {line_number}: invalid conviction")
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
            f"line {line_number}: pct_below_52wk_high is unsupported by the Alpaca-only "
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
            errors.extend(validate_record(record, line_number, args.minimum_sources))
    result = {"valid": not errors, "thesis_count": thesis_count, "errors": errors}
    print(json.dumps(result))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
