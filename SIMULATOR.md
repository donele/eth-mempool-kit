# V3 Fork Simulator

This document describes a practical design for a future `simulate_v3_exact_input_single.py` script that uses Anvil in fork mode to simulate a pending Uniswap V3 `exactInputSingle` transaction and then simulate a candidate post-victim trade.

The goal is not to reimplement Uniswap V3 math locally. The goal is to use a forked EVM as the execution engine and let Python orchestrate:

- loading the victim transaction
- replaying it on the fork
- running one or more candidate trades after it
- measuring balances, gas, and rough net profit

## What Anvil Is

Anvil is a standalone local Ethereum node from Foundry.

It is not a Python library. A Python script talks to it over JSON-RPC using `web3.py`, the same way it would talk to any Ethereum node.

For this workflow:

- Anvil provides the forked EVM state and transaction execution
- Python provides orchestration, simulation logic, and reporting

## When You Need Anvil

The current `estimate_arb.py` only does rough estimation:

- V2 path: current-state reserve math plus a synthetic post-victim adjustment
- V3 path: current quote only

That is not enough for real V3 MEV analysis because a V3 pool changes through:

- `slot0`
- active liquidity
- ticks crossed during the swap
- liquidity net changes at tick boundaries

If you want exact post-victim state without implementing V3 swap math yourself, the simplest practical route is:

- fork mainnet into Anvil
- apply the victim swap on the fork
- apply your candidate trade after it
- compare before/after balances

## Install Anvil

Anvil is distributed with Foundry.

Install Foundry:

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

Verify:

```bash
anvil --version
cast --version
forge --version
```

If `foundryup` is not on your path yet, restart your shell or source your shell profile first.

## How To Run Anvil

### Simple Local Dev Chain

This mode does not use mainnet state:

```bash
anvil
```

This is useful for generic testing, but not for realistic MEV simulation.

### Mainnet Fork Mode

For real simulation you want fork mode:

```bash
anvil --fork-url https://YOUR_RPC_URL
```

This tells Anvil to:

- fetch Ethereum state from the upstream RPC
- expose a local fork at `http://127.0.0.1:8545`

You can also pin a block:

```bash
anvil --fork-url https://YOUR_RPC_URL --fork-block-number 22400000
```

Pinning a block is useful when:

- you want reproducible simulations
- you are debugging
- you want to compare runs against the same base state

If you want Anvil on a different port:

```bash
anvil --fork-url https://YOUR_RPC_URL --port 8546
```

## RPC Source For Forking

Anvil in fork mode still needs an upstream RPC source.

That can be:

- a third-party provider like Alchemy
- your own local node

You do not need to run your own node just to use Anvil.

But if you do run your own node, Anvil can fork from it instead.

## Python Dependencies

A Python simulator would typically need:

```bash
pip install web3 python-dotenv
```

Depending on how you build and sign candidate transactions, you may also need:

```bash
pip install eth-account
```

## Environment Variables

A practical setup might use:

```bash
RPC_URL=https://...
ANVIL_URL=http://127.0.0.1:8545
FORK_BLOCK_NUMBER=22400000
```

Possible meaning:

- `RPC_URL`
  - upstream mainnet RPC used to start the fork
- `ANVIL_URL`
  - local RPC used by the Python simulator
- `FORK_BLOCK_NUMBER`
  - optional pinned block number for reproducibility

## Recommended Workflow

The clean separation is:

1. `read_mempool_queue.py -f`
   - capture candidate transactions
2. `decode_transactions.py`
   - inspect and decode calldata
3. `estimate_arb.py`
   - rough screen for promising routes
4. `simulate_v3_exact_input_single.py`
   - exact fork-based simulation for shortlisted V3 candidates

That keeps the expensive exact simulation off the hot path.

## Scope Of `simulate_v3_exact_input_single.py`

The first practical version should be narrow.

Recommended support:

- Uniswap V3 `exactInputSingle`
- one victim transaction at a time
- one candidate post-victim trade at a time
- fork-based execution only

