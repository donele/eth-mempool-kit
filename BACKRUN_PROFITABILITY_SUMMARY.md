# Backrun Profitability Estimation Summary

## Scope

This summary covers the replay and profitability estimation work performed on the May 1, 2026 dataset, including:

- Replay simulation (`exactInputSingle`, `exactInput`, `multicall`)
- Batch table generation (`batch-replay-table`)
- Nominal USD conversion of quote deltas
- Interpretation of expected profitability under optimistic capture assumptions

Primary dataset paths discussed:

- `/home/jdlee/workspace/mempool/20260501.source-alchemy.rpc-local`
- `/home/jdlee/workspace/mempool/20260501.source-local`

## Key Fixes and Tooling Changes

## 1) Quote metrics unavailable: root cause and fix

Observed issue:

- `batch-replay-table` reported: `Replays worked, but quote metrics were unavailable.`

Root cause:

- `simulate_v3_exact_input.py` called `quoteExactInput(...)` but was using an ABI that only contained `quoteExactInputSingle(...)`.
- Quoter exceptions were swallowed, resulting in `quote_before=unavailable`, `quote_after=unavailable`, `quote_delta=unavailable`.

Fix applied:

- Added correct `quoteExactInput(bytes,uint256)` ABI in `simulate_v3_exact_input.py`.
- Updated quoter call to use that ABI.

Impact:

- New simulation outputs can populate `quote_*` fields where quote calls succeed.

## 2) Added combined simulator

Created new orchestrator:

- `src/eth_mempool_kit/simulate.py`

Added entrypoint:

- `simulate = "eth_mempool_kit.simulate:main"` in `pyproject.toml`

Behavior:

- Runs all three replay types in one command:
  - `simulate_v3_exact_input_single`
  - `simulate_v3_exact_input`
  - `simulate_v3_multicall`

## 3) Extended `batch-replay-table`

Implemented multiple enhancements in `src/eth_mempool_kit/batch_replay_table.py`:

- Output modes:
  - `-c` CSV
  - `-m` Markdown
  - `-t` per-transaction rows
  - `-a` aggregate rows (per log)
- Defaults changed to:
  - CSV + transaction mode by default
- Sorting:
  - Transaction rows sorted by `max_theoretical_profit_usd` ascending
- `tx_hash` display shortened to first 6 chars
- Removed log filename/path display columns
- Added summary `TOTAL` row with sum of `max_theoretical_profit_usd`
- Added quote token visibility:
  - `quote_token_symbol` column (address display removed per request)
- Added nominal pricing inputs:
  - `--nominal-eth-usd`
  - `--nominal-token-usd KEY=PRICE` (repeatable)
  - `--token-decimals KEY=DECIMALS` (repeatable)
- Added outlier clamp:
  - If quote impact > 10%, force USD estimate to `0.000000`
- Changed unavailable USD behavior:
  - Missing estimate now outputs `0.000000` (instead of `unavailable`)

## Estimation Method Used

Per transaction:

- Quote impact:
  - `abs(quote_delta) / quote_before * 100`
- Max theoretical USD proxy:
  - `abs(quote_delta) / 10^decimals * nominal_token_usd`
  - fallback for `ETH/WETH` via `--nominal-eth-usd`
  - if impact > 10%, set to `0`
  - if price/decimals missing, set to `0`

Important:

- This is a rough upper-bound proxy from replay quote movement, not realized PnL.

## Pricing Coverage Work

To reduce zero-value rows, nominal mappings were expanded in:

- `table.sh` (repo root)

Added broad symbol mappings for tokens seen in `table.log` (e.g. `USDC`, `USDT`, `USDKG`, `WETH`, `WBTC`, `PAXG`, and many long-tail symbols) with assumed prices and decimals for triage.

## Main Findings

From the local block-derived dataset (`/home/jdlee/workspace/mempool/20260501.source-local`), interpreted as near-100% block transaction coverage:

- Summed max theoretical USD over ~3 hours: about `464.6`
- Linear daily extrapolation: about `3716/day`
- At an optimistic 5% capture rate: about `185/day` gross

Interpretation:

- For home PC + public internet execution, this likely overstates realizable net profit.
- After realistic frictions (gas/tips, missed inclusion, competition, own impact, failed attempts), expected net is likely much lower and can be negative on weak days.

## Practical Conclusion

The backrun strategy is not universally unprofitable, but in this dataset and execution context:

- Broad deployment does not look attractive economically.
- Current numbers are better treated as triage/research signals than production-ready PnL.

Most impactful next steps (if continued):

- Improve opportunity selection quality
- Improve execution path (latency, inclusion channel, builder connectivity)
- Re-estimate with full costed simulations on only top candidates

## Lower-Bound Caveat and Follow-Up

Current profitability totals should be treated as a lower bound because:

- Only three selector families were included (`exactInputSingle`, `exactInput`, `multicall`)
- Many rows failed to decode/extract/replay and therefore did not contribute to measured USD

Implication:

- It is reasonable to spend additional time expanding selector coverage and reducing avoidable failures, then re-running the estimation before making a final go/no-go decision.
