import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from eth_abi import decode
from web3 import Web3

from .decode_mempool import _decode_input_structured
from .simulate_v3_exact_input import (
    DEFAULT_DECODED_LOG,
    RouteSnapshot,
    _format_optional_amount,
    _get_token_contract,
    _parse_exact_input_params,
    _parse_v3_path,
    _quote_exact_input,
    _snapshot_route_state,
)
from .simulate_v3_exact_input_single import (
    _derive_rpc_url,
    _format_amount,
    _format_delta,
    _normalize_hash,
    _replay_victim_transaction,
    _reset_anvil_to_pre_tx_state,
    _safe_token_symbol,
    _parse_exact_input_single_params,
)


load_dotenv(override=True)

TX_HASH_RE = re.compile(r"TRANSACTION HASH:\s*([0-9a-fA-Fx]+)")
ROUTER_RE = re.compile(r"^router=(.+)$")
METHOD_RE = re.compile(r"^decoded_method:\s*(.+)$")
SELECTOR_RE = re.compile(r"^decoded_input:\s*selector=(0x[0-9a-fA-F]+)")

MULTICALL_WITH_DEADLINE = "0x5ae401dc"
MULTICALL_NO_DEADLINE = "0xac9650d8"
MULTICALL_SELECTORS = {MULTICALL_WITH_DEADLINE, MULTICALL_NO_DEADLINE}


@dataclass
class MulticallLogEntry:
    tx_hash: str
    tx_hash_line: str
    router: str | None = None
    selector: str | None = None
    decoded_method: str | None = None


@dataclass
class ExtractedSwap:
    embedded_method: str
    subcall_index: int
    deadline: int | None
    recipient: str
    amount_in: int
    amount_out_min: int
    sqrt_price_limit_x96: int | None
    tokens: list[str]
    fees: list[int]
    path_hex: str
    path_bytes: bytes | None


def _parse_multicall_log(log_path: Path) -> list[MulticallLogEntry]:
    entries = []
    current = None

    with log_path.open() as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            tx_match = TX_HASH_RE.search(line)
            if tx_match:
                current = MulticallLogEntry(
                    tx_hash=_normalize_hash(tx_match.group(1)),
                    tx_hash_line=line,
                )
                entries.append(current)
                continue

            if current is None:
                continue

            router_match = ROUTER_RE.match(line)
            if router_match:
                current.router = router_match.group(1).strip()
                continue

            selector_match = SELECTOR_RE.match(line)
            if selector_match:
                current.selector = selector_match.group(1).lower()
                continue

            method_match = METHOD_RE.match(line)
            if method_match:
                current.decoded_method = method_match.group(1).strip()
                continue

    return entries


def _decode_multicall_payload(tx_input: str) -> tuple[int | None, list[bytes]]:
    if not isinstance(tx_input, str) or not tx_input.startswith("0x"):
        raise RuntimeError("Unexpected multicall tx input encoding")

    selector = tx_input[:10].lower()
    raw = bytes.fromhex(tx_input[10:])
    if selector == MULTICALL_WITH_DEADLINE:
        deadline, data = decode(["uint256", "bytes[]"], raw)
        return deadline, list(data)
    if selector == MULTICALL_NO_DEADLINE:
        (data,) = decode(["bytes[]"], raw)
        return None, list(data)
    raise RuntimeError(f"Unsupported multicall selector: {selector}")


def _extract_embedded_swap(tx_input: str) -> ExtractedSwap:
    multicall_deadline, subcalls = _decode_multicall_payload(tx_input)
    for index, subcall in enumerate(subcalls):
        decoded = _decode_input_structured(f"0x{subcall.hex()}")
        if decoded is None:
            continue
        method_name = decoded.get("method_name")
        if method_name == "exactInput":
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
            return ExtractedSwap(
                embedded_method=method_name,
                subcall_index=index,
                deadline=deadline if deadline is not None else multicall_deadline,
                recipient=recipient,
                amount_in=amount_in,
                amount_out_min=amount_out_min,
                sqrt_price_limit_x96=None,
                tokens=tokens,
                fees=fees,
                path_hex=path_hex,
                path_bytes=path_bytes,
            )
        if method_name == "exactInputSingle":
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
            path_bytes = bytes.fromhex(token_in[2:]) + fee.to_bytes(3, "big") + bytes.fromhex(token_out[2:])
            path_hex = "0x" + path_bytes.hex()
            return ExtractedSwap(
                embedded_method=method_name,
                subcall_index=index,
                deadline=deadline if deadline is not None else multicall_deadline,
                recipient=Web3.to_checksum_address(recipient),
                amount_in=amount_in,
                amount_out_min=amount_out_min,
                sqrt_price_limit_x96=sqrt_price_limit_x96,
                tokens=[token_in, token_out],
                fees=[fee],
                path_hex=path_hex,
                path_bytes=path_bytes,
            )
    raise RuntimeError("No embedded exactInput or exactInputSingle call found in multicall")


