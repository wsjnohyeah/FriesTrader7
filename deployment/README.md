# Deployment boundary

The repository currently supports research, deterministic risk calculations,
dry-run decisions, and Robinhood order previews. It is not yet a standalone
unattended live execution service.

## Runtime connections

- Claude runs through the operator's selected Claude Code environment.
- Alpaca credentials provide read-only SIP market data and news.
- Robinhood MCP must be authorized separately in the exact Claude Code
  environment that runs the scheduled task. A connection in ChatGPT or another
  app does not provide Claude Code with reusable OAuth credentials.
- Do not copy OAuth tokens from another application's private storage or use an unofficial
  Robinhood login API.

Robinhood's documented Streamable HTTP MCP endpoint is:

```text
https://agent.robinhood.com/mcp/trading
```

Verify current setup instructions with Robinhood before configuring a new
client:

- https://robinhood.com/us/en/support/articles/agentic-trading-overview/
- https://robinhood.com/us/en/support/articles/trading-with-your-agent/

## Required live execution layer

An independently operated live runner must:

1. schedule against timezone-aware market sessions and hold a single-run lock;
2. persist every order intent before broker submission;
3. use only Robinhood-supported identifiers and lookup behavior—never invent
   an unsupported client-order-id field;
4. reconcile open/unknown/partial orders, positions, reserved exposure, and
   fills at startup and after submission;
5. prevent a timeout from causing a blind resubmission;
6. persist take-profit remainder and holding-period identity;
7. pass restart-after-acceptance, timeout, partial-fill, stale-data, and
   persistence-failure tests.

The human operator alone controls final activation. Documentation and passing
calculator tests do not make the live path ready. Keep
`execution.live_prerequisites` false and `execution.mode` at `dry_run` until
the target host supplies these controls.
