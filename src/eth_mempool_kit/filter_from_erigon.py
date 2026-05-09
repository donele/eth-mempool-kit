#!/usr/bin/env python3
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from web3 import Web3
import yaml

from .decode_lib import _decode_input


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = Path("filtered_20260501_1528.log")
DEFAULT_OUTPUT = Path("filtered_20260501_1528_erigon.log")
DEFAULT_CONFIG = PACKAGE_DIR / "decode_config.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read the min/max timestamps from an existing filtered log, "
            "query archive txs from Erigon in that time window, and write "
            "known-router txs in the same output format."
        )
    )
    parser.add_argument(
        "--input-log",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Input filtered log (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output-log",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output filtered log (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"decode config path (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--rpc-url",
        default=None,
        help="Erigon HTTP RPC URL. If omitted, uses ERIGON_RPC_URL, then HTTPS_URL from .env.",
    )
    parser.add_argument(
        "-n",
        "--max-results",
        type=int,
        default=0,
        help="Stop after writing N matched router txs. 0 means no limit.",
    )
    return parser.parse_args()


def load_router_labels(config_path: Path) -> dict[str, str]:
    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    router_labels = config.get("router_labels")
    if not isinstance(router_labels, dict):
        raise ValueError("decode_config.yaml missing mapping: router_labels")
    return {k.lower(): v for k, v in router_labels.items()}


def parse_time_window(input_log: Path) -> tuple[datetime, datetime]:
    min_dt: datetime | None = None
    max_dt: datetime | None = None

    with input_log.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if " TRANSACTION HASH: " not in line:
                continue
            ts_text = line.split(" TRANSACTION HASH: ", 1)[0]
            try:
                dt = datetime.strptime(ts_text, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                continue
            dt = dt.replace(tzinfo=timezone.utc)
            if min_dt is None or dt < min_dt:
                min_dt = dt
            if max_dt is None or dt > max_dt:
                max_dt = dt

    if min_dt is None or max_dt is None:
        raise ValueError(f"No timestamped tx records found in {input_log}")

    return min_dt, max_dt


def block_timestamp(w3: Web3, block_number: int) -> int:
    block = w3.eth.get_block(block_number, full_transactions=False)
    return int(block["timestamp"])


def find_first_block_at_or_after(w3: Web3, target_ts: int, latest_block: int) -> int:
    lo = 0
    hi = latest_block
    while lo < hi:
        mid = (lo + hi) // 2
        mid_ts = block_timestamp(w3, mid)
        if mid_ts < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def find_last_block_at_or_before(w3: Web3, target_ts: int, latest_block: int) -> int:
    lo = 0
    hi = latest_block
    while lo < hi:
        mid = (lo + hi + 1) // 2
        mid_ts = block_timestamp(w3, mid)
        if mid_ts <= target_ts:
            lo = mid
        else:
            hi = mid - 1
    return lo


def format_timestamp_utc(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def run() -> None:
    args = parse_args()
    if args.max_results < 0:
        raise ValueError("--max-results/-n must be >= 0")

    load_dotenv(find_dotenv(usecwd=True), override=False)

    rpc_url = args.rpc_url
    if not rpc_url:
        import os

        rpc_url = os.getenv("ERIGON_RPC_URL") or os.getenv("HTTPS_URL")
    if not rpc_url:
        raise ValueError("Missing RPC URL. Set --rpc-url or ERIGON_RPC_URL/HTTPS_URL in .env")

    router_labels = load_router_labels(args.config)
    known_router_addresses = set(router_labels)

    min_dt, max_dt = parse_time_window(args.input_log)
    min_ts = int(min_dt.timestamp())
    max_ts = int(max_dt.timestamp())

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        raise RuntimeError(f"Could not connect to RPC: {rpc_url}")

    latest_block = w3.eth.block_number
    start_block = find_first_block_at_or_after(w3, min_ts, latest_block)
    end_block = find_last_block_at_or_before(w3, max_ts, latest_block)

    if start_block > end_block:
        raise RuntimeError("No blocks found in the requested timestamp window")

    args.output_log.parent.mkdir(parents=True, exist_ok=True)

    scanned = 0
    matched = 0
    started = time.monotonic()

    with args.output_log.open("w", encoding="utf-8") as out:
        for block_number in range(start_block, end_block + 1):
            block = w3.eth.get_block(block_number, full_transactions=True)
            block_ts = int(block["timestamp"])
            if block_ts < min_ts or block_ts > max_ts:
                continue

            timestamp_text = format_timestamp_utc(block_ts)
            txs = block.get("transactions", [])
            for tx in txs:
                scanned += 1
                to_addr = tx.get("to")
                to_addr_normalized = to_addr.lower() if isinstance(to_addr, str) else ""
                if to_addr_normalized not in known_router_addresses:
                    continue

                matched += 1
                elapsed = max(time.monotonic() - started, 1e-9)
                avg_lookups_per_sec = scanned / elapsed

                tx_hash_hex = tx["hash"].hex()
                if tx_hash_hex.startswith("0x"):
                    tx_hash_hex = tx_hash_hex[2:]

                out.write(f"{timestamp_text} TRANSACTION HASH: {tx_hash_hex}\n")
                out.write(f"queue_size=0 avg_lookups_per_sec={avg_lookups_per_sec:.2f}\n")
                out.write(f"router={router_labels[to_addr_normalized]}\n")
                out.write(f"{tx}\n")
                out.write(f"{_decode_input(tx.get('input'))}\n")
                if args.max_results > 0 and matched >= args.max_results:
                    print(
                        f"Reached max results ({args.max_results}); stopping early."
                    )
                    print(
                        "Done: "
                        f"window=[{min_dt.isoformat()} -> {max_dt.isoformat()}], "
                        f"blocks=[{start_block} -> {end_block}], "
                        f"scanned_txs={scanned}, matched_router_txs={matched}, "
                        f"output={args.output_log}"
                    )
                    return

    print(
        "Done: "
        f"window=[{min_dt.isoformat()} -> {max_dt.isoformat()}], "
        f"blocks=[{start_block} -> {end_block}], "
        f"scanned_txs={scanned}, matched_router_txs={matched}, "
        f"output={args.output_log}"
    )


if __name__ == "__main__":
    run()
