import asyncio
import os
import time

from dotenv import load_dotenv
from eth_abi import decode
from web3 import AsyncWeb3, WebSocketProvider


load_dotenv(override=True)
WSS_URL = os.getenv("WSS_URL")
RUN_SECONDS = 180
KNOWN_ROUTER_ADDRESSES = {
    # Uniswap Permit2
    "0x000000000022d473030f116ddee9f6b43ac78ba3",
    # Uniswap V2 Router02
    "0x7a250d5630b4cf539739df2c5dacab4c659f2488",
    # Uniswap V3 old SwapRouter
    "0xe592427a0aece92de3edee1f18e0157c05861564",
    # Uniswap Universal Router
    "0xef1c6e67703c7bd7107eed8303fbe6ec2554bf6b",
    # Uniswap V3 SwapRouter02
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
    # SushiSwap Router
    "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f",
    # 1inch Router v5
    "0x1111111254eeb25477b68fb85ed929f73a960582",
    # 1inch Aggregation Router v6
    "0x111111125421ca6dc452d289314280a0f8842a65",
    # ParaSwap Augustus
    "0xdef171fe48cf0115b1d80b88dc8eab59176fee57",
    # 0x Exchange Proxy
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff",
    # 0x Exchange Proxy (legacy)
    "0x61935cbdd02287b511119ddb11aeb42f1593b7ef",
    # KyberSwap MetaAggregation
    "0x6131b5fae19ea4f9d964eac0408e4408b66337b5",
    # KyberSwap MetaAggregation (old)
    "0x9aabad3f75489902f3a48495025729a0af77d4b11",
    # KyberSwap Aggregation Executor
    "0x5644b4ddf6c126f90cf3ecb92120fd7190acb401",
    # OpenOcean
    "0x6352a56caadc4f1e25cd6c75970fa768a3304e64",
    # DODO v2 Proxy
    "0xa356867fdcea8e71aeaf87805808803806231fdc",
    # OKX DEX Router
    "0x3b86917369b83a6892f553609f3bad8ddfd549f5",
    # 1inch Fusion Settlement
    "0xa88800cd213da5ae406ce248380802bd53b47647",
    # CoW Protocol Vault Relayer
    "0xc92e8bdf79f0507f65a392b0ab4667716bfe0110",
    # MetaMask Swap Router
    "0x881d40237659c251811cec9c364ef91dc08d300c",
    # CoW Protocol Settlement
    "0x9008d19f58aabd9ed0d60971565aa8510560ab41",
}
ROUTER_LABELS = {
    "0x000000000022d473030f116ddee9f6b43ac78ba3": "Uniswap Permit2",
    "0x7a250d5630b4cf539739df2c5dacab4c659f2488": "Uniswap V2",
    "0xe592427a0aece92de3edee1f18e0157c05861564": "Uniswap V3 (old)",
    "0xef1c6e67703c7bd7107eed8303fbe6ec2554bf6b": "Uniswap Universal",
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45": "Uniswap V3",
    "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f": "SushiSwap",
    "0x1111111254eeb25477b68fb85ed929f73a960582": "1inch v5",
    "0x111111125421ca6dc452d289314280a0f8842a65": "1inch v6",
    "0xdef171fe48cf0115b1d80b88dc8eab59176fee57": "ParaSwap Augustus",
    "0xdef1c0ded9bec7f1a1670819833240f027b25eff": "0x Exchange Proxy",
    "0x61935cbdd02287b511119ddb11aeb42f1593b7ef": "0x Exchange Proxy (legacy)",
    "0x6131b5fae19ea4f9d964eac0408e4408b66337b5": "KyberSwap MetaAggregation",
    "0x9aabad3f75489902f3a48495025729a0af77d4b11": "KyberSwap MetaAggregation (old)",
    "0x5644b4ddf6c126f90cf3ecb92120fd7190acb401": "KyberSwap Aggregation Executor",
    "0x6352a56caadc4f1e25cd6c75970fa768a3304e64": "OpenOcean",
    "0xa356867fdcea8e71aeaf87805808803806231fdc": "DODO v2 Proxy",
    "0x3b86917369b83a6892f553609f3bad8ddfd549f5": "OKX DEX Router",
    "0xa88800cd213da5ae406ce248380802bd53b47647": "1inch Fusion Settlement",
    "0xc92e8bdf79f0507f65a392b0ab4667716bfe0110": "CoW Vault Relayer",
    "0x881d40237659c251811cec9c364ef91dc08d300c": "MetaMask Swap Router",
    "0x9008d19f58aabd9ed0d60971565aa8510560ab41": "CoW Settlement",
}
SELECTOR_LABELS = {
    "0x04e45aaf": "exactInputSingle",
    "0x09b81346": "exactInput",
    "0x38ed1739": "swapExactTokensForTokens",
    "0x18cbafe5": "swapExactTokensForETH",
    "0x7ff36ab5": "swapExactETHForTokens",
    "0xfb3bdb41": "swapETHForExactTokens",
    "0x4a25d94a": "swapTokensForExactETH",
    "0x8803dbee": "swapTokensForExactTokens",
    "0xb68fb020": "1inch aggregate-like",
    "0x07ed2379": "1inch aggregate-like",
    "0x414bf389": "exactInputSingle",
    "0xc04b8d59": "exactInput",
    "0xf28c0498": "exactOutput",
    "0xdb3e2198": "exactOutputSingle",
}
SELECTOR_DECODERS = {
    # Uniswap V2 Router
    "0x38ed1739": (
        "swapExactTokensForTokens",
        ["uint256", "uint256", "address[]", "address", "uint256"],
        ["amountIn", "amountOutMin", "path", "to", "deadline"],
    ),
    "0x18cbafe5": (
        "swapExactTokensForETH",
        ["uint256", "uint256", "address[]", "address", "uint256"],
        ["amountIn", "amountOutMin", "path", "to", "deadline"],
    ),
    "0x7ff36ab5": (
        "swapExactETHForTokens",
        ["uint256", "address[]", "address", "uint256"],
        ["amountOutMin", "path", "to", "deadline"],
    ),
    "0xfb3bdb41": (
        "swapETHForExactTokens",
        ["uint256", "address[]", "address", "uint256"],
        ["amountOut", "path", "to", "deadline"],
    ),
    "0x4a25d94a": (
        "swapTokensForExactETH",
        ["uint256", "uint256", "address[]", "address", "uint256"],
        ["amountOut", "amountInMax", "path", "to", "deadline"],
    ),
    "0x8803dbee": (
        "swapTokensForExactTokens",
        ["uint256", "uint256", "address[]", "address", "uint256"],
        ["amountOut", "amountInMax", "path", "to", "deadline"],
    ),
    # Uniswap V3 SwapRouter exactInputSingle
    "0x04e45aaf": (
        "exactInputSingle",
        ["(address,address,uint24,address,uint256,uint256,uint160)"],
        ["params"],
    ),
    "0x414bf389": (
        "exactInputSingle",
        ["(address,address,uint24,address,uint256,uint256,uint160)"],
        ["params"],
    ),
    "0xdb3e2198": (
        "exactOutputSingle",
        ["(address,address,uint24,address,uint256,uint256,uint160)"],
        ["params"],
    ),
    "0x09b81346": (
        "exactInput",
        ["(bytes,address,uint256,uint256,uint256)"],
        ["params"],
    ),
    "0xc04b8d59": (
        "exactInput",
        ["(bytes,address,uint256,uint256,uint256)"],
        ["params"],
    ),
    "0xf28c0498": (
        "exactOutput",
        ["(bytes,address,uint256,uint256,uint256)"],
        ["params"],
    ),
}