Do not try to support all of this in version one:

- multi-hop V3
- exact-output V3
- aggregator multicalls
- full bundle construction
- multi-leg optimized path search

Start with a one-pool exact-input victim and one candidate follow-up trade.

## Concrete Python Control Flow

Below is the control flow I would use for `simulate_v3_exact_input_single.py`.

### 1. Parse CLI Arguments

The script should accept:

- a log filename
- a transaction hash or line selector
- `--anvil-url`
- optionally `--candidate-router`
- optionally `--candidate-direction`
- optionally `--candidate-amount-in`

Example:

```bash
python simulate_v3_exact_input_single.py \
  script/filtered_20260501_1528.log \
  --tx-hash 0x35f07f52e188da00092ebfca9fe7f1ba795d8a729f267bc80cf6fad24438493f \
  --anvil-url http://127.0.0.1:8545
```

### 2. Load The Victim Transaction From The Log

Use the same parsing style as `estimate_arb.py`:

- scan `TRANSACTION HASH: ...`
- capture the `AttributeDict(...)` line
- extract:
  - `to`
  - `value`
  - `input`
  - gas fields if needed

Then decode with `decode_mempool._decode_input_structured(...)`.

Reject unless:

- router is a supported Uniswap V3 router
- method is `exactInputSingle`

### 3. Connect To Anvil

Using `web3.py`:

```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider(anvil_url))
if not w3.is_connected():
    raise RuntimeError("Unable to connect to Anvil")
```

### 4. Record Pre-State

Before replaying the victim, read:

- victim sender balance
- token balances for the addresses you care about
- V3 pool address via `factory.getPool(tokenIn, tokenOut, fee)`
- pool `slot0()`
- pool `liquidity()`

Optional but useful:

- current block number
- current base fee
- pool observations if you care about TWAP behavior later

## Worked Example: `35f07f...`

This section uses the real example captured in:

- [script/filtered_20260501_1528.log](/home/jdlee/repos/eth-mempool-kit/script/filtered_20260501_1528.log:23)
- [script/decoded_20260501_1528.log](/home/jdlee/repos/eth-mempool-kit/script/decoded_20260501_1528.log:195)
- [script/simulate_35f07f.log](/home/jdlee/repos/eth-mempool-kit/script/simulate_35f07f.log:1)

The current `simulate_v3_exact_input_single.py` does not yet simulate a candidate backrun trade. It replays the victim swap exactly, snapshots the pool before and after, and reports how the swap moved price and balances.

### Original Captured Transaction

```text
2026-05-01 19:32:38.639162 TRANSACTION HASH: 35f07f52e188da00092ebfca9fe7f1ba795d8a729f267bc80cf6fad24438493f
queue_size=2 avg_lookups_per_sec=15.80
router=Uniswap V3
AttributeDict({'type': 2, 'chainId': 1, 'nonce': 145, 'gas': 200041, 'maxFeePerGas': 572754988, 'maxPriorityFeePerGas': 100, 'to': '0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45', 'value': 0, 'accessList': [], 'input': HexBytes('0x04e45aaf000000000000000000000000e820c06321e60d36257c666643fa5436643445e3000000000000000000000000dac17f958d2ee523a2206206994597c13d831ec7000000000000000000000000000000000000000000000000000000000000006400000000000000000000000074d9c49327f92b45f3136ae5304079e0204c6f690000000000000000000000000000000000000000000000000000000042bde168000000000000000000000000000000000000000000000000000000004268737a0000000000000000000000000000000000000000000000000000000000000000'), 'r': HexBytes('0xe84f778fd15a101021b534fb9b1de86198cf3fd2b4491ddfdaf3bf894b362f27'), 's': HexBytes('0x20819c56a86698ce9f5f77e64f8c5842319b9334625f0f65c7b375b6c3f5b13e'), 'yParity': 0, 'v': 0, 'hash': HexBytes('0x35f07f52e188da00092ebfca9fe7f1ba795d8a729f267bc80cf6fad24438493f'), 'blockHash': None, 'blockNumber': None, 'transactionIndex': None, 'from': '0x74d9c49327F92b45F3136Ae5304079e0204c6F69', 'gasPrice': 572754988})
```

