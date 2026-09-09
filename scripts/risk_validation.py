"""Shared fail-closed input/output checks for standalone risk calculators."""
import json
import math
import sys


def reject(message):
    print(json.dumps({"error": message}), file=sys.stderr)
    raise SystemExit(1)


def validate_finite(value, path="input"):
    if isinstance(value, float) and not math.isfinite(value):
        reject(f"{path} must be finite")
    if isinstance(value, dict):
        for key, item in value.items():
            validate_finite(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            validate_finite(item, f"{path}[{index}]")


def validate_args(args, *, positive=(), nonnegative=(), fractions=(), prices=()):
    values = vars(args)
    validate_finite(values)
    for field in positive:
        value = values[field]
        if value is not None and value <= 0:
            reject(f"{field} must be positive")
    for field in nonnegative:
        value = values[field]
        if value is not None and value < 0:
            reject(f"{field} must be nonnegative")
    for field in fractions:
        value = values[field]
        if value is not None and not 0 <= value <= 1:
            reject(f"{field} must be between zero and one")
    for field in prices:
        if any(value <= 0 for value in values[field]):
            reject(f"{field} must contain positive prices")


def emit(value):
    validate_finite(value, "output")
    print(json.dumps(value, allow_nan=False))


def load_candidates(stream, *, sizing=False):
    try:
        candidates = json.load(stream)
    except (ValueError, TypeError) as error:
        reject(f"invalid candidates JSON: {error}")
    validate_finite(candidates)
    if not isinstance(candidates, list):
        reject("candidates must be an array")
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            reject("every candidate must be an object")
        symbol = candidate.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            reject("candidate symbol must be a nonempty string")
        symbol_key = symbol.strip().upper()
        if symbol_key in seen:
            reject(f"duplicate candidate symbol: {symbol_key}")
        seen.add(symbol_key)
        if candidate.get("conviction") not in {"high", "medium", "low"}:
            reject(f"invalid conviction for {symbol}")
        flags = candidate.get("risk_flags")
        if flags is not None and (
            not isinstance(flags, list)
            or any(not isinstance(flag, str) for flag in flags)
        ):
            reject(f"risk_flags must be an array of strings for {symbol}")
        score = candidate.get("signal_score")
        if score is not None and (
            isinstance(score, bool) or not isinstance(score, (int, float))
        ):
            reject(f"signal_score must be numeric for {symbol}")
        if score is not None and score < 0:
            reject(f"signal_score must be nonnegative for {symbol}")
        if sizing:
            if candidate.get("group") not in {"new", "held"}:
                reject(f"invalid group for {symbol}")
            if candidate["group"] == "held":
                value = candidate.get("current_position_value")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value <= 0
                ):
                    reject(
                        f"current_position_value must be a positive number for {symbol}"
                    )
    return candidates
