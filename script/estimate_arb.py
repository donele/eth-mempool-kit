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
UNISWAP_V3_ROUTER = "0xe592427a0aece92de3edee1f18e0157c05861564"
UNISWAP_V3_ROUTER_02 = "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"
UNISWAP_V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
UNISWAP_V3_QUOTER = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"

ROUTER_TO_FACTORY = {
    UNISWAP_V2_ROUTER: "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
    SUSHISWAP_ROUTER: "0xC0AEe478e3658e2610c5F7A4A2E1777Ce9e4f2Ac",
}
ROUTER_TO_NAME = {
    UNISWAP_V2_ROUTER: "Uniswap V2",
    SUSHISWAP_ROUTER: "SushiSwap",
    UNISWAP_V3_ROUTER: "Uniswap V3",
    UNISWAP_V3_ROUTER_02: "Uniswap V3",
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
V3_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
V3_QUOTER_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenIn", "type": "address"},
            {"internalType": "address", "name": "tokenOut", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
        ],
        "name": "quoteExactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes", "name": "path", "type": "bytes"},
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        ],
        "name": "quoteExactInput",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
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


def _get_v2_reserves_for_path(
    w3: Web3, factory_address: str, path: list[str]
) -> list[tuple[int, int]] | None:
    factory = w3.eth.contract(
        address=Web3.to_checksum_address(factory_address), abi=FACTORY_ABI
    )
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


def _get_v3_pool_address(w3: Web3, token_in: str, token_out: str, fee: int) -> str | None:
    factory = w3.eth.contract(
        address=Web3.to_checksum_address(UNISWAP_V3_FACTORY), abi=V3_FACTORY_ABI
    )
    pool_address = factory.functions.getPool(
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
        fee,
    ).call()
    return None if int(pool_address, 16) == 0 else pool_address


def _get_v3_exact_input_single_quote(
    w3: Web3,
    token_in: str,
    token_out: str,
    fee: int,
    amount_in: int,
    sqrt_price_limit_x96: int,
) -> int:
    quoter = w3.eth.contract(
        address=Web3.to_checksum_address(UNISWAP_V3_QUOTER), abi=V3_QUOTER_ABI
    )
    return quoter.functions.quoteExactInputSingle(
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
        fee,
        amount_in,
        sqrt_price_limit_x96,
    ).call()


def _get_v3_exact_input_path_quote(w3: Web3, path_bytes: bytes, amount_in: int) -> int:
    quoter = w3.eth.contract(
        address=Web3.to_checksum_address(UNISWAP_V3_QUOTER), abi=V3_QUOTER_ABI
    )
    return quoter.functions.quoteExactInput(path_bytes, amount_in).call()


def _extract_supported_v2_swap(logged_tx: LoggedTransaction) -> dict | None:
    decoded = _decode_input_structured(logged_tx.tx_input)
    if decoded is None:
        return None

    method_name = decoded.get("method_name")
    args = decoded.get("args") or {}
    to_address = _normalize_address(logged_tx.to)
    if to_address is None or to_address.lower() not in ROUTER_TO_FACTORY:
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
        "venue_type": "v2",
        "method_name": method_name,
        "amount_in": amount_in,
        "path": [Web3.to_checksum_address(token) for token in path],
    }


def _extract_v3_one_hop_path(path_bytes: bytes) -> tuple[list[str], int] | None:
    if len(path_bytes) != 43:
        return None
    token_in = Web3.to_checksum_address(f"0x{path_bytes[:20].hex()}")
    fee = int.from_bytes(path_bytes[20:23], "big")
    token_out = Web3.to_checksum_address(f"0x{path_bytes[23:43].hex()}")
    return [token_in, token_out], fee


def _extract_supported_v3_swap(logged_tx: LoggedTransaction) -> dict | None:
    decoded = _decode_input_structured(logged_tx.tx_input)
    if decoded is None:
        return None

    method_name = decoded.get("method_name")
    args = decoded.get("args") or {}
    to_address = _normalize_address(logged_tx.to)
    if to_address is None or to_address.lower() not in {
        UNISWAP_V3_ROUTER,
        UNISWAP_V3_ROUTER_02,
    }:
        return None

    if method_name == "exactInputSingle":
        params = args.get("params")
        if not isinstance(params, list) or len(params) != 7:
            return None
        token_in, token_out, fee, _recipient, amount_in, _amount_out_min, sqrt_price_limit_x96 = params
        if not isinstance(token_in, str) or not isinstance(token_out, str):
            return None
        if not isinstance(fee, int) or not isinstance(amount_in, int):
            return None
        if not isinstance(sqrt_price_limit_x96, int):
            return None
        return {
            "router": to_address,
            "router_name": ROUTER_TO_NAME[to_address.lower()],
            "venue_type": "v3",
            "method_name": method_name,
            "amount_in": amount_in,
            "path": [
                Web3.to_checksum_address(token_in),
                Web3.to_checksum_address(token_out),
            ],
            "fee": fee,
            "sqrt_price_limit_x96": sqrt_price_limit_x96,
        }

    if method_name == "exactInput":
        params = args.get("params")
        if not isinstance(params, list) or len(params) != 5:
            return None
        path_hex, _recipient, _deadline, amount_in, _amount_out_min = params
        if not isinstance(path_hex, str) or not isinstance(amount_in, int):
            return None
        if not path_hex.startswith("0x"):
            return None
        path_bytes = bytes.fromhex(path_hex[2:])
        return {
            "router": to_address,
            "router_name": ROUTER_TO_NAME[to_address.lower()],
            "venue_type": "v3",
            "method_name": method_name,
            "amount_in": amount_in,
            "path_bytes": path_bytes,
            "one_hop": _extract_v3_one_hop_path(path_bytes),
        }

    return None


