## Script Notes

This directory contains the mempool capture, decode, and rough estimation scripts.

## Sample Log Stats

The file `filtered_20260501_1528.log` is a captured filtered mempool stream produced by `read_mempool_queue.py -f`.

### Capture Window

- first filtered event: `2026-05-01 19:30:05.218058` UTC
- last filtered event: `2026-05-01 22:53:26.794259` UTC
- elapsed wall time: `12,201.576201` seconds
- elapsed wall time: about `203.36` minutes
- filtered transactions captured: `233`

### Filtered Event Rate

- average filtered events per second: about `0.0191`
- average filtered events per minute: about `1.15`
- average filtered events per hour: about `68.75`

These are whole-log averages. The actual stream is bursty, with some minutes containing no filtered hits and others containing several.

### Count By Router

Router counts observed in `filtered_20260501_1528.log`:

- `Uniswap V3`: `64`
- `Uniswap Permit2`: `42`
- `KyberSwap MetaAggregation`: `40`
- `1inch v6`: `37`
- `Uniswap V3 (old)`: `35`
- `MetaMask Swap Router`: `7`
- `SushiSwap`: `5`
- `1inch v5`: `3`

Approximate share of the `233` filtered events:

- `Uniswap V3`: `27.5%`
- `Uniswap Permit2`: `18.0%`
- `KyberSwap MetaAggregation`: `17.2%`
- `1inch v6`: `15.9%`
- `Uniswap V3 (old)`: `15.0%`
- `MetaMask Swap Router`: `3.0%`
- `SushiSwap`: `2.1%`
- `1inch v5`: `1.3%`

### Queue And Lookup Observations

From the `queue_size=... avg_lookups_per_sec=...` lines in the same log:

- minimum queue size: `0`
- maximum queue size: `37`
- average queue size: about `5.63`
- minimum reported average lookup rate: `13.43` lookups/sec
- maximum reported average lookup rate: `16.06` lookups/sec
- final reported average lookup rate: `13.46` lookups/sec

This suggests the worker pool was generally keeping up, but there were short bursts where the queue built up meaningfully before draining.

### Time Distribution

The log spans a little over `3.39` hours, from `19:30` UTC through `22:53` UTC on `2026-05-01`.

A few visible patterns from the per-minute distribution:

- early in the capture, the flow is sparse and mixed across SushiSwap, Uniswap V3 (old), and KyberSwap
- the middle of the capture has repeated clusters from `Uniswap Permit2`, `1inch v6`, `Uniswap V3`, and `KyberSwap MetaAggregation`
- the final hour is dominated by `Uniswap V3`, `Uniswap Permit2`, and `KyberSwap MetaAggregation`

### Practical Interpretation

For this sample, the filtered stream is not dominated by classic Uniswap V2 style routers. Most hits come from:

- `Uniswap V3`
- `Uniswap Permit2`
- `KyberSwap MetaAggregation`
- `1inch v6`

That matters because `estimate_arb.py` currently only handles a narrow V2-style subset. In this particular sample log, only a small fraction of the captured transactions are directly usable by that estimator without extending the decoder and pricing logic.