Line by line:

- `TRANSACTION HASH: ...`
  - the mempool transaction identifier
- `queue_size=2 avg_lookups_per_sec=15.80`
  - logger-side runtime stats from `read_mempool_queue.py`
  - not part of the Ethereum transaction itself
- `router=Uniswap V3`
  - the transaction `to` address matches the configured Uniswap V3 `SwapRouter02`
- `type: 2`
  - EIP-1559 transaction
- `chainId: 1`
  - Ethereum mainnet
- `nonce: 145`
  - this sender had already sent 145 prior transactions
- `gas: 200041`
  - user-supplied gas limit for the swap
- `maxFeePerGas: 572754988`
  - absolute EIP-1559 fee cap in wei
- `maxPriorityFeePerGas: 100`
  - very small tip in wei
- `to: 0x68b3...5Fc45`
  - Uniswap V3 `SwapRouter02`
- `value: 0`
  - ERC-20 to ERC-20 swap, no ETH attached
- `accessList: []`
  - no access list was supplied
- `input: HexBytes('0x04e45aaf...')`
  - calldata for `exactInputSingle`
- `r`, `s`, `yParity`, `v`
  - signature fields
- `hash: 0x35f07f...`
  - same tx hash, duplicated inside the decoded object
- `blockHash: None`, `blockNumber: None`, `transactionIndex: None`
  - this was still pending when captured
- `from: 0x74d9...6F69`
  - transaction sender
- `gasPrice: 572754988`
  - effective legacy-style gas price view emitted by Web3 for a type-2 tx

### Decoded Transaction

```text
2026-05-01 19:32:38.639162 TRANSACTION HASH: 35f07f52e188da00092ebfca9fe7f1ba795d8a729f267bc80cf6fad24438493f
router=Uniswap V3
decoded_input: selector=0x04e45aaf (exactInputSingle), words=7
decoded_input_detail: selector=0x04e45aaf (exactInputSingle), bytes=224, words=7
decoded_method: exactInputSingle
  params=['0xe820c06321e60d36257c666643fa5436643445e3', '0xdac17f958d2ee523a2206206994597c13d831ec7', 100, '0x74d9c49327f92b45f3136ae5304079e0204c6f69', 1119740264, 1114141562, 0]
```

Line by line:

- `selector=0x04e45aaf`
  - first 4 bytes of calldata
  - mapped in `decode_config.yaml` to `exactInputSingle`
- `words=7`, `bytes=224`
  - the ABI payload after the 4-byte selector is 7 words long
- `decoded_method: exactInputSingle`
  - the decoder matched the selector and ABI layout
- `params=[...]`
  - these are the fields of the Uniswap V3 `ExactInputSingleParams` struct in order:
- `0xe820...45e3`
  - `tokenIn`
  - the input token is `USDKG`
- `0xdAC1...1ec7`
  - `tokenOut`
  - the output token is `USDT`
- `100`
  - fee tier
  - this is the 0.01% Uniswap V3 pool
- `0x74d9...6f69`
  - recipient
  - in this example the sender receives the output directly
- `1119740264`
  - `amountIn`
  - the user is selling 1,119,740,264 raw `USDKG` units
- `1114141562`
  - `amountOutMinimum`
  - if the router cannot deliver at least this many raw `USDT` units, the transaction should revert
- `0`
  - `sqrtPriceLimitX96`
  - zero means “no explicit price limit beyond pool mechanics”

### Simulation Result

