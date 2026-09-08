# Phase B — Revalidation, risk enforcement, and Robinhood execution (Codex)

Run approximately five minutes after the US regular session opens. Alpaca SIP
provides research-grade market data and news; Robinhood MCP is the only source
of account state and the only permitted order route.

## Runtime and security contract

- Run in Codex with the automation model set to `gpt-6-astra`.
- If that model is unavailable, report the configuration problem; do not
  silently substitute Claude or another model.
- Read `risk_rules.json`, `pending_proposals.jsonl`, and `trade_log.jsonl`
  fresh. Never modify risk rules during a scheduled run.
- Read Alpaca credentials only from `ALPACA_API_KEY_ID` and
  `ALPACA_API_SECRET_KEY`; never display, log, or commit them.
- Alpaca is read-only. Never call an Alpaca account, position, order, cancel,
  or trading endpoint. `scripts/alpaca_api.py` intentionally exposes GET-only
  market-data commands.
- Robinhood MCP is the sole source for portfolio, cash, positions, executable
  bid/ask, order review, order placement, and fill confirmation.

## Step 0 — Load state and enforce idempotency

Determine the real US/Eastern date, weekday, and time through the shell. Use
Robinhood market-hours data when available to confirm that this is a trading
day and the regular session is open.

Read every `stage == "thesis"` proposal. If the file is missing or empty,
append a zero-count `cycle_summary` and stop.

Skip proposal processing when `trade_log.jsonl` already contains a
`risk_check` or `order` for the same symbol and `proposal_date`. The proposal's
own date—not today's date—is the idempotency key. Stop-loss and take-profit
checks still run every cycle for every live position.

Count distinct dates having a `cycle_summary` with `mode == "dry_run"`. At
least `execution.dry_run_min_cycles_before_live` are required before the live
gate can open.

## Step 1 — Load Robinhood account state

Using `execution_broker.account_number`, call Robinhood MCP for:

- portfolio total value and cash;
- all open equity positions;
- open/recent equity orders needed for duplicate detection;
- fresh equity quotes.

If portfolio or position state cannot be obtained, fail closed: append an
error summary and submit no orders. Never substitute values from Alpaca's
paper account. Alpaca paper cash, positions, buying power, and P&L have no role
in this workflow.

Classify each unprocessed proposal against the initial Robinhood position
snapshot:

- `avoid`: no further candidate processing;
- `exit_existing`: sell candidate;
- `long` with an open position: held/top-up candidate;
- `long` without an open position: new candidate.

Keep this classification fixed for the cycle so a same-cycle sale cannot turn
a held symbol into a new-entry candidate.

## Step 2 — Process every Robinhood position before buys

For every open equity position, use its Robinhood average cost and quantity.
Use a fresh Robinhood bid as the conservative executable price. Pull adjusted
daily bars from Alpaca SIP for volatility and trailing-high calculations.

### Stop loss

Run `scripts/stop_loss.py`. In volatility-scaled mode:

```text
stop_pct = clamp(multiplier × sample stdev of daily close returns,
                 configured minimum, configured maximum)
```

Use the configured fallback if there are fewer than 10 usable bars. Before a
take-profit tier has fired in the current holding period, the reference is the
Robinhood average cost. Afterwards, pass Alpaca holding-period daily highs so
the reference becomes the trailing high.

A triggered stop schedules a full-position sell. Neither a bullish thesis nor
an Alpaca/Robinhood quote disagreement may block a risk-reducing sell.

### Tiered take profit

Run `scripts/take_profit.py` with Robinhood average cost, fresh Robinhood bid,
quantity, configured tiers, and tiers already fired during this holding
period. Use its cascading quantities verbatim.

### Conviction trim

For held symbols with a current proposal, compute the tier target from the
fresh Robinhood total account value and run `scripts/conviction_trim.py`.

If any sell-side risk script fails, do not improvise: exclude that symbol from
an automated sell, halt every new entry/top-up for this cycle, and log
`halt_entries_check_manually`.

An `exit_existing` thesis also schedules a full exit. A full stop/exit takes
precedence over partial take-profit or trim quantities so duplicate sell
orders cannot be created.

## Step 3 — Review and execute sells through Robinhood MCP

Before each sell, confirm:

- the account and position still match the earlier snapshot;
- the quantity is positive and no larger than the current position;
- no existing Robinhood order conflicts with this sale;
- the symbol, side, quantity, and reason match the logged decision.

Always call `review_equity_order` first. If it returns a blocking alert, log it
verbatim and do not place the order.

The live-order gate requires all of:

1. `execution.mode == "live"`;
2. at least the configured number of distinct dry-run cycles;
3. a non-blocking Robinhood order review;
4. fresh Robinhood account, position, and quote data;
5. no duplicate/conflicting Robinhood order.

In `dry_run`, log `would_execute: true` and never call
`place_equity_order`. If live mode is configured but any gate fails, log the
exact blocker.

When the gate is open, call Robinhood `place_equity_order` with exactly the
reviewed parameters. Confirm the result using `get_equity_orders` and the
returned order id. If not terminal, wait approximately 15 seconds and check
once more. Never poll more than twice and never label an unconfirmed order as
filled. Log estimated bid/quantity and actual order state/fill separately.

Loss-limit and wash-sale rules never block a risk-reducing sell. For a sell at
a loss, inspect every configured `wash_sale_avoidance.linked_accounts` account
for a surviving replacement lot and log a possible wash-sale warning when
appropriate.

After sell processing, re-pull Robinhood portfolio, cash, positions, and open
orders. Only this refreshed state may be used for buy sizing.

