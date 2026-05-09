import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from web3 import Web3

from .decode_mempool import _decode_input_structured


load_dotenv(find_dotenv(usecwd=True), override=True)

DEFAULT_DECODED_LOG = Path(__file__).with_name("decoded_20260501_1528.log")
TX_HASH_RE = re.compile(r"TRANSACTION HASH:\s*([0-9a-fA-Fx]+)")
ROUTER_RE = re.compile(r"^router=(.+)$")
METHOD_RE = re.compile(r"^decoded_method:\s*(.+)$")

UNISWAP_V3_ROUTER = "0xe592427a0aece92de3edee1f18e0157c05861564"
UNISWAP_V3_ROUTER_02 = "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"
UNISWAP_V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
UNISWAP_V3_QUOTER = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"
MAX_SENDER_ETH_WEI = 10**21

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
V3_POOL_ABI = [
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {
                "internalType": "uint16",
                "name": "observationCardinalityNext",
                "type": "uint16",
            },
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
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
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "tickSpacing",
        "outputs": [{"internalType": "int24", "name": "", "type": "int24"}],
        "stateMutability": "view",
        "type": "function",
    },
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
    }
]
ERC20_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass
class DecodedLogEntry:
    tx_hash: str
    tx_hash_line: str
    router: str | None = None
    decoded_method: str | None = None


@dataclass
class PoolSnapshot:
    block_number: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    token0: str
    token1: str
    fee: int
    tick_spacing: int
    token0_pool_balance: int
    token1_pool_balance: int
    sender_token_in_balance: int
    sender_token_out_balance: int
    sender_eth_balance: int
    quote_amount_out: int | None


def _derive_rpc_url() -> str | None:
    erigon_rpc_url = os.getenv("ERIGON_RPC_URL")
    if erigon_rpc_url:
        return erigon_rpc_url

    rpc_url = os.getenv("RPC_URL")
    if rpc_url:
        return rpc_url

    https_url = os.getenv("HTTPS_URL")
    if https_url:
        return https_url

    wss_url = os.getenv("WSS_URL")
    if not wss_url:
        return None
    if wss_url.startswith("wss://"):
        return "https://" + wss_url[len("wss://") :]
    if wss_url.startswith("ws://"):
        return "http://" + wss_url[len("ws://") :]
    return None


def _normalize_hash(value: str) -> str:
    value = value.strip()
    return value.lower() if value.startswith("0x") else f"0x{value.lower()}"


def _parse_decoded_log(log_path: Path) -> list[DecodedLogEntry]:
    entries = []
    current = None

    with log_path.open() as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            tx_match = TX_HASH_RE.search(line)
            if tx_match:
                tx_hash = _normalize_hash(tx_match.group(1))
                current = DecodedLogEntry(tx_hash=tx_hash, tx_hash_line=line)
                entries.append(current)
                continue

            if current is None:
                continue

            router_match = ROUTER_RE.match(line)
            if router_match:
                current.router = router_match.group(1).strip()
                continue

            method_match = METHOD_RE.match(line)
            if method_match:
                current.decoded_method = method_match.group(1).strip()
                continue

    return entries


def _select_entry(log_entries: list[DecodedLogEntry], tx_hash: str | None) -> DecodedLogEntry:
    if tx_hash is not None:
        normalized = _normalize_hash(tx_hash)
        for entry in log_entries:
            if entry.tx_hash == normalized:
                return entry
        raise ValueError(f"Transaction hash not found in decoded log: {normalized}")

    for entry in log_entries:
        if entry.decoded_method == "exactInputSingle":
            return entry
    raise ValueError("No exactInputSingle entry found in decoded log")


def _select_entries(log_entries: list[DecodedLogEntry], tx_hash: str | None) -> list[DecodedLogEntry]:
    if tx_hash is not None:
        return [_select_entry(log_entries, tx_hash)]

    selected = [entry for entry in log_entries if entry.decoded_method == "exactInputSingle"]
    if not selected:
        raise ValueError("No exactInputSingle entry found in decoded log")
    return selected


def _make_request(w3: Web3, method: str, params: list):
    response = w3.provider.make_request(method, params)
    if "error" in response:
        raise RuntimeError(f"{method} failed: {response['error']}")
    return response.get("result")


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


def _get_token_contract(w3: Web3, address: str):
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)


def _safe_token_symbol(token_contract) -> str:
    try:
        return token_contract.functions.symbol().call()
    except Exception:
        return token_contract.address