```text
2026-05-01 19:32:38.639162 TRANSACTION HASH: 35f07f52e188da00092ebfca9fe7f1ba795d8a729f267bc80cf6fad24438493f
router=Uniswap V3
simulation_scope=replay_victim_exact_input_single fork_based
upstream_tx_hash=0x35f07f52e188da00092ebfca9fe7f1ba795d8a729f267bc80cf6fad24438493f
fork_reset_block=25002214
anvil_block_before=25002214
anvil_block_after=25002215
sender=0x74d9c49327F92b45F3136Ae5304079e0204c6F69
recipient=0x74d9c49327f92b45f3136ae5304079e0204c6f69
pool=0x1320483123658e2192CEb6c4150a759f4398c5e4
path=USDKG(0xE820C06321E60d36257C666643Fa5436643445E3) -> USDT(0xdAC17F958D2ee523a2206206994597C13D831ec7)
fee_tier=100
amount_in=1,119,740,264
amount_out_min=1,114,141,562
sqrt_price_limit_x96=0
replay_tx_hash=ddf68e5312c255c2885f434209bd7d27f175c5ce6fcd9150cacfab5016b92b38
replay_status=1
replay_gas_used=180440
slot0_before.sqrtPriceX96=79228161763885571377991070712
slot0_before.tick=-1
slot0_after.sqrtPriceX96=79228430954313881014280919578
slot0_after.tick=0
slot0_delta.tick=1
liquidity_before=400717328425834
liquidity_after=329330151646718
tick_spacing=1
quote_before=1,119,624,507
quote_after=1,119,616,895
quote_delta=-7,612
pool_token0_balance_before=310,524,575,052
pool_token0_balance_after=309,404,950,545
pool_token1_balance_before=685,023,741,380
pool_token1_balance_after=686,143,481,644
sender_token_in_balance_before=1,135,740,264
sender_token_in_balance_after=16,000,000
sender_token_out_balance_before=0
sender_token_out_balance_after=1,119,624,507
sender_eth_balance_before=12,184,078,055,765,016
sender_eth_balance_after=999,999,765,329,953,956,320
```

Line by line:

- `simulation_scope=replay_victim_exact_input_single fork_based`
  - this is only a victim replay
  - no searcher trade is executed afterward
- `upstream_tx_hash=...`
  - the original mainnet transaction being replayed
- `fork_reset_block=25002214`
  - the script resets Anvil to one block before the real inclusion block
  - this is meant to approximate pre-transaction state
- `anvil_block_before=25002214`
  - fork state before sending the replay
- `anvil_block_after=25002215`
  - replay mined one local block later on Anvil
- `sender=...`
  - account impersonated on Anvil to submit the same call
- `recipient=...`
  - recipient from the decoded params
- `pool=0x1320...c5e4`
  - V3 pool found via `factory.getPool(tokenIn, tokenOut, fee)`
- `path=USDKG -> USDT`
  - human-readable version of the token pair
- `fee_tier=100`
  - confirms the 0.01% pool
- `amount_in`, `amount_out_min`, `sqrt_price_limit_x96`
  - direct restatement of the decoded swap parameters
- `replay_tx_hash=...`
  - the local Anvil transaction hash, not the original mainnet hash
- `replay_status=1`
  - success
- `replay_gas_used=180440`
  - gas consumed on the replay
- `slot0_before.sqrtPriceX96`, `slot0_after.sqrtPriceX96`
  - core Uniswap V3 price state before and after the trade
  - the increase means the encoded pool price moved upward
  - the script reports the raw state change and does not try to infer a trading decision from `sqrtPriceX96` alone
- `slot0_before.tick=-1`, `slot0_after.tick=0`
  - the trade moved the active tick up by one
- `slot0_delta.tick=1`
  - concise summary of that tick movement
- `liquidity_before=400717328425834`
  - active in-range liquidity before the swap
- `liquidity_after=329330151646718`
  - active liquidity after the swap
  - this changed because the swap crossed into a new active region
- `tick_spacing=1`
  - this pool supports very fine tick increments
- `quote_before=1,119,624,507`
  - if you asked the quoter for the same `amountIn` before replay, this is the current output
