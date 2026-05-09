# eth-mempool-kit

`eth-mempool-kit` is an exploratory MEV research repo for:

- capturing pending Ethereum transactions
- filtering for router- and swap-related flow
- decoding calldata
- building rough opportunity estimators
- evolving toward exact post-victim simulation

Right now the repo is Python-heavy and script-oriented. That is intentional for iteration speed. The expected long-term direction is a much more performance-oriented stack with:

- a local execution client such as Erigon
- local fork simulation
- reduced third-party RPC dependence
- lower-latency mempool ingestion
- higher-performance decode / screening / simulation components
- likely C++ services for the hot path

This README describes the overall plan and the intended future structure.

## Install And Run As A Package

You can install `eth-mempool-kit` and run tools from any directory.

1. Install (editable during development):
```bash
python -m pip install -e . --no-build-isolation
```

2. Run installed console commands:
```bash
decode-transactions /path/to/log.txt
read-mempool-queue -f
```

## Current Repository Layout

Today the repo is small:

- [script/](/home/jdlee/repos/eth-mempool-kit/script)
  - mempool readers
  - decode helpers
  - log analysis
  - rough V2 / initial V3 estimation
- [SIMULATOR.md](/home/jdlee/repos/eth-mempool-kit/SIMULATOR.md)
  - design notes for a future fork-based Uniswap V3 simulator

The current scripts are useful for:

- quickly testing ideas
- validating decode logic
- understanding what kinds of transactions appear in filtered flow
- building out the simulation plan before committing to a larger engine

They are not the final architecture.

## Current Scripts

Main scripts in [script/](/home/jdlee/repos/eth-mempool-kit/script):

- [read_mempool.py](/home/jdlee/repos/eth-mempool-kit/script/read_mempool.py)
  - simple pending-tx reader
- [read_mempool_queue.py](/home/jdlee/repos/eth-mempool-kit/script/read_mempool_queue.py)
  - queue-based pending-tx reader with worker consumers
- [decode_mempool.py](/home/jdlee/repos/eth-mempool-kit/script/decode_mempool.py)
  - decode library helpers and router/selector config loading (imported by other tools)
- [decode_transactions.py](/home/jdlee/repos/eth-mempool-kit/script/decode_transactions.py)
  - offline decoder for captured transaction logs
- [estimate_arb.py](/home/jdlee/repos/eth-mempool-kit/script/estimate_arb.py)
  - rough V2 estimator and initial V3 quote-based estimator
- [decode_config.yaml](/home/jdlee/repos/eth-mempool-kit/script/decode_config.yaml)
  - shared router labels, selector labels, and decoder metadata
- [MEMPOOL_COVERAGE_NOTES.md](/home/jdlee/repos/eth-mempool-kit/MEMPOOL_COVERAGE_NOTES.md)
  - sample-log stats and worked V2/V3 examples

## Project Goal

The long-term goal is not just “decode mempool swaps.” The real goal is a staged system that can:

1. observe relevant mempool flow quickly
2. decode it into structured intent
3. estimate whether the transaction may matter
4. run exact post-victim simulation on shortlisted candidates
5. eventually support automated candidate generation and bundle-oriented execution logic

In practical terms, this means moving from:

- Python scripts
- websocket subscriptions
- rough current-state RPC estimates

Toward:

- local node infrastructure
- local state access
- fork-based exact simulation
- high-performance screening engines
- lower-latency systems language components for the critical path

## Why The Current Python Phase Exists

Python is not the ideal end-state for a high-performance MEV engine, but it is useful at this stage because it makes it cheap to answer questions like:

- which routers dominate filtered flow?
- which selectors appear most often?
- how much of the captured stream is actually V2 vs V3 vs aggregator traffic?
- what is the minimal useful decode surface?
- where does exact simulation matter most?

This matters because the repo should not prematurely optimize the wrong path.

For example, the sample analyzed in [MEMPOOL_COVERAGE_NOTES.md](/home/jdlee/repos/eth-mempool-kit/MEMPOOL_COVERAGE_NOTES.md:1) shows that the filtered stream is dominated by:

- Uniswap V3
- Uniswap Permit2
- KyberSwap MetaAggregation
- 1inch v6

That strongly suggests the end-state system should not be built around V2-only assumptions.

## Expected Future Architecture

The likely architecture eventually looks something like this:

### 1. Node Layer

This is the state and mempool foundation.

Expected components:

- an Ethereum execution client, likely Erigon
- tuned peer connectivity
- local JSON-RPC
- local tracing / historical-state support depending on needs
- possibly separate nodes for:
  - low-latency mempool observation
  - historical replay / research
  - simulation / fork sources

Why Erigon is attractive:

- efficient state access
- good performance profile for local infrastructure
- useful for reducing dependence on third-party RPC
- better long-term control over state, history, and data locality

At this stage, the repo does not yet include Erigon setup automation, but the expected future direction is to rely much more on a local node and much less on hosted RPC for critical paths.

### 2. Capture Layer

This is the mempool ingestion stage.

Current version:

- Python websocket readers
- queue-based transaction fetch
- router filtering

Future version:

- more direct and lower-latency mempool feeds
- tighter control over peer topology
- more reliable transaction acquisition
- likely a compiled ingestion service

