# Phase B — Revalidation, risk enforcement, and Alpaca paper orders (Codex)

Run this task approximately five minutes after the US regular session opens.
It consumes Phase A's proposals, processes exits before entries, and produces
an append-only audit trail.

## Runtime and security contract

- Run in Codex with the automation's model set to `gpt-6-astra`.
- If that model is unavailable, stop and report the configuration problem;
  never silently switch to Claude or another model.
- Read `risk_rules.json`, `pending_proposals.jsonl`, and `trade_log.jsonl`
  fresh. Never modify risk rules during a run.
- Credentials come only from `ALPACA_API_KEY_ID` and
  `ALPACA_API_SECRET_KEY`. Never display, log, or commit them.
- Orders may target `https://paper-api.alpaca.markets/v2` only. A live Alpaca
  endpoint is prohibited even when `execution.mode` is `paper`.
- Alpaca has no Robinhood-style order-preview endpoint. The local preflight
  checks below replace preview, and `alpaca_api.py` independently rejects any
  order call not aimed at the paper hostname.

## Step 0 — Load state and enforce idempotency

Get the actual US/Eastern time, then call `alpaca_api.py clock`. Continue only
on a scheduled trading day after the regular session has opened.

Read all proposal lines with `stage == "thesis"`. If the file is missing or
empty, append one zero-count `cycle_summary` and stop.

For each symbol, skip proposal processing when `trade_log.jsonl` already
contains a `risk_check` or `order` with the same symbol and `proposal_date`.
The proposal's date—not today's date—is the idempotency key. Stop-loss and
take-profit checks are exempt and run every cycle for every open position.

Count distinct dates having a `cycle_summary` with `mode == "dry_run"`. This
must be at least `execution.dry_run_min_cycles_before_paper` before paper
orders are permitted.

## Step 1 — Load current Alpaca state

Run and parse:

```bash
python3 scripts/alpaca_api.py account
python3 scripts/alpaca_api.py positions
python3 scripts/alpaca_api.py orders --status open
```

Fail closed if account or position state cannot be read: write an error
`cycle_summary`, do not submit orders, and preserve existing logs.

Classify each unprocessed proposal using this initial position snapshot:

- `avoid`: no further candidate processing;
- `exit_existing`: sell candidate;
- `long` with an open position: held/top-up candidate;
- `long` without an open position: new candidate.

Keep that classification fixed for the cycle so a same-cycle sell cannot turn
a held symbol into a new-entry candidate.

## Step 2 — Run sell-side checks first

For every open position, obtain a current SIP snapshot and adjusted daily bars.
Use Alpaca `avg_entry_price`, `qty`, `market_value`, and the current bid/last
price as inputs. Do not ask the model to recompute script results.

### Stop loss

Run `scripts/stop_loss.py` with the fresh position and bar inputs. In
`volatility_scaled` mode the stop is:

```text
clamp(volatility_stdev_multiplier × sample stdev of daily returns,
      min_stop_pct, max_stop_pct)
```

Use the fallback stop when there are fewer than 10 usable daily bars. Before
any take-profit tier has fired in the current holding period, the reference is
average cost. After a tier fires, pass holding-period daily highs so the
reference becomes the trailing high.

A triggered stop schedules a full-position sell. A positive thesis cannot
cancel it.

### Tiered take profit

Run `scripts/take_profit.py` with average cost, current price, quantity,
configured tiers, and tiers already fired during this holding period. The
script handles multiple newly crossed tiers and cascading remaining quantity.

### Conviction trim

For a held symbol that has a current proposal, use the proposal conviction and
the corresponding target percentage from `position_sizing.conviction_pct`.
Run `scripts/conviction_trim.py`. If low conviction and material overweight
persist for the configured number of consecutive cycles, schedule the returned
partial sell.

If any sell-side risk script fails, do not guess. Do not sell that symbol from
the failed calculation, halt all entries for the cycle, and log
`halt_entries_check_manually`.

An `exit_existing` proposal schedules a full-position sell independently of
the three mechanical checks. Never create duplicate sell quantities: a full
stop/exit takes precedence over partial take-profit or trim orders.

## Step 3 — Paper-order preflight and sell execution

Before every proposed order verify all of the following:

- account `status` is `ACTIVE`;
- `trading_blocked`, `account_blocked`, and `trade_suspended_by_user` are false;
- the asset is active and tradable;
- the latest quote is present and not stale;
- quantity/notional is positive and compatible with `fractionable`;
- no open order would duplicate or conflict with this symbol and side;
- the order will not exceed the position quantity for a long-only sell;
- the calculated order matches the logged risk decision exactly.

Then apply the paper-order gate. All conditions must be true:

1. `execution.mode == "paper"`;
2. the configured trading URL hostname is exactly `paper-api.alpaca.markets`;
3. the dry-run cycle count has reached its configured minimum;
4. all local preflight checks passed.

If mode is `dry_run`, log `would_execute: true` but never call the order
command. If mode is `paper` but another gate condition fails, log the precise
blocker and do not submit.

When open, submit a day market sell with a unique deterministic
`client_order_id` containing the proposal date, symbol, side, reason, and cycle
identifier:

```bash
python3 scripts/alpaca_api.py order --symbol XXXX --side sell --qty QTY \
  --type market --time-in-force day --client-order-id ID --confirm-paper
```

Poll `alpaca_api.py order-status --id ID` at most twice, about 15 seconds apart.
Log the Alpaca order id, client order id, status, filled quantity, average fill
price, and the pre-trade bid. Never represent `accepted` or `new` as `filled`.

