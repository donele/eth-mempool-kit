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

## `estimate_arb.py` For V2 Swaps

`estimate_arb.py` started as a rough profitability estimator for a narrow V2-style subset of the filtered mempool stream.

It is not a full MEV simulator. It does not build bundles, simulate exact inclusion order, estimate gas used by your own arb transaction, or model builder/searcher competition. For the V2 path, it is meant to answer a smaller question:

- Does this pending V2-style swap appear to create a simple two-pool arbitrage at current reserves?

### What It Reads

The script reads a captured transaction log such as `filtered_20260501_1528.log`.

For each transaction record, it parses:

- the transaction hash
- the router label line when present
- the transaction `to` address
- the transaction `value`
- the calldata from the logged `input: HexBytes('0x...')`

It then uses the local decoder helpers in `decode_mempool.py` to decode the calldata into structured swap arguments.

### V2 Support Scope

The V2 estimator path only handles swaps that meet all of these conditions:

- the router is `Uniswap V2` or `SushiSwap`
- the method is one of:
  - `swapExactTokensForTokens`
  - `swapExactTokensForETH`
  - `swapExactETHForTokens`
- the path has exactly 2 tokens

Everything else is skipped by the V2 estimator path.

### RPC Requirement

This script needs a live HTTP RPC endpoint because it reads current pair state from chain.

It uses:

- `RPC_URL` from `.env` if present
- otherwise it derives an HTTP URL from `WSS_URL`
- or you can pass `--rpc-url` explicitly

Example:

```bash
python estimate_arb.py filtered_20260501_1528.log
```

Or:

```bash
python estimate_arb.py filtered_20260501_1528.log --rpc-url https://eth-mainnet.g.alchemy.com/v2/...
```

Important limitation:

- `getReserves()` returns the current reserve state seen by the RPC node at query time
- it does not return the reserve state at the moment the pending transaction first appeared in the mempool
- it also does not return the exact reserve state immediately before the transaction executes in a future block

So the estimator is using current-state reserves as an approximation, not the true execution-time state.

### How The V2 Estimator Works

For each supported V2 transaction:

1. Decode the calldata and extract the exact-input amount and 2-token path.
2. Identify whether the victim swap is on Uniswap V2 or SushiSwap.
3. Query `getPair(tokenA, tokenB)` on both factories.
4. Query `getReserves()` and `token0()` for both pairs.
5. Simulate:
   - the victim route on the victim DEX
   - the same route on the other DEX
   - a simple current-state round trip across the two DEXes
   - a simple post-victim round trip where the victim trade has already shifted reserves on its own DEX

The reserve math uses the standard constant-product V2 formula with a `0.3%` fee:

- fee numerator: `997`
- fee denominator: `1000`

The most important caveat here is that the reserve inputs come from the current chain state at query time. They are not historical reserves taken from the exact moment the transaction was first observed, and they are not the fully mempool-adjusted reserves that would exist after all earlier pending transactions have executed.

### What The V2 Output Means

The V2 path prints a block per supported transaction. Example shape:

```text
2026-05-01 19:30:05.218058 TRANSACTION HASH: 058e072879fcca7b9877aac448ebfe58410286f6c8427599932353e058c0ddf7
router=SushiSwap
estimate_scope=rough_v2_two_pool_cycle current_state_and_post_victim same_size only
tx_router=SushiSwap method=swapExactTokensForTokens amount_in=1,456,223,114,490,895,000,000
path=0xccc8cb5229b0ac8069c51fd58367fd1e622afd97 -> 0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2
route_out_current[SushiSwap]=23,239,156,954,437,964
route_out_current[Uniswap V2]=23,101,000,000,000,000
gross_cycle_current[SushiSwap->Uniswap V2]=-12,345,678
gross_cycle_current[Uniswap V2->SushiSwap]=45,678,901
gross_cycle_post_victim[Uniswap V2->SushiSwap]=78,901,234
```

Interpretation:

- `route_out_current[...]` is the output amount you would get by swapping the victim's input size on that DEX right now.
- `gross_cycle_current[A->B]` is the raw token profit or loss from a simple 2-leg cycle:
  - swap token0 to token1 on DEX `A`
  - then swap token1 back to token0 on DEX `B`
- `gross_cycle_post_victim[...]` applies the victim trade to the victim DEX reserves first, then computes the cycle.

This is a gross amount in token units. It does not subtract:

- gas costs
- priority fee / bribe
- failed inclusion probability
- slippage from other pending transactions
- your own trade impact beyond the same-size approximation already used in the cycle calculation

### What The V2 Path Can Tell You

