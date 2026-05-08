# Mempool Coverage Notes

This README summarizes how to interpret coverage between:
- `20260501_1528/filtered_20260501_1528.log` (live mempool capture via public RPC)
- `20260501_1528_erigon/filtered_20260501_1528.log` (on-chain txs from Erigon block history)

## Key Definitions

- `P(tx in erigon | tx in mempool)`: of txs seen pending, how many were eventually included on-chain.
- `P(tx in mempool | tx in erigon)`: of on-chain txs, how many were seen by your mempool capture.

These are different metrics and can be very different in value.

## Current Dataset Results

From the two files above (unique tx hashes):
- mempool unique: `225`
- erigon unique: `2546`
- overlap: `224`

Computed probabilities:
- `P(tx in erigon | tx in mempool) = 224 / 225 = 99.56%`
- `P(tx in mempool | tx in erigon) = 224 / 2546 = 8.80%`

Interpretation:
- Almost every captured mempool tx made it on-chain.
- The mempool capture only saw a small fraction of eventually included on-chain txs.

## Why Mismatch Happens

Mempool -> missing in chain:
- dropped/evicted
- replaced by higher-fee tx (same sender+nonce)
- canceled
- never reached builders/validators

Chain -> missing in mempool capture:
- private orderflow (non-public path)
- propagation differences across peers/providers
- provider websocket/rate-limit/drop behavior
- local ingestion bottlenecks (queue full, lookup limits)

## Expected Coverage (Single Well-Tuned Local Node)

For live mempool visibility versus eventual inclusion:
- good: `40-70%`
- very good: `70-85%`
- excellent single-vantage: `85%+` (hard)
- near 100%: unrealistic for one node/feed

Notes:
- Hardware (16 cores / 126 GB RAM / NVMe) is more than sufficient.
- Limiting factors are network topology, peer quality, and private flow access.

## Practical Ways to Improve Coverage

1. Use local node endpoints directly for pending capture (`IPC` preferred, local `WS` next).
2. Increase and monitor peer quality/count continuously.
3. Remove ingestion bottlenecks (queue size, parallel lookups, reconnect handling, drop metrics).
4. Merge multiple independent public WSS feeds and dedupe by tx hash.
5. Add additional geographic/network vantage points (second node/region).
6. Add private-flow data sources where contractually available.

## Important Distinction

- Erigon block-history scan gives high completeness for included txs.
- Live mempool capture is always partial from any single vantage point.

