# FriesTrader7 — Claude Code + Alpaca data + Robinhood execution

FriesTrader7 is a two-phase, auditable trading workflow:

- Claude Code researches and synthesizes decisions.
- Alpaca Pro/SIP is a read-only professional market-data and news source.
- Robinhood Agentic Trading MCP is the only account and order interface.
- Deterministic Python scripts enforce technical scanning and risk math.

This is a template, not evidence that LLM stock selection beats an index. Keep
it in `dry_run` until you have inspected enough complete cycles yourself.

## Architecture

```text
Alpaca whole-market movers / most-active / news / SIP bars
Robinhood watchlist + Robinhood positions
Persistent candidate_universe.json
                         │
                         ▼
 Phase A after close — Claude Code
 discovery → technical scan → detailed news/filing thesis
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
 pending_proposals.jsonl    candidate_universe.json
            │
            ▼
 Phase B after next open — Claude Code
 Robinhood positions/quotes → exits → entry gates → sizing
            │
            ▼
 Robinhood review → gated Robinhood order → fill confirmation
            │
            ▼
 trade_log.jsonl + trade_log_recent.md
```

Alpaca account cash, positions, buying power, P&L, and orders are never used.
The Alpaca helper implements GET-only market-data calls and has no order
command. The paper endpoint is unnecessary for this architecture; Alpaca Pro
market data is served from `https://data.alpaca.markets`.

## Broad, independent candidate discovery

The tradable research universe is not bounded by the Robinhood watchlist or
current holdings. Every Phase A run merges:

- Alpaca whole-market top gainers and losers;
- Alpaca whole-market most-active stocks;
- symbols attached to the latest market-wide Alpaca news;
- the repository's persistent candidate pool;
- optional configured seed symbols;
- the Robinhood watchlist;
- every current Robinhood position.

The scanner calculates daily movement, opening gap, intraday range, relative
volume, average dollar volume, 20-day momentum/breakout, SMA20/50/200, RSI14,
and ATR14 percentage. Dynamic names are ranked by a disclosed signal score.

`candidate_universe.json` keeps opportunities across days without modifying
the Robinhood watchlist. Entries are categorized as:

- `active_opportunity`: actionable technical/news setup now;
- `upcoming_catalyst`: a sourced future earnings, product, regulatory, legal,
  or other date-specific event;
- `monitor`: a developing thesis that is not actionable yet;
- `held`: an existing Robinhood position that must always be reviewed.

Every non-held entry has a sourced reason, next-review date, and expiration.
`universe_manager.py` validates, deduplicates, expires, and caps the list, so it
does not grow forever or preserve stale ideas indefinitely.

## Detailed Claude analysis

Each researched stock requires:

- executive summary and exact conviction rationale;
- catalyst chronology and confirmation status;
- source-by-source evidence with publication time and URL;
- technical trend, momentum, volume, volatility, and observed levels;
- explicit bull case and strongest bear case;
- concrete thesis invalidation conditions;
- fixed risk flags and unresolved data gaps.

At least two independent sources are required by default. High conviction
requires a confirmed company-specific catalyst, a primary or wire source,
independent corroboration, supportive technical structure, and no unresolved
material risk flag or data gap. `validate_proposals.py` prevents incomplete
records from reaching Phase B.

The model is selected in the Claude Code scheduled-task configuration, not by
repository text. Choose the Claude model you want there; the task does not
silently route analysis to a different provider.

## Data boundaries

Alpaca Pro supplies high-quality trades, quotes, bars, screeners, and news, but
it is not a complete fundamentals database. Never infer market cap, valuation,
earnings estimates, revenue, margins, or balance-sheet data from price bars.
Robinhood fundamentals and cited primary filings may supply verified facts;
otherwise they are recorded as data gaps.

For execution, Robinhood's bid/ask is authoritative because that is where the
order will be sent. Alpaca SIP is independently compared with it. A stale or
materially divergent Alpaca quote blocks a buy and creates an audit entry, but
never blocks a risk-reducing sell.

## Deterministic components

- `scripts/alpaca_api.py`: read-only Alpaca data client.
- `scripts/market_scan.py`: broad discovery and technical metrics.
- `scripts/universe_manager.py`: persistent opportunity-pool lifecycle.
- `scripts/validate_proposals.py`: detailed thesis contract.
- `scripts/entry_gate.py`: gap, extension, wash-sale, and re-entry locks.
- `scripts/stop_loss.py`: fixed or volatility-scaled stops.
- `scripts/take_profit.py`: cascading partial exits.
- `scripts/conviction_trim.py`: persistent low-conviction reduction.
- `scripts/pnl_pct.py`: Robinhood daily/weekly realized-loss entry halt.
- `scripts/rank_candidates.py`: conviction, risk, technical-score ranking.
- `scripts/position_sizing.py`: position, cash, slot, and top-up limits.

All scripts use the Python standard library.

## Credentials

The Alpaca key previously posted in a chat should be considered exposed and
rotated. Do not commit the replacement. Inject the new pair through the Claude
runtime or secret manager:

```bash
export ALPACA_API_KEY_ID='your_new_key_id'
export ALPACA_API_SECRET_KEY='your_new_secret'
export ALPACA_DATA_BASE_URL='https://data.alpaca.markets'
export ALPACA_DATA_FEED='sip'
```

`.env` is ignored, but the scripts do not automatically load it. Avoid shell
tracing while credentials are present.

The Robinhood MCP connection must be authorized in the exact Claude Code
environment used by the scheduled task. A connection in ChatGPT does not carry
over automatically. Do not store
Robinhood credentials in this repository.

## Initial setup

1. Keep the GitHub repository private.
2. Merge this migration branch.
3. Rotate the exposed Alpaca key and configure both new environment variables.
4. In `risk_rules.json`, set
   `execution_broker.account_number` and
   `universe.robinhood_watchlist_name`.
5. Add every inspectable Robinhood account to
   `wash_sale_avoidance.linked_accounts`.
6. Set `starting_capital_usd` to net deposits minus withdrawals.
7. Review all discovery, sizing, stop, profit-taking, and loss thresholds.
8. Leave `execution.mode` as `dry_run`.

Test Alpaca data access without printing environment variables:

```bash
python3 scripts/alpaca_api.py movers --top 10
python3 scripts/alpaca_api.py most-actives --top 10
python3 scripts/alpaca_api.py snapshots --symbols AAPL,MSFT
```

Then test the scanner with symbols obtained through Robinhood MCP:

```bash
python3 scripts/market_scan.py \
  --held-symbols 'AAPL' \
  --watchlist-symbols 'MSFT,NVDA' \
  --candidate-symbols ''
```

An empty optional list can be omitted entirely.

## Scheduling in Claude Code

Create two separate Claude Code scheduled sessions, give both the GitHub
checkout, Alpaca environment secrets, and a separately authorized Robinhood
MCP connection. Select the desired Claude model in that environment.

Suggested schedules in `America/New_York`:

- Phase A: weekdays at 4:30 PM, after the regular close.
- Phase B: weekdays at 9:35 AM, after the regular open.

Phase A prompt:

```text
Read CLAUDE.md and execute PHASE_A_TASK.md exactly. Alpaca is read-only and
Robinhood order tools are forbidden in this phase. Commit and push only the
named output files.
```

Phase B prompt:

```text
Read CLAUDE.md and execute PHASE_B_TASK.md exactly. Alpaca is data-only.
Robinhood MCP is the only account and order route. A live order is authorized
only when every documented gate passes. Commit and push only the named logs.
```

Use only one scheduler per phase. Give Phase A enough time to finish and push
well before Phase B starts the next morning.

## Moving from dry run to Robinhood execution

Every Phase B run ends with one `cycle_summary`. Distinct successful dry-run
dates are counted. After at least the configured minimum—10 by default—review:

- rejected as well as approved candidates;
- source quality and thesis/data gaps;
- Alpaca versus Robinhood quote differences;
- all stop-loss and take-profit calculations;
- position sizing, cash buffer, and duplicate-order behavior;
- the final `trade_log.jsonl` against Robinhood account history.

Ten validated cycles are necessary but not sufficient for unattended live
execution. The checked-in `execution.live_prerequisites` remain false until a
deployment supplies and tests a durable order-intent journal, single-run lock,
startup broker reconciliation, and ambiguous-submission recovery. Only after
those controls exist may the human set every prerequisite true and change
`execution.mode` from `dry_run` to `live`. The agent must never make those
changes itself. See `LAUNCH_HARDENING.md` and `deployment/README.md`.

Run the offline calculator regression suite with:

```bash
python3 -m unittest discover -s tests -v
```

## Default limits

| Rule | Default |
|---|---:|
| Dynamic mover input | top 50 |
| Most-active input | top 50 |
| Recent news items | 50 |
| Dynamic names researched per cycle | 20 |
| Persistent non-held pool cap | 100 |
| Minimum price | $5 |
| Minimum 20-day dollar volume | $20M/day |
| Daily move / opening gap trigger | 3% / 2% |
| Intraday range / relative volume trigger | 3.5% / 1.5× |
| High / medium / low target | 20% / 12% / 6% |
| Maximum positions / cash reserve | 4 / 10% |
| Daily / weekly realized-loss entry halt | 5% / 10% |

These values are examples, not recommendations.

## Limitations

- Scheduled checks are not continuous risk monitoring.
- LLM research can still omit, misunderstand, or overweight information.
- Alpaca news coverage and Robinhood fundamentals are not exhaustive.
- Market-data feeds can differ briefly; the workflow rejects rather than
  averages unexplained buy-side discrepancies.
- Wash-sale visibility is limited to accounts the Robinhood MCP can inspect.
- Git is an audit/state mechanism, not a transactional trading database.
- The Markdown phase specifications guide an agent; they do not by themselves
  close the crash window between broker acceptance and durable local logging.
- No backtest here validates news-driven stock selection.

## License

MIT. See `LICENSE`. Provided as-is, without financial advice or warranty.