- `quote_after=1,119,616,895`
  - same quote after replay
- `quote_delta=-7,612`
  - the same trade became worse by 7,612 raw `USDT` units after the victim executed
  - that is the direct local price impact measured by the script
- `pool_token0_balance_before=310,524,575,052`
  - pool holdings of `token0` before replay
  - in this pool, `token0` is `USDT`
- `pool_token0_balance_after=309,404,950,545`
  - pool holdings of `USDT` after replay
  - down by 1,119,624,507, which matches the delivered output token amount
- `pool_token1_balance_before=685,023,741,380`
  - pool holdings of `token1` before replay
  - in this pool, `token1` is `USDKG`
- `pool_token1_balance_after=686,143,481,644`
  - pool holdings of `USDKG` after replay
  - up by 1,119,740,264, which matches the victim input amount
- `sender_token_in_balance_before=1,135,740,264`
  - sender started with enough `USDKG` to execute the trade
- `sender_token_in_balance_after=16,000,000`
  - sender spent 1,119,740,264 `USDKG`
- `sender_token_out_balance_before=0`
  - sender had no `USDT` beforehand
- `sender_token_out_balance_after=1,119,624,507`
  - sender received the quoted `USDT` amount
- `sender_eth_balance_before=12,184,078,055,765,016`
  - actual sender ETH balance on the fork before impersonation top-up
- `sender_eth_balance_after=999,999,765,329,953,956,320`
  - after top-up and replay
  - this number is not economically meaningful for the original user because the simulator intentionally injects ETH with `anvil_setBalance` to guarantee the replay can pay gas

### How The Simulation Ran

For this example, `simulate_v3_exact_input_single.py` performed these steps.

1. Parse `decoded_20260501_1528.log` and select tx `35f07f...`.
2. Query upstream RPC with `eth_getTransactionByHash` for the full transaction.
3. Query upstream RPC with `eth_getTransactionReceipt` for the real inclusion block.
4. Call Anvil `anvil_reset` with `blockNumber = receipt.blockNumber - 1`.
5. Decode `tx.input` locally with `_decode_input_structured(...)`.
6. Call Uniswap V3 factory `getPool(tokenIn, tokenOut, fee)` to find the pool.
7. Snapshot pre-state with:
   - pool `slot0()`
   - pool `liquidity()`
   - pool `token0()`
   - pool `token1()`
   - pool `fee()`
   - pool `tickSpacing()`
   - ERC-20 `balanceOf(pool)` for both tokens
   - ERC-20 `balanceOf(sender)` for both tokens
   - `eth_getBalance(sender)`
   - quoter `quoteExactInputSingle(...)`
8. Call Anvil `anvil_impersonateAccount(sender)`.
9. Call Anvil `anvil_setBalance(sender, ...)` so the impersonated sender can pay gas.
10. Submit the same transaction call to Anvil with:
    - `from = sender`
    - `to = router`
    - `data = original calldata`
    - `value = original value`
11. Wait for the replay receipt.
12. Snapshot the same state again after execution.
13. Print the diff-oriented report shown above.

This is why the output is useful even without a backrun stage: it tells you exactly how much the victim moved the pool and what the same route would quote immediately after the trade.

### What This Says About Arb Potential

This example does show state movement, but only a very small one.

- The same-route quote worsened by `7,612` raw `USDT` units.
- Because `USDT` has 6 decimals, that is about `0.007612 USDT`.
- The victim moved the pool by only one tick.
- The gross price impact is therefore tiny.

That does not automatically mean there is no arbitrage, because arbitrage depends on the gap between this pool and another venue after the trade. But it does mean:

- the victim itself is small
- the induced distortion is small
- any cross-venue arb would need to overcome gas and execution risk

For a realistic searcher decision, this example is probably weak unless:

- another venue was already extremely tightly mispriced against this pool and
- even a `0.007612 USDT` shift was enough to open a profitable backrun after gas

