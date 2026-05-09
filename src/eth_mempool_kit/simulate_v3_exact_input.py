import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from web3 import Web3

from .decode_mempool import _decode_input_structured
from .simulate_v3_exact_input_single import (
    DEFAULT_DECODED_LOG,
    ERC20_ABI,
    V3_FACTORY_ABI,
    V3_POOL_ABI,
    UNISWAP_V3_FACTORY,
    UNISWAP_V3_QUOTER,
    _derive_rpc_url,
    _format_amount,
    _format_delta,
    _make_request,
    _normalize_hash,
    _parse_decoded_log,
    _replay_victim_transaction,
    _reset_anvil_to_pre_tx_state,
    _safe_token_symbol,
)


load_dotenv(find_dotenv(usecwd=True), override=True)

V3_QUOTER_EXACT_INPUT_ABI = [
    {
        "inputs": [
            {"internalType": "bytes", "name": "path", "type": "bytes"},
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        ],
        "name": "quoteExactInput",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


@dataclass
class HopSnapshot:
    pool_address: str
    token0: str
    token1: str
    fee: int
    tick_spacing: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    token0_pool_balance: int
    token1_pool_balance: int


@dataclass
class RouteSnapshot:
    block_number: int
    sender_token_in_balance: int
    sender_token_out_balance: int
    sender_eth_balance: int
    quote_amount_out: int | None
    hop_snapshots: list[HopSnapshot]


def _get_token_contract(w3: Web3, address: str):
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)


def _parse_v3_path(path_hex: str) -> tuple[list[str], list[int], bytes]:
    if not isinstance(path_hex, str) or not path_hex.startswith("0x"):
        raise RuntimeError("Unexpected exactInput path encoding")

    path_bytes = bytes.fromhex(path_hex[2:])
    if len(path_bytes) < 43 or (len(path_bytes) - 20) % 23 != 0:
        raise RuntimeError("Invalid V3 path length")

    tokens = []
    fees = []
    offset = 0
    tokens.append(Web3.to_checksum_address(path_bytes[offset : offset + 20].hex()))
    offset += 20
    while offset < len(path_bytes):
        fees.append(int.from_bytes(path_bytes[offset : offset + 3], "big"))
        offset += 3
        tokens.append(Web3.to_checksum_address(path_bytes[offset : offset + 20].hex()))
        offset += 20
    return tokens, fees, path_bytes


def _get_v3_pool_address(w3: Web3, token_in: str, token_out: str, fee: int) -> str:
    factory = w3.eth.contract(
        address=Web3.to_checksum_address(UNISWAP_V3_FACTORY), abi=V3_FACTORY_ABI
    )
    pool_address = factory.functions.getPool(
        Web3.to_checksum_address(token_in),
        Web3.to_checksum_address(token_out),
        fee,
    ).call()
    if int(pool_address, 16) == 0:
        raise RuntimeError("Uniswap V3 pool not found for token pair and fee tier")
    return Web3.to_checksum_address(pool_address)


def _quote_exact_input(w3: Web3, path_bytes: bytes, amount_in: int) -> int:
    quoter = w3.eth.contract(
        address=Web3.to_checksum_address(UNISWAP_V3_QUOTER), abi=V3_QUOTER_EXACT_INPUT_ABI
    )
    return quoter.functions.quoteExactInput(path_bytes, amount_in).call()


def _try_quote_exact_input(w3: Web3, path_bytes: bytes, amount_in: int) -> int | None:
    try:
        return _quote_exact_input(w3, path_bytes, amount_in)
    except Exception:
        return None


def _snapshot_hop_state(w3: Web3, pool_address: str) -> HopSnapshot:
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=V3_POOL_ABI)
    slot0 = pool.functions.slot0().call()
    liquidity = pool.functions.liquidity().call()
    token0 = Web3.to_checksum_address(pool.functions.token0().call())
    token1 = Web3.to_checksum_address(pool.functions.token1().call())
    fee = pool.functions.fee().call()
    tick_spacing = pool.functions.tickSpacing().call()
    token0_contract = _get_token_contract(w3, token0)
    token1_contract = _get_token_contract(w3, token1)
    return HopSnapshot(
        pool_address=pool_address,
        token0=token0,
        token1=token1,
        fee=fee,
        tick_spacing=tick_spacing,
        sqrt_price_x96=slot0[0],
        tick=slot0[1],
        liquidity=liquidity,
        token0_pool_balance=token0_contract.functions.balanceOf(pool.address).call(),
        token1_pool_balance=token1_contract.functions.balanceOf(pool.address).call(),
    )


