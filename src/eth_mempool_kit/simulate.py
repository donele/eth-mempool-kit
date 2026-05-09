import argparse
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .simulate_v3_exact_input import simulate_v3_exact_input
from .simulate_v3_exact_input_single import _derive_rpc_url, simulate_exact_input_single
from .simulate_v3_multicall import simulate_v3_multicall


load_dotenv(find_dotenv(usecwd=True), override=True)


def _run_section(
    title: str,
    fn,
    filtered_log_path: Path,
    tx_hash: str | None,
    anvil_url: str,
    rpc_url: str,
    gas_limit: int,
) -> None:
    print(f"=== {title} ===")
    fn(
        decoded_log_path=filtered_log_path,
        tx_hash=tx_hash,
        anvil_url=anvil_url,
        rpc_url=rpc_url,
        gas_limit=gas_limit,
    )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run v3 replay simulation for exactInputSingle, exactInput, and multicall from filtered.log"
    )
    parser.add_argument(
        "filtered_log",
        type=Path,
        help="filtered transaction log file (with decoded_input selector lines)",
    )
    parser.add_argument(
        "--tx-hash",
        help="target transaction hash; when omitted, simulate all supported entries",
    )
    parser.add_argument(
        "--anvil-url",
        default=os.getenv("ANVIL_URL", "http://127.0.0.1:8547"),
        help="Anvil RPC URL",
    )
    parser.add_argument(
        "--rpc-url",
        default=_derive_rpc_url(),
        help="upstream RPC URL used to fetch the original tx and reset Anvil",
    )
    parser.add_argument(
        "--gas-limit",
        type=int,
        default=2_000_000,
        help="gas limit used for replay transactions",
    )
    args = parser.parse_args()

    if not args.rpc_url:
        raise SystemExit(
            "Missing --rpc-url and could not derive RPC URL from "
            "ERIGON_RPC_URL/RPC_URL/HTTPS_URL/WSS_URL in environment"
        )

    _run_section(
        "simulate_v3_exact_input_single",
        simulate_exact_input_single,
        filtered_log_path=args.filtered_log,
        tx_hash=args.tx_hash,
        anvil_url=args.anvil_url,
        rpc_url=args.rpc_url,
        gas_limit=args.gas_limit,
    )
    _run_section(
        "simulate_v3_exact_input",
        simulate_v3_exact_input,
        filtered_log_path=args.filtered_log,
        tx_hash=args.tx_hash,
        anvil_url=args.anvil_url,
        rpc_url=args.rpc_url,
        gas_limit=args.gas_limit,
    )
    _run_section(
        "simulate_v3_multicall",
        simulate_v3_multicall,
        filtered_log_path=args.filtered_log,
        tx_hash=args.tx_hash,
        anvil_url=args.anvil_url,
        rpc_url=args.rpc_url,
        gas_limit=args.gas_limit,
    )


if __name__ == "__main__":
    main()