def _select_multicall_entries(log_entries: list[MulticallLogEntry], tx_hash: str | None) -> list[MulticallLogEntry]:
    if tx_hash is not None:
        normalized = _normalize_hash(tx_hash)
        for entry in log_entries:
            if entry.tx_hash == normalized:
                if entry.selector not in MULTICALL_SELECTORS:
                    raise RuntimeError(f"Transaction is not a multicall entry: {normalized}")
                return [entry]
        raise RuntimeError(f"Transaction hash not found in decoded log: {normalized}")

    selected = [entry for entry in log_entries if entry.selector in MULTICALL_SELECTORS]
    if not selected:
        raise RuntimeError("No multicall entry found in decoded log")
    return selected


def simulate_v3_multicall(
    decoded_log_path: Path,
    tx_hash: str | None,
    anvil_url: str,
    rpc_url: str,
    gas_limit: int,
) -> None:
    log_entries = _parse_multicall_log(decoded_log_path)
    if not log_entries:
        raise RuntimeError(f"No decoded transactions found in {decoded_log_path}")

    upstream_w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not upstream_w3.is_connected():
        raise RuntimeError(f"Unable to connect to upstream RPC: {rpc_url}")

    anvil_w3 = Web3(Web3.HTTPProvider(anvil_url))
    if not anvil_w3.is_connected():
        raise RuntimeError(f"Unable to connect to Anvil: {anvil_url}")

    selected_entries = _select_multicall_entries(log_entries, tx_hash)
    for index, selected in enumerate(selected_entries):
        if index:
            print()
        print(selected.tx_hash_line)
        if selected.router is not None:
            print(f"router={selected.router}")

        try:
            tx = upstream_w3.eth.get_transaction(selected.tx_hash)
            swap = _extract_embedded_swap(tx["input"])
            receipt = upstream_w3.eth.get_transaction_receipt(selected.tx_hash)
            reset_block = receipt["blockNumber"] - 1
            _reset_anvil_to_pre_tx_state(anvil_w3, rpc_url, reset_block)

            sender = Web3.to_checksum_address(tx["from"])
            pre = _snapshot_route_state(
                anvil_w3, swap.tokens, swap.fees, sender, swap.amount_in, swap.path_bytes
            )
            replay_receipt = _replay_victim_transaction(anvil_w3, tx, gas_limit)
            post = _snapshot_route_state(
                anvil_w3, swap.tokens, swap.fees, sender, swap.amount_in, swap.path_bytes
            )
        except Exception as err:
            print("simulation_scope=replay_victim_multicall fork_based")
            print(f"upstream_tx_hash={selected.tx_hash}")
            print(f"simulation_error={err}")
            continue

        token_in_contract = _get_token_contract(anvil_w3, swap.tokens[0])
        token_out_contract = _get_token_contract(anvil_w3, swap.tokens[-1])
        token_in_symbol = _safe_token_symbol(token_in_contract)
        token_out_symbol = _safe_token_symbol(token_out_contract)

        print("simulation_scope=replay_victim_multicall fork_based")
        print(f"upstream_tx_hash={selected.tx_hash}")
        print(f"multicall_selector={selected.selector}")
        print(f"embedded_method={swap.embedded_method}")
        print(f"embedded_subcall_index={swap.subcall_index}")
        print(f"fork_reset_block={reset_block}")
        print(f"anvil_block_before={pre.block_number}")
        print(f"anvil_block_after={post.block_number}")
        print(f"sender={sender}")
        print(f"recipient={swap.recipient}")
        if swap.deadline is not None:
            print(f"deadline={swap.deadline}")
        print(f"path={token_in_symbol}({swap.tokens[0]}) -> {token_out_symbol}({swap.tokens[-1]})")
        print(f"path_hex={swap.path_hex}")
        print(f"hop_count={len(swap.fees)}")
        print(f"amount_in={_format_amount(swap.amount_in)}")
        print(f"amount_out_min={_format_amount(swap.amount_out_min)}")
        if swap.sqrt_price_limit_x96 is not None:
            print(f"sqrt_price_limit_x96={swap.sqrt_price_limit_x96}")
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
            zip(swap.tokens, swap.tokens[1:], swap.fees, pre.hop_snapshots, post.hop_snapshots),
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "decoded_log",
        nargs="?",
        default=str(DEFAULT_DECODED_LOG),
        help="decoded transaction log; defaults to script/decoded_20260501_1528.log",
    )
    parser.add_argument(
        "--tx-hash",
        help="target transaction hash; when omitted, simulate every multicall entry in the decoded log",
    )
    parser.add_argument(
        "--anvil-url",
        default=os.getenv("ANVIL_URL", "http://127.0.0.1:8545"),
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
        help="gas limit used for the replay transaction",
    )
    args = parser.parse_args()

    if not args.rpc_url:
        raise SystemExit("Missing --rpc-url and could not derive RPC_URL from environment")

    simulate_v3_multicall(
        decoded_log_path=Path(args.decoded_log),
        tx_hash=args.tx_hash,
        anvil_url=args.anvil_url,
        rpc_url=args.rpc_url,
        gas_limit=args.gas_limit,
    )


if __name__ == "__main__":
    main()