Selling is never blocked by the entry loss limit or wash-sale avoidance. If a
loss sale leaves a replacement position in this configured Alpaca account,
record a possible wash-sale warning. Other brokerage accounts remain outside
this system's visibility.

After sells, re-fetch account, positions, and open orders. Use the refreshed
state—not estimates—for all entry checks.

## Step 4 — Revalidate buy candidates

Reject a held candidate for a same-cycle top-up if any stop-loss, take-profit,
conviction-trim, or exit sell was scheduled for that symbol, even in dry run.

On Monday, inspect weekend Alpaca news and primary-source updates for every
remaining proposal. Reject a candidate when a material development triggers
its documented invalidation criteria.

For every remaining new entry and top-up:

1. fetch a fresh SIP snapshot;
2. use the ask as `fresh_ask`;
3. obtain the last configured number of adjusted daily closes;
4. inspect this account's Alpaca fill activities for loss sales inside the
   configured wash-sale window;
5. reconstruct any real prior sell lock from filled orders and the audit log;
6. run `scripts/entry_gate.py` with those inputs.

Reject on any gate failure:

- ask more than `entry_price_gap.max_pct` above Phase A's `current_price`;
- price more than `entry_extension.max_extension_pct` above its moving average;
- same-account wash-sale guard;
- active sell re-entry lock.

If a material gap is inside the mechanical ceiling, determine whether fresh
news nevertheless invalidates the thesis. Cite the evidence; do not simply
repeat Phase A's view.

## Step 5 — Account loss limit

Call Alpaca portfolio history for the day and week:

```bash
python3 scripts/alpaca_api.py portfolio-history --period 1D --timeframe 5Min
python3 scripts/alpaca_api.py portfolio-history --period 1W --timeframe 1D
```

Take the last non-null `profit_loss` value from each response and run:

```bash
python3 scripts/pnl_pct.py \
  --daily-pnl-usd DAILY --weekly-pnl-usd WEEKLY \
  --starting-capital-usd START \
  --daily-limit-pct DAILY_LIMIT --weekly-limit-pct WEEKLY_LIMIT
```

This Alpaca version uses account-level P&L, including unrealized movement, so
an unsold drawdown cannot be hidden from the entry halt. If either portfolio
history or the script fails, halt all new entries and top-ups.

## Step 6 — Rank and size entries

Merge surviving new and held candidates. Preserve from Phase A:

- `conviction`;
- `risk_flags`;
- `technical_setup.signal_score` as top-level `signal_score`;
- `group` (`new` or `held`);
- current position value for held candidates.

Pipe the JSON list through:

```bash
python3 scripts/rank_candidates.py | \
python3 scripts/position_sizing.py \
  --total-value TOTAL_EQUITY --cash-start CASH \
  --concurrent-positions-start OPEN_COUNT \
  [--entries-halted] \
  --max-position-pct MAX_POSITION_PCT \
  --max-concurrent-positions MAX_POSITIONS \
  --min-cash-buffer-pct MIN_CASH_PCT \
  --min-top-up-usd MIN_TOP_UP \
  --min-top-up-pct-of-target MIN_TOP_UP_PCT \
  --conviction-pct 'high:HIGH,medium:MEDIUM,low:LOW'
```

Ranking is deterministic: conviction first, then fewer risk flags, then higher
Phase A technical `signal_score`. Sizing is sequential, so earlier candidates
consume cash and new-position slots before later candidates. Top-ups do not
consume a new slot.

Log the script output verbatim in a `risk_check` entry. Include
`proposal_date`, `risk_flags`, `signal_score`, group, and whether it is a
top-up. If either script fails, reject every pending buy; do not hand-rank or
hand-size.

## Step 7 — Execute approved paper buys

Apply the same preflight and paper-order gate used for sells. Use notional
orders only for fractionable assets; otherwise derive a whole-share quantity
that does not exceed the approved dollar amount or cash buffer.

```bash
python3 scripts/alpaca_api.py order --symbol XXXX --side buy \
  --notional DOLLARS --type market --time-in-force day \
  --client-order-id ID --confirm-paper
```

Poll status at most twice and log estimate versus actual fill separately. Do
not retry a rejected order with looser parameters. The deterministic client
order id is the protection against duplicate submissions after a timeout.

## Step 8 — Audit log and publication

Append one JSON object per decision to `trade_log.jsonl`. Valid stages include:

- `stop_loss`
- `take_profit`
- `conviction_trim`
- `loss_limit_check`
- `risk_check`
- `order`
- `cycle_summary`

Every line includes real `date` and `timestamp`; candidate decisions include
`proposal_date`. Every run appends exactly one final summary:

```json
{"date":"YYYY-MM-DD","timestamp":"HH:mm:ss","stage":"cycle_summary","mode":"dry_run|paper","candidates_considered":0,"orders_approved":0,"orders_submitted":0,"orders_filled":0,"errors":[]}
```

Regenerate `trade_log_recent.md` as a concise human-readable view. It must
report technical/news revalidation, every held-position risk check, entry
rejections, and submitted/fill status, but it must not introduce new decisions.
`trade_log.jsonl` remains the source of truth.

Commit only the audit files. Never stage `.env`, credentials, configuration
changes made during a run, or unrelated files. On push rejection, pull with
rebase once and retry once; never force push or guess through a log conflict.

## Non-negotiable rules

- Never change `execution.mode` or another risk threshold.
- Never use a live Alpaca endpoint.
- Never submit an order unless every paper-order gate condition passes.
- Never let model conviction override a script result.
- Never fabricate missing fundamental, market, news, order, or fill data.
- Never treat a submitted/accepted paper order as filled without order-status
  confirmation.