def _get_quote_amount_out(
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


def _try_get_quote_amount_out(
    w3: Web3,
    token_in: str,
    token_out: str,
    fee: int,
    amount_in: int,
    sqrt_price_limit_x96: int,
) -> int | None:
    try:
        return _get_quote_amount_out(
            w3,
            token_in,
            token_out,
            fee,
            amount_in,
            sqrt_price_limit_x96,
        )
    except Exception:
        return None


def _snapshot_pool_state(
    w3: Web3,
    pool_address: str,
    token_in: str,
    token_out: str,
    sender: str,
    amount_in: int,
    sqrt_price_limit_x96: int,
) -> PoolSnapshot:
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=V3_POOL_ABI)
    slot0 = pool.functions.slot0().call()
    liquidity = pool.functions.liquidity().call()
    token0 = Web3.to_checksum_address(pool.functions.token0().call())
    token1 = Web3.to_checksum_address(pool.functions.token1().call())
    fee = pool.functions.fee().call()
    tick_spacing = pool.functions.tickSpacing().call()

    token0_contract = _get_token_contract(w3, token0)
    token1_contract = _get_token_contract(w3, token1)
    token_in_contract = _get_token_contract(w3, token_in)
    token_out_contract = _get_token_contract(w3, token_out)

    quote_amount_out = _try_get_quote_amount_out(
        w3, token_in, token_out, fee, amount_in, sqrt_price_limit_x96
    )

    return PoolSnapshot(
        block_number=w3.eth.block_number,
        sqrt_price_x96=slot0[0],
        tick=slot0[1],
        liquidity=liquidity,
        token0=token0,
        token1=token1,
        fee=fee,
        tick_spacing=tick_spacing,
        token0_pool_balance=token0_contract.functions.balanceOf(pool.address).call(),
        token1_pool_balance=token1_contract.functions.balanceOf(pool.address).call(),
        sender_token_in_balance=token_in_contract.functions.balanceOf(sender).call(),
        sender_token_out_balance=token_out_contract.functions.balanceOf(sender).call(),
        sender_eth_balance=w3.eth.get_balance(sender),
        quote_amount_out=quote_amount_out,
    )


def _format_delta(after: int, before: int) -> str:
    delta = after - before
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:,}"


def _format_amount(value: int) -> str:
    return f"{value:,}"