The core requirement here is:

- ingest fast
- filter fast
- avoid wasting compute on irrelevant transactions

### 3. Decode Layer

This turns raw calldata into structured intent.

Current version:

- selector labels in YAML
- Python decode helpers
- ad hoc heuristics for unsupported selectors

Future version:

- broader selector coverage
- router-specific decode modules
- clearer distinction between:
  - observed selectors
  - labeled selectors
  - fully structured decoders
- likely a reusable internal decode library

For the hot path, this layer may eventually need a faster implementation than Python.

### 4. Screening Layer

This is the fast “should I care?” stage.

Current version:

- rough V2 reserve math
- current V3 quoter checks

Future version:

- route-aware heuristics
- cross-venue price checks
- fee-tier-aware V3 screening
- reserve / quote caches
- more aggressive pre-simulation pruning

This layer should stay cheap. The goal is not to be perfectly accurate; the goal is to decide whether exact simulation is worth spending time on.

### 5. Exact Simulation Layer

This is where real opportunity validation happens.

Current state:

- design notes only, in [SIMULATOR.md](/home/jdlee/repos/eth-mempool-kit/SIMULATOR.md:1)

Expected near-term implementation:

- fork-based simulation using Anvil
- replay victim transaction
- run candidate post-victim trade
- measure state transitions, token deltas, and gas

Expected later evolution:

- better replay tooling
- broader router coverage
- batch simulation
- candidate-size search
- maybe direct local node / engine integration

For V3 in particular, this is the layer that matters most. Rough quoting is not enough for serious MEV decisions.

### 6. High-Performance Engine Layer

This is the part that likely moves into C++.

Why a compiled engine is likely:

- lower latency
- lower GC overhead
- better concurrency control
- better integration with custom data structures
- more predictable performance under load

Possible responsibilities for a future C++ engine:

- mempool event ingestion
- selector-based filtering
- structured swap extraction
- pool-state caching
- candidate scoring
- queue scheduling for exact simulation

Python would still be useful for:

- research
- tooling
- offline analysis
- prototype strategies

But the highest-throughput production path will likely want a compiled implementation.

## Staged Development Plan

The expected roadmap is roughly:

### Stage 1. Python Prototyping

Already in progress.

Focus:

- capture logs
- decode selectors
- understand router mix
- build worked examples
- identify what deserves simulation

### Stage 2. Better Local Infra

Next likely priority.

Focus:

- run a local node
- improve mempool data quality
- reduce third-party RPC dependence
- start reproducible fork simulations

### Stage 3. Fork-Based Exact Simulation

Planned via the design in [SIMULATOR.md](/home/jdlee/repos/eth-mempool-kit/SIMULATOR.md:1).

Focus:

- replay victim tx
- run post-victim candidate trade
- compute exact gas-adjusted results on a fork

### Stage 4. Broader Coverage

After exact simulation works well for narrow cases.

Focus:

- better Uniswap V3 support
- more routers
- multi-hop paths
- more robust transaction classification

### Stage 5. Performance Refactor

Only after the pipeline shape is validated.

Focus:

- move hot path into C++
- keep Python for orchestration and research
- add caches and lower-latency internal services

## Latency Model

The project is ultimately bounded by a very small reaction window.

### Mempool Propagation

- Use WebSocket, not polling
- Add async queue + parallel workers
- Filter before sim
- Minimize logging
- Rough order of magnitude: `50 - 500 ms`

### Processing

- Decoding/filtering
- Queueing/backpressure
- Python vs C++
- GC pauses, logging overhead
- Observed offline batch decode throughput: `2546` tx in about `2s` via `decode_transactions.py` (about `0.8 ms/tx`, approximately `1 ms/tx`) on local data; this is decode-only and not end-to-end reaction latency
- Worst case rough range: `10 - 500 ms`

### Simulation

- `eth_call`
- local fork execution
- route complexity
- parallel candidate evaluation
- local simulation is much more attractive than remote RPC once the strategy is exact-sim driven
- Observed local test (Erigon + Anvil on the same machine): `445` transactions simulated in `5m24s`, averaging about `0.73 s/tx` (about `728 ms/tx`); this was only a small random sample of `445` transactions

### Bundle Construction

- Building calldata
- Signing

### Network To Builder

- Distance
- Routing quality
- Congestion
- Submission fanout
- Retries

### Total Reaction Window

Rough target:

- `1 - 3 seconds`

### Block Time

- about `12 seconds`

That window is why the likely end-state includes:

- local node infra
- local simulation
- aggressive filtering
- compiled hot-path services

## What This Repo Is Not Yet

This repo is not yet:

- a production trading engine
- a bundle sender
- a complete V3 simulator
- a full mempool node management stack
- a C++ low-latency service mesh

It is better understood as:

- a research and prototyping workspace for getting there

## Related Docs

- [MEMPOOL_COVERAGE_NOTES.md](/home/jdlee/repos/eth-mempool-kit/MEMPOOL_COVERAGE_NOTES.md:1)
  - script-level examples and sample-log analysis
- [SIMULATOR.md](/home/jdlee/repos/eth-mempool-kit/SIMULATOR.md:1)
  - fork-based V3 simulation design