def _snapshot_route_state(
    w3: Web3,
    tokens: list[str],
    fees: list[int],
    sender: str,
    amount_in: int,
    path_bytes: bytes,
) -> RouteSnapshot:
    token_in_contract = _get_token_contract(w3, tokens[0])
    token_out_contract = _get_token_contract(w3, tokens[-1])
    hop_snapshots = [
        _snapshot_hop_state(w3, _get_v3_pool_address(w3, token_in, token_out, fee))
        for token_in, token_out, fee in zip(tokens, tokens[1:], fees)
    ]
    return RouteSnapshot(
        block_number=w3.eth.block_number,
        sender_token_in_balance=token_in_contract.functions.balanceOf(sender).call(),
        sender_token_out_balance=token_out_contract.functions.balanceOf(sender).call(),
        sender_eth_balance=w3.eth.get_balance(sender),
        quote_amount_out=_try_quote_exact_input(w3, path_bytes, amount_in),
        hop_snapshots=hop_snapshots,
    )


def _format_optional_amount(value: int | None) -> str:
    return "unavailable" if value is None else _format_amount(value)


def _select_exact_input_entries(log_entries, tx_hash: str | None):
    if tx_hash is not None:
        normalized = _normalize_hash(tx_hash)
        for entry in log_entries:
            if entry.tx_hash == normalized:
                if entry.decoded_method != "exactInput":
                    raise RuntimeError(f"Transaction is not an exactInput entry: {normalized}")
                return [entry]
        raise RuntimeError(f"Transaction hash not found in decoded log: {normalized}")

    selected = [entry for entry in log_entries if entry.decoded_method == "exactInput"]
    if not selected:
        raise RuntimeError("No exactInput entry found in decoded log")
    return selected


def _parse_exact_input_params(decoded: dict) -> tuple[str, str, int | None, int, int, list[str], list[int], bytes]:
    params = (decoded.get("args") or {}).get("params")
    if not isinstance(params, list):
        raise RuntimeError("Unexpected exactInput params shape")

    if len(params) == 4:
        path_hex, recipient, amount_in, amount_out_min = params
        deadline = None
    elif len(params) == 5:
        path_hex, recipient, deadline, amount_in, amount_out_min = params
    else:
        raise RuntimeError("Unexpected exactInput params length")

    if not isinstance(path_hex, str) or not isinstance(recipient, str):
        raise RuntimeError("Unexpected exactInput path/recipient fields")
    numeric_values = [amount_in, amount_out_min]
    if deadline is not None:
        numeric_values.append(deadline)
    if not all(isinstance(value, int) for value in numeric_values):
        raise RuntimeError("Unexpected exactInput numeric fields")

    tokens, fees, path_bytes = _parse_v3_path(path_hex)
    return path_hex, Web3.to_checksum_address(recipient), deadline, amount_in, amount_out_min, tokens, fees, path_bytes


