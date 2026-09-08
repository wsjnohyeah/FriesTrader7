# FriesTrader7 — Codex + Alpaca paper-trading workflow

FriesTrader7 is a two-phase, auditable AI trading workflow. Codex performs
after-close market discovery and evidence-based analysis; deterministic Python
scripts enforce entry, exit, ranking, and sizing rules; Alpaca supplies SIP
market data, news, account state, and paper order execution.

This repository is intentionally paper-only. It is a research and automation
template, not evidence that LLM-selected stocks outperform an index.

## Architecture

```text
Alpaca movers + most actives + watchlist + open positions
                         │
                         ▼
 Phase A (after close, Codex gpt-6-astra)
 dynamic scan → technical metrics → news/filing research → detailed thesis
                         │
                         ▼
              pending_proposals.jsonl
                         │
                         ▼
 Phase B (after next open, Codex gpt-6-astra)
 fresh prices → exits → entry gates → ranking/sizing → paper orders
                         │
                         ▼
          trade_log.jsonl + trade_log_recent.md
```

The selected model is configured in the Codex automation, not inside a prompt.
The task specifications require `gpt-6-astra` and instruct the run to report a
configuration error if that model is unavailable rather than silently using a
different provider.

This is the Codex-native integration path: the scheduled Codex task itself is
the GPT-6 Astra agent. It does not require an OpenAI API key in the repository.
If you later replace Codex scheduling with a standalone service that calls the
OpenAI Responses API, that service will need a separate `OPENAI_API_KEY` and
API billing; a ChatGPT subscription and API usage are separate products.

## What is deterministic

The model researches and synthesizes evidence. It does not decide risk math:

- `market_scan.py` discovers broad-market candidates and computes technicals.
- `entry_gate.py` enforces gap, extension, wash-sale, and re-entry locks.
- `stop_loss.py` computes fixed or volatility-scaled stops.
- `take_profit.py` computes cascading partial exits.
- `conviction_trim.py` mechanically reduces persistent low-conviction exposure.
- `pnl_pct.py` enforces daily and weekly account-loss limits.
- `rank_candidates.py` ranks by conviction, risk flags, and technical score.
- `position_sizing.py` applies position, slot, cash-buffer, and top-up limits.
- `validate_proposals.py` prevents incomplete detailed theses from being
  published to Phase B.

All scripts use the Python standard library.

## Broader stock discovery

Phase A is not limited to a hand-maintained watchlist. Every run merges:

- Alpaca whole-market gainers and losers;
- Alpaca whole-market most-active stocks;
- symbols in the latest market-wide Alpaca news feed;
- an optional Alpaca watchlist;
- optional seed symbols in `risk_rules.json`;
- every current position.

It then uses SIP snapshots and adjusted history to calculate:

- daily movement and opening gap;
- intraday high/low range;
- relative volume and average dollar volume;
- 20-day momentum and breakout;
- SMA20/SMA50/SMA200;
- RSI14 and ATR14 percentage.

Dynamic candidates are ranked by a disclosed technical signal score. Open
positions are always retained. Thresholds and source caps are configurable in
`risk_rules.json`.

Alpaca Pro market data is not a complete company-fundamentals service. The
workflow must not invent market cap, P/E, earnings estimates, balance-sheet
figures, or similar data. Verified filing facts may be used when cited;
otherwise the missing item is recorded under `data_gaps`.

## More detailed LLM analysis

Each researched symbol now requires:

- a decision-oriented executive summary;
- catalyst chronology and confirmation status;
- source-by-source news evidence with timestamps and URLs;
- trend, momentum, volume, volatility, and observed technical levels;
- explicit bull and bear cases;
- concrete invalidation conditions;
- fixed risk flags;
- material data gaps;
- an explanation for the exact conviction tier.

At least two independent sources are required by default. High conviction also
requires a confirmed company-specific catalyst, a primary or wire source,
corroboration, a supportive technical setup, and no unresolved material risk
flag or data gap.

## Alpaca authentication

Never put credentials in this repository or in a scheduled prompt. Create or
rotate an Alpaca paper API key and expose both values to the Codex runtime:

