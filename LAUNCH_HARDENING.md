# Launch hardening status

This document records the safety boundary of the current GPT-6 Astra + Alpaca
data + Robinhood execution branch.

## Implemented in this repository

- Standalone risk calculators reject NaN, infinity, invalid price/capital/
  quantity domains, malformed conviction maps, and non-finite computed output.
- Ranking and sizing reject malformed or duplicate candidate symbols, so one
  symbol cannot consume allocation twice.
- A profitable sell enforces the full configured time lock regardless of the
  subsequent price. A loss sell remains locked until price is strictly below
  the sale price.
- Phase A records the exact positive finite thesis quote and timezone-aware
  quote timestamp, validates the full file, and atomically replaces proposals.
- Missing or invalid proposals halt entries while held-position stop and
  take-profit checks continue.
- Only fully validated dry-run dates count toward the live gate.
- Take-profit tiers are completed by reconciled broker fills, not by local
  decisions; partial fills retain an outstanding target remainder.
- Offline CLI regression tests cover bad data, overflow, thresholds, re-entry
  locks, duplicate candidates, ranking, sizing, stops, profit-taking, and trims.

Run:

```bash
python3 -m unittest discover -s tests -v
```

## Not implemented by the Markdown workflow

The phase files tell an agent what to do. They are not an independent,
transactional execution engine and cannot intercept a direct broker tool call.
Git commits are valuable audit exports, but a Git push cannot be atomic with a
Robinhood order acceptance.

Before unattended live execution, the deployment still needs:

1. a durable order intent persisted before submission;
2. a stable local intent identity and broker-supported idempotency/reconciliation;
3. a single-run lock covering decision and execution;
4. startup reconciliation of unknown, open, partial, filled, and rejected orders;
5. durable cumulative fills, remaining quantities, reserved shares/cash,
   holding-period identity, and take-profit completion;
6. tests for process failure after broker acceptance but before local logging.

SQLite transactions are sufficient for a single-host runner. A distributed
scheduler requires a shared durable store and lock. Until these controls are
implemented and tested against the actual Robinhood MCP, keep all
`execution.live_prerequisites` false and `execution.mode` set to `dry_run`.

Secrets belong only in ignored runtime configuration or a secret manager.