It can help answer:

- Is this pending V2-style swap touching a pair that is priced differently on Sushi vs Uniswap right now?
- Does the victim trade appear to widen or reduce a simple two-pool arbitrage?
- Is this transaction worth deeper simulation?

### What The V2 Path Cannot Tell You

It cannot reliably answer:

- Will this be profitable net of gas?
- Can I backrun this in a real bundle?
- What happens after all competing mempool transactions are applied?
- What is the optimal trade size?
- Is a V3 / aggregator route profitable?

Treat it as a filter, not a final decision engine.

### Worked Example: Transaction `058e07...`

Below is the first transaction from `filtered_20260501_1528.log`, shortened only where the calldata is too long to read comfortably.

Original captured transaction:

```text
2026-05-01 19:30:05.218058 TRANSACTION HASH: 058e072879fcca7b9877aac448ebfe58410286f6c8427599932353e058c0ddf7
queue_size=6 avg_lookups_per_sec=14.97
router=SushiSwap
AttributeDict({
  'type': 2,
  'chainId': 1,
  'nonce': 2407535,
  'gas': 138314,
  'maxFeePerGas': 200000000000,
  'maxPriorityFeePerGas': 200000000,
  'to': '0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F',
  'value': 0,
  'accessList': [],
  'input': HexBytes('0x38ed173900000000000000000000000000000000000000000000004ef127c73a8ec9e1c000000000000000000000000000000000000000000000000000528fe3df26194c00000000000000000000000000000000000000000000000000000000000000a0000000000000000000000000b1b2d032aa2f52347fbcfd08e5c3cc55216e84040000000000000000000000000000000000000000000000000000000069f4ff8c0000000000000000000000000000000000000000000000000000000000000002000000000000000000000000ccc8cb5229b0ac8069c51fd58367fd1e622afd97000000000000000000000000c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2'),
  'r': HexBytes('0x501563a27be1900a0c064f9d50fd53c70e7a20df3cbe012438ebc6e9fca65e02'),
  's': HexBytes('0x3b3ead5045a55486c2de70cb3a40bf5e401641cfd2f7cd2d1925ef6b8e716744'),
  'yParity': 0,
  'v': 0,
  'hash': HexBytes('0x058e072879fcca7b9877aac448ebfe58410286f6c8427599932353e058c0ddf7'),
  'blockHash': None,
  'blockNumber': None,
  'transactionIndex': None,
  'from': '0xb1b2d032AA2F52347fbcfd08E5C3Cc55216E8404',
  'gasPrice': 200000000000
})
```

Decoded calldata:

```text
decoded_input: selector=0x38ed1739 (swapExactTokensForTokens), words=8
decoded_input_detail: selector=0x38ed1739 (swapExactTokensForTokens), bytes=256, words=8
decoded_method: swapExactTokensForTokens
  amountIn=1456223114490895000000
  amountOutMin=23239156954437964
  path=['0xccc8cb5229b0ac8069c51fd58367fd1e622afd97', '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2']
  to=0xb1b2d032aa2f52347fbcfd08e5c3cc55216e8404
  deadline=1777663884
```

What that means:

- router: `SushiSwap`
- method: `swapExactTokensForTokens`
- token in: `0xccc8cb5229b0ac8069c51fd58367fd1e622afd97` = `Gods Unchained: GODS Token (GODS)`
- token out: `0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2` = `Wrapped Ether (WETH)`
- exact input size: `1,456,223,114,490,895,000,000`
- minimum acceptable output: `23,239,156,954,437,964`

In plain English, this transaction is selling `GODS` for `WETH` on SushiSwap.

This transaction is supported by the V2 estimator path because:

- the router is SushiSwap
- the method is an exact-input Uniswap V2 style method
- the path has exactly 2 tokens

### Worked Example: Call By Call

For this transaction, `estimate_arb.py` performs the following steps.

1. Parse the log line and extract:
   - `to = 0xd9e1...`
   - `value = 0`
   - `input = 0x38ed1739...`
2. Decode the calldata using `decode_mempool._decode_input_structured(...)`.
3. Recognize the router as SushiSwap.
4. Infer the comparison venue as Uniswap V2.
   - it means the estimator compares two separate V2-style venues for the same token pair
   - because the victim transaction is on SushiSwap, the current implementation chooses Uniswap V2 as the comparison venue
   - if the victim transaction were on Uniswap V2, the estimator would compare against SushiSwap instead
5. Extract the 2-token path:
   - `tokenA = 0xccc8cb5229b0ac8069c51fd58367fd1e622afd97` = `GODS`
   - `tokenB = 0xC02aaA39b223FE8D0A0E5C4F27eAD9083C756Cc2` = `WETH`
