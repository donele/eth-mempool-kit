## Latency Breakdown

### Mempool propagation

- Use WebSocket, not polling
- Add async queue + parallel workers
- Filter before sim
- Minimize logging
- 50 - 500 ms

### Processing

- Decoding/filtering
- Queueing/backpressure
- Python vs C++
- GC pauses, logging overhead
- Worst case 10 - 500 ms

### Simulation

- eth_call
- RPC vs local (Anvil / local node)
- Complexity of route
- Parallelism
- Local: 1 - 10 ms, remote RPC: 10 - 100 ms

### Bundle construction

- Building calldata, signing

### Network to builder

- Distance
- Routing quality, congestion
- Submitting to 1 vs many relays
- Retries

### Total reaction window

1 - 3 seconds

### Block Time

12 seconds

## Reducing mempool propagation latency

- Run a full node
- Increase and improve peers
- use good static/trusted peers
- Open inbound P2P ports
- Monitor transaction fetcher health

## `estimate_arb.py`

`script/estimate_arb.py` is a rough profitability estimator for a narrow subset of swaps captured by the mempool logger.

It is not a full MEV simulator. It does not build bundles, simulate exact inclusion order, estimate gas used by your own arb transaction, or model builder/searcher competition. It is meant to answer a smaller question:

- Does this pending swap appear to create a simple two-pool Uniswap V2 style arbitrage at current reserves?

### What It Reads

The script reads a captured transaction log such as `script/filtered_20260501_1528.log`.

For each transaction record, it parses:

- the transaction hash
- the router label line when present
- the transaction `to` address
- the transaction `value`
- the calldata from the logged `input: HexBytes('0x...')`

It then uses the local decoder helpers in `script/decode_mempool.py` to decode the calldata into structured swap arguments.

### What It Supports

The current implementation only estimates swaps that meet all of these conditions:

- the router is `Uniswap V2` or `SushiSwap`
- the method is one of:
  - `swapExactTokensForTokens`
  - `swapExactTokensForETH`
  - `swapExactETHForTokens`
- the path has exactly 2 tokens

Everything else is skipped.

That means these are currently unsupported:

- Uniswap V3
- Universal Router
- 1inch
- Kyber
- ParaSwap
- multi-hop V2 routes
- exact-output swaps
- anything requiring route-specific custom decoding or dynamic simulation

### RPC Requirement

This script needs a live HTTP RPC endpoint because it reads current pair state from chain.

It uses:

- `RPC_URL` from `.env` if present
- otherwise it derives an HTTP URL from `WSS_URL`
- or you can pass `--rpc-url` explicitly

Example:

```bash
python script/estimate_arb.py script/filtered_20260501_1528.log
```

Or:

```bash
python script/estimate_arb.py script/filtered_20260501_1528.log --rpc-url https://eth-mainnet.g.alchemy.com/v2/...
```

### How It Works

For each supported transaction:

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

### What The Output Means

The script prints a block per supported transaction. Example shape:

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

### What It Can Tell You

It can help answer:

- Is this pending V2-style swap touching a pair that is priced differently on Sushi vs Uniswap right now?
- Does the victim trade appear to widen or reduce a simple two-pool arbitrage?
- Is this transaction worth deeper simulation?

### What It Cannot Tell You

It cannot reliably answer:

- Will this be profitable net of gas?
- Can I backrun this in a real bundle?
- What happens after all competing mempool transactions are applied?
- What is the optimal trade size?
- Is a V3 / aggregator route profitable?

Treat it as a filter, not a final decision engine.
