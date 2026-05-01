import asyncio
import os
import random
import time
from pathlib import Path

from dotenv import load_dotenv
from eth_abi import decode
from web3 import AsyncWeb3, WebSocketProvider
import yaml


load_dotenv(override=True)
WSS_URL = os.getenv("WSS_URL")
RUN_SECONDS = 180
MAX_TX_LOOKUPS_PER_SEC = float(os.getenv("MAX_TX_LOOKUPS_PER_SEC", "8"))
LOOKUP_INTERVAL_SECONDS = (
    1.0 / MAX_TX_LOOKUPS_PER_SEC if MAX_TX_LOOKUPS_PER_SEC > 0 else 0.0
)
RATE_LIMIT_RETRIES = int(os.getenv("RATE_LIMIT_RETRIES", "5"))
RATE_LIMIT_BASE_BACKOFF_SECONDS = float(
    os.getenv("RATE_LIMIT_BASE_BACKOFF_SECONDS", "0.5")
)
CONFIG_PATH = Path(__file__).with_name("decode_config.yaml")


def _load_decode_config(config_path: Path) -> tuple[dict, dict, dict]:
    with config_path.open() as f:
        config = yaml.safe_load(f) or {}

    router_labels = config.get("router_labels")
    selector_labels = config.get("selector_labels")
    selector_decoders_raw = config.get("selector_decoders")
    if not isinstance(router_labels, dict):
        raise ValueError("decode_config.yaml missing mapping: router_labels")
    if not isinstance(selector_labels, dict):
        raise ValueError("decode_config.yaml missing mapping: selector_labels")
    if not isinstance(selector_decoders_raw, dict):
        raise ValueError("decode_config.yaml missing mapping: selector_decoders")

    selector_decoders = {}
    for selector, decoder_config in selector_decoders_raw.items():
        if not isinstance(decoder_config, dict):
            raise ValueError(f"selector_decoders[{selector}] must be a mapping")
        method_name = decoder_config.get("method_name")
        arg_types = decoder_config.get("arg_types")
        arg_names = decoder_config.get("arg_names")
        if not isinstance(method_name, str):
            raise ValueError(f"selector_decoders[{selector}].method_name must be a string")
        if not isinstance(arg_types, list) or not all(
            isinstance(item, str) for item in arg_types
        ):
            raise ValueError(f"selector_decoders[{selector}].arg_types must be a list[str]")
        if not isinstance(arg_names, list) or not all(
            isinstance(item, str) for item in arg_names
        ):
            raise ValueError(f"selector_decoders[{selector}].arg_names must be a list[str]")
        selector_decoders[selector] = (method_name, arg_types, arg_names)

    return router_labels, selector_labels, selector_decoders


ROUTER_LABELS, SELECTOR_LABELS, SELECTOR_DECODERS = _load_decode_config(CONFIG_PATH)
KNOWN_ROUTER_ADDRESSES = set(ROUTER_LABELS)


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


def _is_rate_limit_error(err: Exception) -> bool:
    err_text = str(err).lower()
    return (
        "429" in err_text
        or "compute units per second" in err_text
        or "throughput" in err_text
        or "rate limit" in err_text
        or "too many requests" in err_text
    )


async def _get_transaction_with_retry(w3: AsyncWeb3, tx_hash):
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return await w3.eth.get_transaction(tx_hash)
        except Exception as err:
            if not _is_rate_limit_error(err) or attempt >= RATE_LIMIT_RETRIES:
                raise

            backoff = RATE_LIMIT_BASE_BACKOFF_SECONDS * (2**attempt)
            jitter = random.uniform(0.0, 0.25)
            delay = backoff + jitter
            print(
                "rate_limited: "
                f"attempt={attempt + 1}/{RATE_LIMIT_RETRIES + 1}, "
                f"sleeping={delay:.2f}s, "
                f"error={err}"
            )
            await asyncio.sleep(delay)


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
            total_lookups = 0
            skipped_due_to_sampling = 0
            rate_limit_errors = 0
            interesting_hits = 0
            next_lookup_at = time.monotonic()
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

                now = time.monotonic()
                if LOOKUP_INTERVAL_SECONDS > 0 and now < next_lookup_at:
                    skipped_due_to_sampling += 1
                    continue

                try:
                    # Sample pending hashes at a controlled rate to stay under provider CU/s limits.
                    tx = await _get_transaction_with_retry(w3, tx_hash)
                    total_lookups += 1
                    next_lookup_at = time.monotonic() + LOOKUP_INTERVAL_SECONDS

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
                    if _is_rate_limit_error(err):
                        rate_limit_errors += 1
                    print(f"error: {err}")
            ratio = (interesting_hits / total_seen) if total_seen else 0.0
            print(
                "Summary: "
                f"total_seen={total_seen}, "
                f"total_lookups={total_lookups}, "
                f"skipped_due_to_sampling={skipped_due_to_sampling}, "
                f"rate_limit_errors={rate_limit_errors}, "
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