def _as_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _decode_input(tx_input) -> str:
    if tx_input is None:
        return "decoded_input: none"
    input_hex = tx_input.hex() if hasattr(tx_input, "hex") else str(tx_input)
    if not isinstance(input_hex, str):
        return "decoded_input: malformed"
    if not input_hex.startswith("0x"):
        input_hex = f"0x{input_hex}"
    if len(input_hex) < 10:
        return "decoded_input: malformed"
    selector = input_hex[:10].lower()
    label = SELECTOR_LABELS.get(selector, "unknown_selector")
    payload = input_hex[10:]
    words = len(payload) // 64
    return f"decoded_input: selector={selector} ({label}), words={words}"


def _stringify_decoded_value(value):
    if isinstance(value, (list, tuple)):
        return [_stringify_decoded_value(v) for v in value]
    if isinstance(value, bytes):
        return f"0x{value.hex()}"
    return value


def _decode_v3_path(path_bytes) -> str:
    if not isinstance(path_bytes, (bytes, bytearray)) or len(path_bytes) < 43:
        return "invalid_path"
    if (len(path_bytes) - 20) % 23 != 0:
        return "invalid_path"

    parts = []
    offset = 0
    token_in = f"0x{path_bytes[offset:offset+20].hex()}"
    offset += 20
    while offset < len(path_bytes):
        fee = int.from_bytes(path_bytes[offset:offset+3], "big")
        offset += 3
        token_out = f"0x{path_bytes[offset:offset+20].hex()}"
        offset += 20
        parts.append(f"{token_in}-[{fee}]->{token_out}")
        token_in = token_out
    return " | ".join(parts)