6. Extract the exact-input amount:
   - `amountIn = 1456223114490895000000`

Then it makes these on-chain calls through the configured HTTP RPC:

1. Sushi factory `getPair(tokenA, tokenB)`
   - factory address: `0xC0AEe478e3658e2610c5F7A4A2E1777Ce9e4f2Ac`
   - purpose: find the Sushi V2 pair for this token pair
2. Uniswap V2 factory `getPair(tokenA, tokenB)`
   - factory address: `0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f`
   - purpose: find the Uniswap V2 pair for the same token pair
3. Sushi pair `getReserves()`
   - purpose: fetch the current reserve balances
4. Sushi pair `token0()`
   - purpose: determine whether the contract stores reserves as `(tokenA, tokenB)` or `(tokenB, tokenA)`
5. Uniswap V2 pair `getReserves()`
   - purpose: fetch the current reserve balances on the comparison venue
6. Uniswap V2 pair `token0()`
   - purpose: align reserve ordering to the same `(tokenIn, tokenOut)` direction
7. Sushi factory `getPair(tokenB, tokenA)`
   - same pair lookup again, but for the reverse direction path used in the simple cycle model
8. Sushi reverse pair `getReserves()`
9. Sushi reverse pair `token0()`
10. Uniswap V2 factory `getPair(tokenB, tokenA)`
11. Uniswap V2 reverse pair `getReserves()`
12. Uniswap V2 reverse pair `token0()`

Because this example has a 2-token path, the reverse-direction lookups usually resolve to the same pair contract as the forward lookup. The script still queries through the same helper for the reverse path so it can model the return leg consistently.

These calls all read current RPC state. They do not reconstruct the exact Sushi or Uniswap reserve state that existed when transaction `058e07...` first entered the mempool.

### Worked Example: The Math

After the RPC calls, the script has four ordered reserve views:

- Sushi forward reserves for `GODS -> WETH`
- Uniswap V2 forward reserves for `GODS -> WETH`
- Sushi reverse reserves for `WETH -> GODS`
- Uniswap V2 reverse reserves for `WETH -> GODS`

For each hop it uses the standard Uniswap V2 amount-out formula:

```text
amountInWithFee = amountIn * 997
amountOut = (amountInWithFee * reserveOut) / (reserveIn * 1000 + amountInWithFee)
```

All arithmetic is integer arithmetic in raw token units.

For transaction `058e07...`, the script computes:

1. `route_out_current[SushiSwap]`
   - simulate swapping `amountIn` of `GODS` on Sushi using current Sushi reserves
2. `route_out_current[Uniswap V2]`
   - simulate swapping the same `GODS` amount on Uniswap V2 using current Uniswap reserves
3. `gross_cycle_current[SushiSwap->Uniswap V2]`
   - first leg: swap `GODS -> WETH` on Sushi
   - second leg: swap resulting `WETH -> GODS` on Uniswap V2
   - subtract original `amountIn`
4. `gross_cycle_current[Uniswap V2->SushiSwap]`
   - first leg: swap `GODS -> WETH` on Uniswap V2
   - second leg: swap resulting `WETH -> GODS` on Sushi
   - subtract original `amountIn`
5. `gross_cycle_post_victim[Uniswap V2->SushiSwap]`
   - first apply the victim transaction to Sushi forward reserves
   - this increases Sushi `GODS` reserve and decreases Sushi `WETH` reserve
   - then simulate the cycle:
     - buy `WETH` on Uniswap V2 with `GODS`
     - sell `WETH` back into Sushi for `GODS` after the victim has moved Sushi's price
   - subtract original `amountIn`

The post-victim case is the most useful one in this script, because it is the one attempting to answer:

- If this Sushi swap lands first, does it open a backrun on Uniswap V2 -> Sushi?

In other words, the estimator is checking whether a trade on one venue moves the pair far enough away from the price on the other venue to create a simple two-pool arbitrage.

That still uses a simplified state model:

- current reserves on both venues
- plus a synthetic reserve update for the victim trade on the victim venue

It does not include the effect of unrelated pending transactions that may also move either pool before this trade lands.

### What The Worked Example Still Does Not Prove

Even for transaction `058e07...`, this worked example is still only a rough signal.

It does not prove real profitability because it still ignores:

- gas used by your backrun transaction
- builder payment / priority fee
- mempool reorder effects from unrelated transactions
- the optimal trade size, which may be very different from the victim's `amountIn`
- inclusion risk
- state changes between observation and execution