```bash
export ALPACA_API_KEY_ID='your_new_key_id'
export ALPACA_API_SECRET_KEY='your_new_secret'
export ALPACA_TRADING_BASE_URL='https://paper-api.alpaca.markets/v2'
export ALPACA_DATA_BASE_URL='https://data.alpaca.markets'
export ALPACA_DATA_FEED='sip'
```

`.env` files are ignored, but the provided Python tools deliberately do not
auto-load them. Your scheduler or secret manager should inject the environment.
Avoid shell tracing (`set -x`) when secrets are present.

Verify read-only access:

```bash
python3 scripts/alpaca_api.py account
python3 scripts/alpaca_api.py clock
python3 scripts/alpaca_api.py movers --top 10
python3 scripts/market_scan.py > market_scan_latest.json
```

The client never prints credentials. Its order command requires
`--confirm-paper` and independently refuses any non-paper hostname.

## Initial configuration

Edit `risk_rules.json` manually before scheduling:

1. Set `starting_capital_usd` to net deposits minus withdrawals.
2. Optionally set `universe.alpaca_watchlist_name` and `seed_symbols`.
3. Review dynamic-discovery, liquidity, and technical thresholds.
4. Review all position sizing, stop, take-profit, and account loss limits.
5. Keep `execution.mode` at `dry_run` for at least the configured number of
   distinct cycles.
6. Only after reviewing every log should a human change the mode to `paper`.

`paper` means simulated Alpaca orders. This repository prohibits the live
Alpaca endpoint.

## Scheduling with Codex

Create two separate Codex automations pointed at your private repository and
select `gpt-6-astra` for both:

- Phase A: weekdays after the US close, for example 4:30 PM America/New_York.
- Phase B: weekdays about five minutes after the open, for example 9:35 AM
  America/New_York.

Phase A prompt:

```text
Read AGENTS.md and execute PHASE_A_TASK.md exactly. Use the current checkout as
the source of truth. Do not place, replace, or cancel orders. Commit and push
only the output files named by the task specification.
```

Phase B prompt:

```text
Read AGENTS.md and execute PHASE_B_TASK.md exactly. Use the current checkout as
the source of truth. Paper orders are authorized only when every gate in the
task specification passes. Never use a live Alpaca endpoint. Commit and push
only the output files named by the task specification.
```

Use only one scheduler for each phase. Concurrent duplicate Phase B runs can
produce duplicate decisions; deterministic client order IDs reduce but do not
eliminate the need for single-run scheduling.

## Main configuration defaults

The checked-in defaults are illustrative:

| Rule | Default |
|---|---:|
| Dynamic mover candidates | 50 gainers/losers feed |
| Most-active candidates | 50 |
| Dynamic names researched | 20 |
| Minimum price | $5 |
| Minimum 20-day dollar volume | $20M/day |
| Daily move trigger | 3% |
| Opening gap trigger | 2% |
| Intraday range trigger | 3.5% |
| Relative volume trigger | 1.5× |
| High/medium/low target | 20% / 12% / 6% |
| Maximum simultaneous positions | 4 |
| Minimum cash buffer | 10% |
| Daily/weekly entry halt | 5% / 10% |

These are not recommendations.

## State and audit trail

- `pending_proposals.jsonl` is replaced by each successful Phase A run.
- `trade_log.jsonl` is append-only and controls idempotency, dry-run count,
  take-profit state, and re-entry state.
- `trade_log_recent.md` is only a readable recap; the JSONL log wins if they
  disagree.
- `market_scan_latest.json` preserves the exact scanner inputs used by Phase A.

Each cloud run commits its output back to the repository so a fresh later run
can reconstruct state without relying on local memory.

## Important limitations

- The workflow checks risk on its schedule, not continuously throughout the
  trading day. It cannot protect against all intraday or overnight gaps.
- LLM news analysis can be incomplete or wrong even with detailed sourcing.
- Alpaca news coverage is not exhaustive; primary-source verification still
  matters.
- One credential can inspect only its Alpaca account. Cross-broker wash-sale
  tracking remains the user's responsibility.
- Paper fills do not perfectly reproduce live liquidity, slippage, or market
  impact.
- Git is an audit/state mechanism, not a transactional trading database.
- No backtest in this repository validates the news-driven selection logic.

## License

MIT. See `LICENSE`. Provided as-is, without financial advice or warranty.
