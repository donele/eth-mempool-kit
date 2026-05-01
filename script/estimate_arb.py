import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

from decode_mempool import _decode_input_structured


load_dotenv(override=True)

TX_HASH_PREFIX = "TRANSACTION HASH:"
ROUTER_PREFIX = "router="
INPUT_PATTERN = re.compile(r"'input': HexBytes\('([^']+)'\)")
TO_PATTERN = re.compile(r"'to': '([^']+)'")
VALUE_PATTERN = re.compile(r"'value': ([0-9]+)")
HASH_PATTERN = re.compile(r"'hash': HexBytes\('0x([^']+)'\)")

UNISWAP_V2_ROUTER = "0x7a250d5630b4cf539739df2c5dacab4c659f2488"
SUSHISWAP_ROUTER = "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f"
ROUTER_TO_FACTORY = {
    UNISWAP_V2_ROUTER: "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
    SUSHISWAP_ROUTER: "0xC0AEe478e3658e2610c5F7A4A2E1777Ce9e4f2Ac",
}
ROUTER_TO_NAME = {
    UNISWAP_V2_ROUTER: "Uniswap V2",
    SUSHISWAP_ROUTER: "SushiSwap",
}

FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
        ],
        "name": "getPair",
        "outputs": [{"internalType": "address", "name": "pair", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
PAIR_ABI = [
    {
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
            {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
            {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]
FEE_NUMERATOR = 997
FEE_DENOMINATOR = 1000


@dataclass
class LoggedTransaction:
    tx_hash_line: str | None
    router_line: str | None
    tx_hash: str | None
    to: str | None
    value: int
    tx_input: str


def _derive_rpc_url() -> str | None:
    rpc_url = os.getenv("RPC_URL")
    if rpc_url:
        return rpc_url

    wss_url = os.getenv("WSS_URL")
    if not wss_url:
        return None
    if wss_url.startswith("wss://"):
        return "https://" + wss_url[len("wss://") :]
    if wss_url.startswith("ws://"):
        return "http://" + wss_url[len("ws://") :]
    return None


def _extract_logged_transactions(log_path: Path) -> list[LoggedTransaction]:
    tx_hash_line = None
    router_line = None
    results = []

    with log_path.open() as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if TX_HASH_PREFIX in line:
                tx_hash_line = line
                router_line = None
                continue
            if line.startswith("queue_size="):
                continue
            if line.startswith(ROUTER_PREFIX):
                router_line = line
                continue
            if "AttributeDict(" not in line:
                continue

            input_match = INPUT_PATTERN.search(line)
            if input_match is None:
                tx_hash_line = None
                router_line = None
                continue

            to_match = TO_PATTERN.search(line)
            value_match = VALUE_PATTERN.search(line)
            hash_match = HASH_PATTERN.search(line)
            results.append(
                LoggedTransaction(
                    tx_hash_line=tx_hash_line,
                    router_line=router_line,
                    tx_hash=f"0x{hash_match.group(1)}" if hash_match else None,
                    to=to_match.group(1) if to_match else None,
                    value=int(value_match.group(1)) if value_match else 0,
                    tx_input=input_match.group(1),
                )
            )
            tx_hash_line = None
            router_line = None

    return results


def _normalize_address(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    return Web3.to_checksum_address(value)


def _get_amount_out(amount_in: int, reserve_in: int, reserve_out: int) -> int:
    if amount_in <= 0 or reserve_in <= 0 or reserve_out <= 0:
        return 0
    amount_in_with_fee = amount_in * FEE_NUMERATOR
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * FEE_DENOMINATOR + amount_in_with_fee
    return numerator // denominator


def _simulate_v2_path(amount_in: int, reserves_by_hop: list[tuple[int, int]]) -> int:
    amount = amount_in
    for reserve_in, reserve_out in reserves_by_hop:
        amount = _get_amount_out(amount, reserve_in, reserve_out)
        if amount <= 0:
            return 0
    return amount


def _apply_victim_to_forward_reserves(
    reserves_by_hop: list[tuple[int, int]],
    victim_amount_in: int,
) -> list[tuple[int, int]]:
    if not reserves_by_hop:
        return []

    first_reserve_in, first_reserve_out = reserves_by_hop[0]
    victim_amount_out = _get_amount_out(victim_amount_in, first_reserve_in, first_reserve_out)
    updated_reserves = list(reserves_by_hop)
    updated_reserves[0] = (
        first_reserve_in + victim_amount_in,
        first_reserve_out - victim_amount_out,
    )
    return updated_reserves


def _get_v2_reserves_for_path(w3: Web3, factory_address: str, path: list[str]) -> list[tuple[int, int]] | None:
    factory = w3.eth.contract(address=Web3.to_checksum_address(factory_address), abi=FACTORY_ABI)
    reserves_by_hop = []
    for token_in, token_out in zip(path, path[1:]):
        token_in = Web3.to_checksum_address(token_in)
        token_out = Web3.to_checksum_address(token_out)
        pair_address = factory.functions.getPair(token_in, token_out).call()
        if int(pair_address, 16) == 0:
            return None
        pair = w3.eth.contract(address=pair_address, abi=PAIR_ABI)
        reserve0, reserve1, _ = pair.functions.getReserves().call()
        token0 = pair.functions.token0().call()
        if token0.lower() == token_in.lower():
            reserves_by_hop.append((reserve0, reserve1))
        else:
            reserves_by_hop.append((reserve1, reserve0))
    return reserves_by_hop


def _extract_supported_swap(logged_tx: LoggedTransaction) -> dict | None:
    decoded = _decode_input_structured(logged_tx.tx_input)
    if decoded is None:
        return None

    method_name = decoded.get("method_name")
    args = decoded.get("args") or {}
    to_address = _normalize_address(logged_tx.to)
    if to_address is None:
        return None
    if to_address.lower() not in ROUTER_TO_FACTORY:
        return None

    supported_methods = {
        "swapExactTokensForTokens",
        "swapExactTokensForETH",
        "swapExactETHForTokens",
    }
    if method_name not in supported_methods:
        return None

    if method_name == "swapExactETHForTokens":
        amount_in = logged_tx.value
        path = args.get("path")
    else:
        amount_in = args.get("amountIn")
        path = args.get("path")

    if not isinstance(amount_in, int) or amount_in <= 0:
        return None
    if not isinstance(path, list) or len(path) != 2:
        return None
    if not all(isinstance(token, str) for token in path):
        return None

    return {
        "router": to_address,
        "router_name": ROUTER_TO_NAME[to_address.lower()],
        "method_name": method_name,
        "amount_in": amount_in,
        "path": [Web3.to_checksum_address(token) for token in path],
        "decoded": decoded,
    }


def _format_int(value: int) -> str:
    return f"{value:,}"


def estimate_log(log_path: Path, rpc_url: str) -> None:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise RuntimeError(f"Unable to connect to RPC URL: {rpc_url}")

    for logged_tx in _extract_logged_transactions(log_path):
        supported_swap = _extract_supported_swap(logged_tx)
        if supported_swap is None:
            continue

        router = supported_swap["router"].lower()
        other_router = (
            SUSHISWAP_ROUTER if router == UNISWAP_V2_ROUTER else UNISWAP_V2_ROUTER
        )
        victim_factory = ROUTER_TO_FACTORY[router]
        other_factory = ROUTER_TO_FACTORY[other_router]
        path = supported_swap["path"]
        amount_in = supported_swap["amount_in"]

        victim_reserves = _get_v2_reserves_for_path(w3, victim_factory, path)
        other_reserves = _get_v2_reserves_for_path(w3, other_factory, path)
        reverse_victim_reserves = _get_v2_reserves_for_path(
            w3, victim_factory, list(reversed(path))
        )
        reverse_other_reserves = _get_v2_reserves_for_path(
            w3, other_factory, list(reversed(path))
        )
        if not all(
            reserves is not None
            for reserves in (
                victim_reserves,
                other_reserves,
                reverse_victim_reserves,
                reverse_other_reserves,
            )
        ):
            continue

        victim_route_out = _simulate_v2_path(amount_in, victim_reserves)
        alt_route_out = _simulate_v2_path(amount_in, other_reserves)
        current_cycle_victim_to_other = _simulate_v2_path(
            amount_in, victim_reserves + reverse_other_reserves
        )
        current_cycle_other_to_victim = _simulate_v2_path(
            amount_in, other_reserves + reverse_victim_reserves
        )
        updated_victim_forward_reserves = _apply_victim_to_forward_reserves(
            victim_reserves, amount_in
        )
        updated_victim_reverse_reserves = [
            (reserve_out, reserve_in)
            for reserve_in, reserve_out in updated_victim_forward_reserves
        ]
        post_victim_cycle_other_to_victim = _simulate_v2_path(
            amount_in,
            other_reserves + updated_victim_reverse_reserves,
        )

        print(logged_tx.tx_hash_line or f"TRANSACTION HASH: {logged_tx.tx_hash or 'unknown'}")
        if logged_tx.router_line:
            print(logged_tx.router_line)
        print(
            "estimate_scope="
            "rough_v2_two_pool_cycle current_state_and_post_victim same_size only"
        )
        print(
            f"tx_router={supported_swap['router_name']} "
            f"method={supported_swap['method_name']} "
            f"amount_in={_format_int(amount_in)}"
        )
        print(f"path={path[0]} -> {path[1]}")
        print(
            f"route_out_current[{supported_swap['router_name']}]={_format_int(victim_route_out)}"
        )
        print(
            f"route_out_current[{ROUTER_TO_NAME[other_router]}]={_format_int(alt_route_out)}"
        )
        print(
            f"gross_cycle_current[{supported_swap['router_name']}->"
            f"{ROUTER_TO_NAME[other_router]}]={_format_int(current_cycle_victim_to_other - amount_in)}"
        )
        print(
            f"gross_cycle_current[{ROUTER_TO_NAME[other_router]}->"
            f"{supported_swap['router_name']}]={_format_int(current_cycle_other_to_victim - amount_in)}"
        )
        print(
            f"gross_cycle_post_victim[{ROUTER_TO_NAME[other_router]}->"
            f"{supported_swap['router_name']}]={_format_int(post_victim_cycle_other_to_victim - amount_in)}"
        )
        print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("filename", help="log file containing captured transactions")
    parser.add_argument(
        "--rpc-url",
        default=_derive_rpc_url(),
        help="HTTP RPC URL; defaults to RPC_URL or one derived from WSS_URL",
    )
    args = parser.parse_args()

    if not args.rpc_url:
        raise SystemExit("Missing --rpc-url and could not derive RPC_URL from environment")

    estimate_log(Path(args.filename), args.rpc_url)


if __name__ == "__main__":
    main()
