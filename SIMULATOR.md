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

## Final Guidance

If your goal is accurate V3 MEV simulation, prefer:

- a narrow fork-based exact simulator
- plus the existing decode/estimate scripts as a filter

Do not try to turn `estimate_arb.py` directly into a full execution simulator. Keep:

- rough estimation
- exact simulation

as separate stages.
