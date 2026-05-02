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
