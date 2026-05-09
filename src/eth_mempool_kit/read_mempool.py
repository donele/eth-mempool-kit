import asyncio
import os
import random
import time

from dotenv import find_dotenv, load_dotenv
from web3 import AsyncWeb3, WebSocketProvider

from .decode_lib import _decode_input

load_dotenv(find_dotenv(usecwd=True), override=True)
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

                printable_hash = (
                    tx_hash.hex()
                    if isinstance(tx_hash, (bytes, bytearray))
                    else tx_hash
                )
                print("TRANSACTION HASH:", printable_hash)
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

                    print(tx)
                    print(_decode_input(tx.get("input")))
                except Exception as err:
                    if _is_rate_limit_error(err):
                        rate_limit_errors += 1
                    print(f"error: {err}")
            print(
                "Summary: "
                f"total_seen={total_seen}, "
                f"total_lookups={total_lookups}, "
                f"skipped_due_to_sampling={skipped_due_to_sampling}, "
                f"rate_limit_errors={rate_limit_errors}"
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