def simulate_v3_exact_input(
    decoded_log_path: Path,
    tx_hash: str | None,
    anvil_url: str,
    rpc_url: str,
    gas_limit: int,
) -> None:
    log_entries = _parse_decoded_log(decoded_log_path)
    if not log_entries:
        raise RuntimeError(f"No decoded transactions found in {decoded_log_path}")

    upstream_w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not upstream_w3.is_connected():
        raise RuntimeError(f"Unable to connect to upstream RPC: {rpc_url}")

    anvil_w3 = Web3(Web3.HTTPProvider(anvil_url))
    if not anvil_w3.is_connected():
        raise RuntimeError(f"Unable to connect to Anvil: {anvil_url}")

    selected_entries = _select_exact_input_entries(log_entries, tx_hash)
    for index, selected in enumerate(selected_entries):
        if index:
            print()
        print(selected.tx_hash_line)
        if selected.router is not None:
            print(f"router={selected.router}")

        try:
            tx = upstream_w3.eth.get_transaction(selected.tx_hash)
            decoded = _decode_input_structured(tx["input"])
            if decoded is None or decoded.get("method_name") != "exactInput":
                raise RuntimeError("Selected transaction is not a decodable exactInput swap")
            receipt = upstream_w3.eth.get_transaction_receipt(selected.tx_hash)
            reset_block = receipt["blockNumber"] - 1
            _reset_anvil_to_pre_tx_state(anvil_w3, rpc_url, reset_block)

            (
                path_hex,
                recipient,
                deadline,
                amount_in,
                amount_out_min,
                tokens,
                fees,
                path_bytes,
            ) = _parse_exact_input_params(decoded)
            sender = Web3.to_checksum_address(tx["from"])

            pre = _snapshot_route_state(anvil_w3, tokens, fees, sender, amount_in, path_bytes)
            replay_receipt = _replay_victim_transaction(anvil_w3, tx, gas_limit)
            post = _snapshot_route_state(anvil_w3, tokens, fees, sender, amount_in, path_bytes)
        except Exception as err:
            print("simulation_scope=replay_victim_exact_input fork_based")
            print(f"upstream_tx_hash={selected.tx_hash}")
            print(f"simulation_error={err}")
            continue

        token_in_contract = _get_token_contract(anvil_w3, tokens[0])
        token_out_contract = _get_token_contract(anvil_w3, tokens[-1])
        token_in_symbol = _safe_token_symbol(token_in_contract)
        token_out_symbol = _safe_token_symbol(token_out_contract)

        print("simulation_scope=replay_victim_exact_input fork_based")
        print(f"upstream_tx_hash={selected.tx_hash}")
        print(f"fork_reset_block={reset_block}")
        print(f"anvil_block_before={pre.block_number}")
        print(f"anvil_block_after={post.block_number}")
        print(f"sender={sender}")
        print(f"recipient={recipient}")
        if deadline is not None:
            print(f"deadline={deadline}")
        print(f"path={token_in_symbol}({tokens[0]}) -> {token_out_symbol}({tokens[-1]})")
        print(f"path_hex={path_hex}")
        print(f"hop_count={len(fees)}")
        print(f"amount_in={_format_amount(amount_in)}")
        print(f"amount_out_min={_format_amount(amount_out_min)}")
        print(f"replay_tx_hash={replay_receipt['transactionHash'].hex()}")
        print(f"replay_status={replay_receipt['status']}")
        print(f"replay_gas_used={replay_receipt['gasUsed']}")
        print(f"quote_before={_format_optional_amount(pre.quote_amount_out)}")
        print(f"quote_after={_format_optional_amount(post.quote_amount_out)}")
        if pre.quote_amount_out is not None and post.quote_amount_out is not None:
            print(f"quote_delta={_format_delta(post.quote_amount_out, pre.quote_amount_out)}")
        else:
            print("quote_delta=unavailable")
        for hop_index, (token_in, token_out, fee, hop_pre, hop_post) in enumerate(
            zip(tokens, tokens[1:], fees, pre.hop_snapshots, post.hop_snapshots),
            start=1,
        ):
            print(f"hop_{hop_index}_pair={token_in}->{token_out}")
            print(f"hop_{hop_index}_fee_tier={fee}")
            print(f"hop_{hop_index}_pool={hop_pre.pool_address}")
            print(f"hop_{hop_index}_slot0_before.sqrtPriceX96={hop_pre.sqrt_price_x96}")
            print(f"hop_{hop_index}_slot0_before.tick={hop_pre.tick}")
            print(f"hop_{hop_index}_slot0_after.sqrtPriceX96={hop_post.sqrt_price_x96}")
            print(f"hop_{hop_index}_slot0_after.tick={hop_post.tick}")
            print(f"hop_{hop_index}_slot0_delta.tick={hop_post.tick - hop_pre.tick}")
            print(f"hop_{hop_index}_liquidity_before={hop_pre.liquidity}")
            print(f"hop_{hop_index}_liquidity_after={hop_post.liquidity}")
            print(f"hop_{hop_index}_tick_spacing={hop_pre.tick_spacing}")
            print(f"hop_{hop_index}_pool_token0_balance_before={_format_amount(hop_pre.token0_pool_balance)}")
            print(f"hop_{hop_index}_pool_token0_balance_after={_format_amount(hop_post.token0_pool_balance)}")
            print(f"hop_{hop_index}_pool_token1_balance_before={_format_amount(hop_pre.token1_pool_balance)}")
            print(f"hop_{hop_index}_pool_token1_balance_after={_format_amount(hop_post.token1_pool_balance)}")
        print(f"sender_token_in_balance_before={_format_amount(pre.sender_token_in_balance)}")
        print(f"sender_token_in_balance_after={_format_amount(post.sender_token_in_balance)}")
        print(f"sender_token_out_balance_before={_format_amount(pre.sender_token_out_balance)}")
        print(f"sender_token_out_balance_after={_format_amount(post.sender_token_out_balance)}")
        print(f"sender_eth_balance_before={_format_amount(pre.sender_eth_balance)}")
        print(f"sender_eth_balance_after={_format_amount(post.sender_eth_balance)}")