def _format_optional_amount(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return _format_amount(value)


def _reset_anvil_to_pre_tx_state(anvil_w3: Web3, fork_rpc_url: str, block_number: int) -> None:
    _make_request(
        anvil_w3,
        "anvil_reset",
        [{"forking": {"jsonRpcUrl": fork_rpc_url, "blockNumber": block_number}}],
    )


def _impersonate_account(anvil_w3: Web3, address: str) -> None:
    _make_request(anvil_w3, "anvil_impersonateAccount", [address])


def _stop_impersonating_account(anvil_w3: Web3, address: str) -> None:
    _make_request(anvil_w3, "anvil_stopImpersonatingAccount", [address])


def _set_eth_balance(anvil_w3: Web3, address: str, balance_wei: int) -> None:
    _make_request(anvil_w3, "anvil_setBalance", [address, hex(balance_wei)])


def _load_target_transaction(
    upstream_w3: Web3,
    selected: DecodedLogEntry,
):
    tx = upstream_w3.eth.get_transaction(selected.tx_hash)
    decoded = _decode_input_structured(tx["input"])
    if decoded is None or decoded.get("method_name") != "exactInputSingle":
        raise RuntimeError("Selected transaction is not a decodable exactInputSingle swap")
    return selected, tx, decoded


def _parse_exact_input_single_params(decoded: dict) -> tuple[str, str, int, str, int | None, int, int, int]:
    params = (decoded.get("args") or {}).get("params")
    if not isinstance(params, list):
        raise RuntimeError("Unexpected exactInputSingle params shape")

    if len(params) == 7:
        token_in, token_out, fee, recipient, amount_in, amount_out_min, sqrt_price_limit_x96 = params
        deadline = None
    elif len(params) == 8:
        (
            token_in,
            token_out,
            fee,
            recipient,
            deadline,
            amount_in,
            amount_out_min,
            sqrt_price_limit_x96,
        ) = params
    else:
        raise RuntimeError("Unexpected exactInputSingle params length")

    if not all(isinstance(value, str) for value in (token_in, token_out, recipient)):
        raise RuntimeError("Unexpected exactInputSingle address fields")

    numeric_values = [fee, amount_in, amount_out_min, sqrt_price_limit_x96]
    if deadline is not None:
        numeric_values.append(deadline)
    if not all(isinstance(value, int) for value in numeric_values):
        raise RuntimeError("Unexpected exactInputSingle numeric fields")

    return (
        token_in,
        token_out,
        fee,
        recipient,
        deadline,
        amount_in,
        amount_out_min,
        sqrt_price_limit_x96,
    )


def _replay_victim_transaction(
    anvil_w3: Web3,
    tx,
    gas_limit: int,
):
    sender = Web3.to_checksum_address(tx["from"])
    _impersonate_account(anvil_w3, sender)
    _set_eth_balance(anvil_w3, sender, MAX_SENDER_ETH_WEI)
    try:
        replay_hash = anvil_w3.eth.send_transaction(
            {
                "from": sender,
                "to": Web3.to_checksum_address(tx["to"]),
                "data": tx["input"],
                "value": int(tx["value"]),
                "gas": gas_limit,
            }
        )
        return anvil_w3.eth.wait_for_transaction_receipt(replay_hash)
    finally:
        _stop_impersonating_account(anvil_w3, sender)


def simulate_exact_input_single(
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

    selected_entries = _select_entries(log_entries, tx_hash)

    for index, selected in enumerate(selected_entries):
        if index:
            print()
        print(selected.tx_hash_line)
        if selected.router is not None:
            print(f"router={selected.router}")

        try:
            selected, upstream_tx, decoded = _load_target_transaction(upstream_w3, selected)
            receipt = upstream_w3.eth.get_transaction_receipt(selected.tx_hash)
            reset_block = receipt["blockNumber"] - 1
            _reset_anvil_to_pre_tx_state(anvil_w3, rpc_url, reset_block)

            (
                token_in,
                token_out,
                fee,
                recipient,
                deadline,
                amount_in,
                amount_out_min,
                sqrt_price_limit_x96,
            ) = _parse_exact_input_single_params(decoded)

            token_in = Web3.to_checksum_address(token_in)
            token_out = Web3.to_checksum_address(token_out)
            sender = Web3.to_checksum_address(upstream_tx["from"])
            pool_address = _get_v3_pool_address(anvil_w3, token_in, token_out, fee)

            token_in_contract = _get_token_contract(anvil_w3, token_in)
            token_out_contract = _get_token_contract(anvil_w3, token_out)
            token_in_symbol = _safe_token_symbol(token_in_contract)
            token_out_symbol = _safe_token_symbol(token_out_contract)

            pre = _snapshot_pool_state(
                anvil_w3,
                pool_address,
                token_in,
                token_out,
                sender,
                amount_in,
                sqrt_price_limit_x96,
            )
            replay_receipt = _replay_victim_transaction(anvil_w3, upstream_tx, gas_limit)
            post = _snapshot_pool_state(
                anvil_w3,
                pool_address,
                token_in,
                token_out,
                sender,
                amount_in,
                sqrt_price_limit_x96,
            )
        except Exception as err:
            print("simulation_scope=replay_victim_exact_input_single fork_based")
            print(f"upstream_tx_hash={selected.tx_hash}")
            print(f"simulation_error={err}")
            continue

        print(f"simulation_scope=replay_victim_exact_input_single fork_based")
        print(f"upstream_tx_hash={selected.tx_hash}")
        print(f"fork_reset_block={reset_block}")
        print(f"anvil_block_before={pre.block_number}")
        print(f"anvil_block_after={post.block_number}")
        print(f"sender={sender}")
        print(f"recipient={recipient}")
        if deadline is not None:
            print(f"deadline={deadline}")
        print(f"pool={pool_address}")
        print(f"path={token_in_symbol}({token_in}) -> {token_out_symbol}({token_out})")
        print(f"fee_tier={fee}")
        print(f"amount_in={_format_amount(amount_in)}")
        print(f"amount_out_min={_format_amount(amount_out_min)}")
        print(f"sqrt_price_limit_x96={sqrt_price_limit_x96}")
        print(f"replay_tx_hash={replay_receipt['transactionHash'].hex()}")
        print(f"replay_status={replay_receipt['status']}")
        print(f"replay_gas_used={replay_receipt['gasUsed']}")
        print(f"slot0_before.sqrtPriceX96={pre.sqrt_price_x96}")
        print(f"slot0_before.tick={pre.tick}")
        print(f"slot0_after.sqrtPriceX96={post.sqrt_price_x96}")
        print(f"slot0_after.tick={post.tick}")
        print(f"slot0_delta.tick={post.tick - pre.tick}")
        print(f"liquidity_before={pre.liquidity}")
        print(f"liquidity_after={post.liquidity}")
        print(f"tick_spacing={pre.tick_spacing}")
        print(f"quote_before={_format_optional_amount(pre.quote_amount_out)}")
        print(f"quote_after={_format_optional_amount(post.quote_amount_out)}")
        if pre.quote_amount_out is not None and post.quote_amount_out is not None:
            print(f"quote_delta={_format_delta(post.quote_amount_out, pre.quote_amount_out)}")
        else:
            print("quote_delta=unavailable")
        print(f"pool_token0_balance_before={_format_amount(pre.token0_pool_balance)}")
        print(f"pool_token0_balance_after={_format_amount(post.token0_pool_balance)}")
        print(f"pool_token1_balance_before={_format_amount(pre.token1_pool_balance)}")
        print(f"pool_token1_balance_after={_format_amount(post.token1_pool_balance)}")
        print(f"sender_token_in_balance_before={_format_amount(pre.sender_token_in_balance)}")
        print(f"sender_token_in_balance_after={_format_amount(post.sender_token_in_balance)}")
        print(f"sender_token_out_balance_before={_format_amount(pre.sender_token_out_balance)}")
        print(f"sender_token_out_balance_after={_format_amount(post.sender_token_out_balance)}")
        print(f"sender_eth_balance_before={_format_amount(pre.sender_eth_balance)}")
        print(f"sender_eth_balance_after={_format_amount(post.sender_eth_balance)}")