That is unlikely. So the most reasonable reading is:

- useful as a simulator sanity-check
- not a strong arb candidate by itself

To answer the arb question properly, the next stage would need to:

- snapshot one or more comparison venues before and after the victim
- simulate the candidate searcher swap after the victim
- compute token deltas and gas-adjusted net profit

The key purpose of this step is to have a baseline.

### 5. Make The Victim Executable On The Fork

There are a few ways to do this.

#### Option A: Impersonate The Victim Sender

If the fork/node supports impersonation-style RPC methods, this is the most direct route.

Typical idea:

- impersonate `tx.from`
- fund that address with ETH on the fork if needed
- send the same call data to the same router contract

This is convenient but depends on the fork environment and RPC support.

#### Option B: Recreate A Functionally Equivalent Call

Instead of reproducing the exact original transaction envelope, build a fresh local transaction that calls:

- same `to`
- same `data`
- same `value`

This is often enough if:

- token approvals already exist in forked state
- the sender has enough token balance and ETH for gas

#### Option C: Rebuild Prerequisites

If the victim depends on state that does not replay cleanly, you may need to:

- mint/fund on a test path
- set approvals
- replace the sender with a controlled address

This is less faithful, but still useful for route behavior experiments.

## Recommended First Replay Strategy

For a first version:

1. try to impersonate the original sender
2. top up ETH if needed
3. send the same `to`, `data`, and `value`

If the replay fails, print the revert reason and stop. Do not silently continue.

## 6. Execute The Victim Transaction

Send the victim-equivalent transaction on the Anvil fork.

Capture:

- transaction receipt
- `status`
- `gasUsed`

Then read post-victim pool state:

- `slot0()`
- `liquidity()`

This tells you the exact post-victim state on the fork, which is the main thing the rough estimator cannot provide.

## 7. Build The Candidate Trade

Now construct the trade you want to test after the victim.

For version one, keep this simple:

- use a known router
- fixed direction
- fixed amount in

For example:

- buy on Sushi, sell on V3
- or buy on V3, sell on Sushi

At this stage, the point is not to solve the global optimization problem. The point is to test specific candidate trades against the exact post-victim state.

## 8. Record Pre-Candidate Balances

Right before the candidate trade, record:

- trader ETH balance
- trader token balances for `tokenIn` and `tokenOut`

These snapshots are needed to calculate realized token delta and gas-adjusted PnL.

## 9. Execute The Candidate Trade

Send the candidate transaction on the same Anvil fork after the victim transaction has landed.

Capture:

- receipt
- status
- gas used

Then read post-candidate balances.

## 10. Compute Profit

Compute:

- token delta before vs after
- ETH spent on gas
- raw token profit
- rough net profit

Typical outputs:

- `victim_gas_used`
- `candidate_gas_used`
- `token_in_delta`
- `token_out_delta`
- `gross_profit_token`
- `gas_cost_wei`
- `rough_net_profit_token`

If you want a single normalized profit number, you also need:

- a conversion route back into a common numeraire like WETH or USDC

## 11. Report Results

A useful report block should include:

- tx hash
- router / method
- token pair
- fee tier
- victim amount in
- pre-victim `slot0`
- post-victim `slot0`
- candidate trade details
- candidate gas used
- gross token delta
- rough net result

## Suggested Script Structure

Reasonable functions:

- `load_logged_transaction(...)`
- `decode_exact_input_single(...)`
- `connect_anvil(...)`
- `get_v3_pool(...)`
- `snapshot_pool_state(...)`
- `replay_victim(...)`
- `build_candidate_trade(...)`
- `snapshot_balances(...)`
- `run_candidate_trade(...)`
- `compute_profit(...)`
- `print_report(...)`

## Suggested Pool State Reads

At minimum, read:

- `slot0()`
- `liquidity()`

Optional but useful:

- `tickSpacing()`
- token balances of the pool

Why these matter:

- `slot0()` tells you the current sqrt price and active tick
- `liquidity()` tells you active liquidity at the current price
- comparing these before and after the victim gives you concrete evidence that the pool state actually moved

## Why Not Just Use `quoteExactInputSingle(...)`

Because the quoter only answers:

- what is the current quote for this path and input?

It does not itself give you:

- exact post-victim state
- your post-victim candidate execution result
- realized gas-adjusted profit

That is why the fork-based simulator matters.

## Practical Failure Cases

Common reasons victim replay fails:

- missing token approval
- sender lacks funds on the fork
- transaction relied on state that has changed since observation
- deadline has expired
- upstream fork block is later than the mempool observation in a way that changes replay behavior

Recommended behavior:

- fail loudly
- print revert reason if available
- record enough context to debug

## Recommended Starting Point

For the first implementation, optimize for correctness and debuggability, not speed.

Version one should:

- handle one specific `exactInputSingle` transaction
- replay it on Anvil
- print pre/post `slot0`
- run one candidate trade
- compute token delta and gas used

Only after that works reliably should you add:

- batch simulation
- multi-candidate search
- amount optimization
- bundle integration

## Example High-Level Session

Start Anvil:

```bash
anvil --fork-url "$RPC_URL" --fork-block-number 22400000
```

Run the simulator:

```bash
python simulate_v3_exact_input_single.py \
  script/filtered_20260501_1528.log \
  --tx-hash 0x35f07f52e188da00092ebfca9fe7f1ba795d8a729f267bc80cf6fad24438493f \
  --anvil-url http://127.0.0.1:8545
```

Expected output shape:

```text
victim_tx=0x35f07f...
router=Uniswap V3
method=exactInputSingle
pool=0x...
slot0_before=(...)
slot0_after_victim=(...)
victim_gas_used=...
candidate_router=...
candidate_gas_used=...
token_delta_in=...
token_delta_out=...
rough_net_profit=...
```

## Batch Replay Table

The current batch logs under `script/20260501_1528/` are aggregate outputs:

- `simulate_v3_exact_input_single.log`
- `simulate_v3_exact_input.log`
- `simulate_v3_multicall.log`

So the right summary is one row per batch file, not one row per transaction.

The heuristic is still conservative:

- for files with available quotes, `quote impact % = abs(quote_delta) / quote_before`
- the table uses the median observed quote impact as the main signal
- a large max quote impact is treated as a warning, not as proof of profit
- `Very low`
  - most replays barely moved the local route quote
- `Weak`
  - some visible local movement exists, but still not enough to justify a live strategy by itself
- `Blocked`
  - the current script run did not produce usable quote data

| Batch Log | Scope | Transactions | Successful Replays | Errors | Quote Coverage | Observed Quote Impact | Estimated Potential | Comment |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| `script/20260501_1528/simulate_v3_exact_input_single.log` | direct `exactInputSingle` | `51` | `51` | `0` | `51 / 51` | median `0.0012%`, max `99.85%` | Very low | Most replays show tiny local quote deterioration. A few extreme outliers look illiquid or pathological and need manual review, not automatic trading. |
| `script/20260501_1528/simulate_v3_exact_input.log` | direct `exactInput` | `12` | `7` | `5` | `0 / 7` | unavailable | Blocked | Replays partly worked, but quoter coverage was unavailable and several runs failed with upstream 429 / fork-reset issues, so this batch does not yet support profit triage. |
| `script/20260501_1528/simulate_v3_multicall.log` | V3 multicall wrappers | `13` | `0` | `13` | `0 / 0` | unavailable | Blocked | The current multicall extraction path failed on every entry with `Unexpected multicall tx input encoding`, so no profitability signal is available yet. |

## Final Guidance

If your goal is accurate V3 MEV simulation, prefer:

- a narrow fork-based exact simulator
- plus the existing decode/estimate scripts as a filter

Do not try to turn `estimate_arb.py` directly into a full execution simulator. Keep:

- rough estimation
- exact simulation

as separate stages.