def _decode_1inch_heuristic(selector: str, payload: str) -> list[str]:
    lines = [f"decoded_1inch_heuristic: selector={selector}"]
    if len(payload) < 64 * 5:
        lines.append("  payload too short for heuristic fields")
        return lines

    words = [payload[i * 64 : (i + 1) * 64] for i in range(min(10, len(payload) // 64))]

    def as_addr(word: str) -> str:
        return f"0x{word[-40:]}"

    def as_int(word: str) -> int:
        return int(word, 16)

    if selector == "0x07ed2379":
        lines.append("  format_guess=1inch_v6_aggregation")
        lines.append(f"  srcToken_guess={as_addr(words[1])}")
        lines.append(f"  dstToken_guess={as_addr(words[2])}")
        lines.append(f"  amount_guess={as_int(words[5])}")
        lines.append(f"  minReturn_guess={as_int(words[6])}")
    elif selector == "0xb68fb020":
        lines.append("  format_guess=1inch_compact_aggregation")
        lines.append(f"  flags_or_amount_guess={as_int(words[0])}")
        lines.append(f"  route_blob_head=0x{words[1]}")
    else:
        lines.append("  no selector-specific heuristic parser")

    return lines


def _decode_input_verbose(tx_input, max_words: int = 12) -> list[str]:
    if tx_input is None:
        return ["decoded_input_detail: none"]
    input_hex = tx_input.hex() if hasattr(tx_input, "hex") else str(tx_input)
    if not isinstance(input_hex, str):
        return ["decoded_input_detail: malformed"]
    if not input_hex.startswith("0x"):
        input_hex = f"0x{input_hex}"
    if len(input_hex) < 10:
        return ["decoded_input_detail: malformed"]

    selector = input_hex[:10].lower()
    label = SELECTOR_LABELS.get(selector, "unknown_selector")
    payload = input_hex[10:]
    total_bytes = len(payload) // 2
    total_words = len(payload) // 64
    lines = [
        f"decoded_input_detail: selector={selector} ({label}), bytes={total_bytes}, words={total_words}"
    ]

    decoder = SELECTOR_DECODERS.get(selector)
    if decoder is not None:
        method_name, arg_types, arg_names = decoder
        try:
            raw = bytes.fromhex(payload)
            values = decode(arg_types, raw)
            lines.append(f"decoded_method: {method_name}")
            for arg_name, value in zip(arg_names, values):
                lines.append(f"  {arg_name}={_stringify_decoded_value(value)}")
            if method_name in ("exactInput", "exactOutput") and values:
                params = values[0]
                if isinstance(params, tuple) and len(params) >= 1:
                    path_bytes = params[0]
                    lines.append(f"  decoded_path={_decode_v3_path(path_bytes)}")
            return lines
        except Exception as err:
            lines.append(f"decoded_method_error: {err}")

    if selector in {"0x07ed2379", "0xb68fb020"}:
        lines.extend(_decode_1inch_heuristic(selector, payload))

    limit = min(total_words, max_words)
    for i in range(limit):
        chunk = payload[i * 64 : (i + 1) * 64]
        if len(chunk) != 64:
            continue
        int_val = int(chunk, 16)
        addr_candidate = f"0x{chunk[-40:]}"
        lines.append(
            f"  word[{i}] int={int_val} hex=0x{chunk} addr_guess={addr_candidate}"
        )
    if total_words > limit:
        lines.append(f"  ... ({total_words - limit} more words)")
    return lines


def router_interest_values(tx) -> tuple[int, int] | None:
    value_wei = _as_int(tx.get("value"))
    gas_price = _as_int(tx.get("gasPrice"))  # legacy fallback
    max_fee_per_gas = _as_int(tx.get("maxFeePerGas"))
    to_addr = tx.get("to")
    to_addr_normalized = to_addr.lower() if isinstance(to_addr, str) else ""

    known_router_hit = to_addr_normalized in KNOWN_ROUTER_ADDRESSES
    if not known_router_hit:
        return None
    effective_max_fee = max_fee_per_gas if max_fee_per_gas > 0 else gas_price
    return value_wei, effective_max_fee


async def stream_pending_transactions() -> None:
    if not WSS_URL:
        raise ValueError("Missing WSS_URL in .env")
    if not WSS_URL.startswith("wss://"):
        raise ValueError("WSS_URL must be a websocket URL starting with wss://")

    subscription_id = None
    async with AsyncWeb3(WebSocketProvider(WSS_URL)) as w3:
        connected = await w3.is_connected(show_traceback=True)
        print(connected)
        if not connected:
            return

        subscription_id = await w3.eth.subscribe("newPendingTransactions")
        print(f"Subscribed to pending transactions: {subscription_id}")

        try:
            started_at = time.monotonic()
            subscription_iter = w3.socket.process_subscriptions()
            total_seen = 0
            interesting_hits = 0
            while True:
                elapsed = time.monotonic() - started_at
                remaining = RUN_SECONDS - elapsed
                if remaining <= 0:
                    print("Reached runtime limit (180s). Exiting.")
                    break

                try:
                    message = await asyncio.wait_for(
                        anext(subscription_iter),
                        timeout=remaining,
                    )
                except asyncio.TimeoutError:
                    print("Reached runtime limit (180s). Exiting.")
                    break

                tx_hash = message.get("result")
                if not tx_hash:
                    continue
                total_seen += 1

                try:
                    # get_transaction() takes 5 - 50 ms
                    tx = await w3.eth.get_transaction(tx_hash)

                    # Instead of sending (public key, signature), Etherium sends (r, s, v)
                    # Then nodes reconstruct public key and deriveaddress
                    # tx.r = x-coordinate of a curve point
                    # tx.s = proof component tying message + private key
                    # tx.from is recovered and validated by the node from (r, s, v)

                    interest_values = router_interest_values(tx)
                    if interest_values is not None:
                        interesting_hits += 1
                        _, max_fee_per_gas = interest_values
                        printable_hash = (
                            tx_hash.hex()
                            if isinstance(tx_hash, (bytes, bytearray))
                            else tx_hash
                        )
                        if isinstance(printable_hash, str) and not printable_hash.startswith("0x"):
                            printable_hash = f"0x{printable_hash}"
                        to_addr = tx.get("to")
                        to_label = ROUTER_LABELS.get(
                            to_addr.lower() if isinstance(to_addr, str) else "",
                            "router",
                        )
                        print(f"TRANSACTION HASH: {printable_hash}")
                        print(f"Router={to_label}, fee={max_fee_per_gas}")
                        print(tx)
                        print(_decode_input(tx.get("input")))
                        for line in _decode_input_verbose(tx.get("input")):
                            print(line)
                except Exception as err:
                    print(f"error: {err}")
            ratio = (interesting_hits / total_seen) if total_seen else 0.0
            print(
                "Summary: "
                f"total_seen={total_seen}, "
                f"interesting_hits={interesting_hits}, "
                f"interesting_ratio={ratio:.4f}"
            )
        finally:
            if subscription_id is not None:
                try:
                    await w3.eth.unsubscribe(subscription_id)
                except Exception:
                    pass


def main() -> None:
    try:
        asyncio.run(stream_pending_transactions())
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