## Step 4 — Revalidate every potential buy

Drop a held candidate from same-cycle top-up consideration when its stop-loss,
take-profit, conviction trim, or exit fired earlier in the cycle, including in
dry-run mode.

On Monday, check weekend Alpaca news and primary-source updates against every
proposal's invalidation criteria. Reject materially invalidated proposals.

For each remaining new entry or top-up:

1. Pull a fresh Robinhood quote. The Robinhood ask is the executable price for
   entry gating, quantity estimates, and logs.
2. Pull the Alpaca SIP snapshot for the same symbol. Treat it as an independent
   market-data sanity check, not as the executable quote.
3. If enabled, reject the buy when the Alpaca quote is older than
   `data_crosscheck.max_alpaca_quote_age_seconds`, missing, or differs from the
   Robinhood ask by more than `data_crosscheck.max_quote_divergence_pct`.
   Log both prices and timestamps. This check never blocks sells.
4. Pull the configured adjusted daily closes from Alpaca.
5. Use Robinhood P&L trade history across configured linked accounts to gather
   recent loss-sale dates.
6. Reconstruct the latest actually executed Robinhood sell lock from the audit
   log and current position history.
7. Run `scripts/entry_gate.py` using the Robinhood ask as `fresh_ask`, Phase
   A's Alpaca `current_price` as `thesis_price`, Alpaca daily closes, and the
   Robinhood wash-sale/re-entry history.

Reject on any script blocking condition: excessive overnight gap, excessive
20-day extension, wash-sale guard, or sell re-entry lock. A candidate is also
rejected when required inputs cannot be reconciled. Never average conflicting
provider prices to manufacture a pass.

If the price changed materially but remains under the hard ceiling, inspect
fresh Alpaca news and primary sources against the thesis invalidation criteria.

## Step 5 — Robinhood account loss limit

Call Robinhood `get_realized_pnl` for day and week, equity only. Pass the dollar
`total_returns` values to:

```bash
python3 scripts/pnl_pct.py \
  --daily-pnl-usd DAILY --weekly-pnl-usd WEEKLY \
  --starting-capital-usd START \
  --daily-limit-pct DAILY_LIMIT --weekly-limit-pct WEEKLY_LIMIT
```

If either P&L value or the script is unavailable, halt all new entries and
top-ups. The halt never blocks exits.

## Step 6 — Rank and size entries

Merge surviving new and held candidates. Preserve:

- `conviction` and `risk_flags` from the detailed thesis;
- `technical_setup.signal_score` as top-level `signal_score`;
- `group` (`new` or `held`);
- current Robinhood position value for held candidates.

Pipe the JSON candidate array through:

```bash
python3 scripts/rank_candidates.py | \
python3 scripts/position_sizing.py \
  --total-value TOTAL_VALUE --cash-start CASH \
  --concurrent-positions-start OPEN_POSITION_COUNT \
  [--entries-halted] \
  --max-position-pct MAX_POSITION_PCT \
  --max-concurrent-positions MAX_POSITIONS \
  --min-cash-buffer-pct MIN_CASH_PCT \
  --min-top-up-usd MIN_TOP_UP \
  --min-top-up-pct-of-target MIN_TOP_UP_PCT \
  --conviction-pct 'high:HIGH,medium:MEDIUM,low:LOW'
```

All monetary/account inputs in this call come from Robinhood, never Alpaca.
Ranking is conviction first, then fewer risk flags, then the higher
deterministic Alpaca technical `signal_score`. Sizing is sequential; higher
ranked approvals consume cash and new-position slots first. Top-ups consume no
new slot.

Log the script output verbatim with `proposal_date`, group, conviction,
risk flags, and signal score. If ranking or sizing fails, reject all remaining
buys rather than manually reproducing the calculation.

## Step 7 — Review and execute buys through Robinhood MCP

For every approved candidate in ranked order, repeat Step 3's Robinhood
review → live-order gate → placement → two-check confirmation procedure. Use
the fresh Robinhood ask and the script-approved dollar amount. Never send an
order to Alpaca.

Do not retry a rejected order with looser parameters. Re-pull cash/positions
when an earlier order's fill materially affects the next order; otherwise stop
the remaining sequence if state cannot be reconciled safely.

## Step 8 — Audit log and publication

Append one JSON line per `stop_loss`, `take_profit`, `conviction_trim`,
`loss_limit_check`, `risk_check`, and `order` decision. Every line gets a real
US/Eastern date/time. Candidate decisions include `proposal_date`; market-data
checks include both provider names and timestamps.

Append exactly one final line:

```json
{"date":"YYYY-MM-DD","timestamp":"HH:mm:ss","stage":"cycle_summary","mode":"dry_run|live","candidates_considered":0,"orders_reviewed":0,"orders_placed":0,"orders_filled":0,"errors":[]}
```

Regenerate `trade_log_recent.md` as a readable recap without making new
decisions. `trade_log.jsonl` remains authoritative.

Commit only `trade_log.jsonl` and `trade_log_recent.md`. Never commit secrets,
temporary API responses, or unrelated changes. On push rejection, pull with
rebase once and retry once; never force-push or guess through an audit-log
conflict.

## Non-negotiable rules

- Alpaca is data-only; no Alpaca account or trading endpoint.
- Robinhood MCP is the only execution route.
- Never change execution mode or risk thresholds during a run.
- Never place a Robinhood order unless every live-order gate condition passes.
- Never let LLM conviction override a script failure or rejection.
- Never fabricate missing fundamental, market, news, portfolio, or fill data.