def _format_int(value: int) -> str:
    return f"{value:,}"


def _print_header(logged_tx: LoggedTransaction) -> None:
    print(logged_tx.tx_hash_line or f"TRANSACTION HASH: {logged_tx.tx_hash or 'unknown'}")
    if logged_tx.router_line:
        print(logged_tx.router_line)


def _estimate_v2_swap(w3: Web3, logged_tx: LoggedTransaction, supported_swap: dict) -> None:
    router = supported_swap["router"].lower()
    other_router = SUSHISWAP_ROUTER if router == UNISWAP_V2_ROUTER else UNISWAP_V2_ROUTER
    victim_factory = ROUTER_TO_FACTORY[router]
    other_factory = ROUTER_TO_FACTORY[other_router]
    path = supported_swap["path"]
    amount_in = supported_swap["amount_in"]

    victim_reserves = _get_v2_reserves_for_path(w3, victim_factory, path)
    other_reserves = _get_v2_reserves_for_path(w3, other_factory, path)
    reverse_victim_reserves = _get_v2_reserves_for_path(w3, victim_factory, list(reversed(path)))
    reverse_other_reserves = _get_v2_reserves_for_path(w3, other_factory, list(reversed(path)))
    if not all(
        reserves is not None
        for reserves in (
            victim_reserves,
            other_reserves,
            reverse_victim_reserves,
            reverse_other_reserves,
        )
    ):
        return

    victim_route_out = _simulate_v2_path(amount_in, victim_reserves)
    alt_route_out = _simulate_v2_path(amount_in, other_reserves)
    current_cycle_victim_to_other = _simulate_v2_path(
        amount_in, victim_reserves + reverse_other_reserves
    )
    current_cycle_other_to_victim = _simulate_v2_path(
        amount_in, other_reserves + reverse_victim_reserves
    )
    updated_victim_forward_reserves = _apply_victim_to_forward_reserves(victim_reserves, amount_in)
    updated_victim_reverse_reserves = [
        (reserve_out, reserve_in) for reserve_in, reserve_out in updated_victim_forward_reserves
    ]
    post_victim_cycle_other_to_victim = _simulate_v2_path(
        amount_in,
        other_reserves + updated_victim_reverse_reserves,
    )

    _print_header(logged_tx)
    print("estimate_scope=rough_v2_two_pool_cycle current_state_and_post_victim same_size only")
    print(
        f"tx_router={supported_swap['router_name']} "
        f"method={supported_swap['method_name']} "
        f"amount_in={_format_int(amount_in)}"
    )
    print(f"path={path[0]} -> {path[1]}")
    print(f"route_out_current[{supported_swap['router_name']}]={_format_int(victim_route_out)}")
    print(f"route_out_current[{ROUTER_TO_NAME[other_router]}]={_format_int(alt_route_out)}")
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


def _estimate_v3_swap(w3: Web3, logged_tx: LoggedTransaction, supported_swap: dict) -> None:
    amount_in = supported_swap["amount_in"]

    if supported_swap["method_name"] == "exactInputSingle":
        path = supported_swap["path"]
        fee = supported_swap["fee"]
        if _get_v3_pool_address(w3, path[0], path[1], fee) is None:
            return
        v3_amount_out = _get_v3_exact_input_single_quote(
            w3,
            path[0],
            path[1],
            fee,
            amount_in,
            supported_swap["sqrt_price_limit_x96"],
        )
        one_hop_path = path
        one_hop_fee = fee
    else:
        v3_amount_out = _get_v3_exact_input_path_quote(w3, supported_swap["path_bytes"], amount_in)
        one_hop = supported_swap.get("one_hop")
        one_hop_path = one_hop[0] if one_hop is not None else None
        one_hop_fee = one_hop[1] if one_hop is not None else None
        if one_hop_path is not None and _get_v3_pool_address(w3, one_hop_path[0], one_hop_path[1], one_hop_fee) is None:
            one_hop_path = None
            one_hop_fee = None

    _print_header(logged_tx)
    print("estimate_scope=rough_v3_current_quote no_post_victim_sim")
    print(
        f"tx_router={supported_swap['router_name']} "
        f"method={supported_swap['method_name']} "
        f"amount_in={_format_int(amount_in)}"
    )
    if one_hop_path is not None:
        print(f"path={one_hop_path[0]} -> {one_hop_path[1]} fee={one_hop_fee}")
    print(f"route_out_current[Uniswap V3]={_format_int(v3_amount_out)}")

    if one_hop_path is not None:
        for alt_router in (UNISWAP_V2_ROUTER, SUSHISWAP_ROUTER):
            alt_reserves = _get_v2_reserves_for_path(w3, ROUTER_TO_FACTORY[alt_router], one_hop_path)
            if alt_reserves is None:
                continue
            alt_amount_out = _simulate_v2_path(amount_in, alt_reserves)
            print(f"route_out_current[{ROUTER_TO_NAME[alt_router]}]={_format_int(alt_amount_out)}")
    print()


def estimate_log(log_path: Path, rpc_url: str) -> None:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise RuntimeError(f"Unable to connect to RPC URL: {rpc_url}")

    for logged_tx in _extract_logged_transactions(log_path):
        supported_v2_swap = _extract_supported_v2_swap(logged_tx)
        if supported_v2_swap is not None:
            _estimate_v2_swap(w3, logged_tx, supported_v2_swap)
            continue

        supported_v3_swap = _extract_supported_v3_swap(logged_tx)
        if supported_v3_swap is not None:
            _estimate_v3_swap(w3, logged_tx, supported_v3_swap)
            continue


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
