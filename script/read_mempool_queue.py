import asyncio
import os
import random
import time

from dotenv import load_dotenv
from web3 import AsyncWeb3, WebSocketProvider


load_dotenv(override=True)
WSS_URL = os.getenv("WSS_URL")
RUN_SECONDS = 180
MAX_TX_LOOKUPS_PER_SEC = float(os.getenv("MAX_TX_LOOKUPS_PER_SEC", "48"))
LOOKUP_INTERVAL_SECONDS = (
    1.0 / MAX_TX_LOOKUPS_PER_SEC if MAX_TX_LOOKUPS_PER_SEC > 0 else 0.0
)
RATE_LIMIT_RETRIES = int(os.getenv("RATE_LIMIT_RETRIES", "5"))
RATE_LIMIT_BASE_BACKOFF_SECONDS = float(
    os.getenv("RATE_LIMIT_BASE_BACKOFF_SECONDS", "0.5")
)
QUEUE_MAXSIZE = int(os.getenv("TX_QUEUE_MAXSIZE", "1000"))
NUM_CONSUMERS = int(os.getenv("TX_CONSUMER_COUNT", "4"))


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


class SharedRateLimiter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.next_allowed_at = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self.interval_seconds <= 0:
            return

        async with self.lock:
            now = time.monotonic()
            if now < self.next_allowed_at:
                await asyncio.sleep(self.next_allowed_at - now)
                now = time.monotonic()
            self.next_allowed_at = now + self.interval_seconds


class Stats:
    def __init__(self) -> None:
        self.started_at = time.monotonic()
        self.total_seen = 0
        self.total_lookups = 0
        self.dropped_queue_full = 0
        self.rate_limit_errors = 0
        self.lock = asyncio.Lock()

    async def incr(self, field: str, amount: int = 1) -> None:
        async with self.lock:
            setattr(self, field, getattr(self, field) + amount)

    async def snapshot(self) -> dict[str, int]:
        async with self.lock:
            return {
                "started_at": self.started_at,
                "total_seen": self.total_seen,
                "total_lookups": self.total_lookups,
                "dropped_queue_full": self.dropped_queue_full,
                "rate_limit_errors": self.rate_limit_errors,
            }


async def _produce_tx_hashes(
    w3: AsyncWeb3,
    tx_queue: asyncio.Queue,
    stats: Stats,
) -> str:
    subscription_iter = w3.socket.process_subscriptions()
    started_at = time.monotonic()

    while True:
        elapsed = time.monotonic() - started_at
        remaining = RUN_SECONDS - elapsed
        if remaining <= 0:
            print("Reached runtime limit (180s). Exiting.")
            return "runtime_limit"

        try:
            message = await asyncio.wait_for(anext(subscription_iter), timeout=remaining)
        except asyncio.TimeoutError:
            print("Reached runtime limit (180s). Exiting.")
            return "runtime_limit"

        tx_hash = message.get("result")
        if not tx_hash:
            continue

        await stats.incr("total_seen")

        try:
            tx_queue.put_nowait(tx_hash)
        except asyncio.QueueFull:
            await stats.incr("dropped_queue_full")


async def _consume_tx_hashes(
    w3: AsyncWeb3,
    tx_queue: asyncio.Queue,
    stats: Stats,
    rate_limiter: SharedRateLimiter,
) -> None:
    while True:
        tx_hash = await tx_queue.get()
        try:
            if tx_hash is None:
                return

            printable_hash = (
                tx_hash.hex() if isinstance(tx_hash, (bytes, bytearray)) else tx_hash
            )
            print(f"TRANSACTION HASH: {printable_hash}")

            try:
                await rate_limiter.acquire()
                tx = await _get_transaction_with_retry(w3, tx_hash)
                await stats.incr("total_lookups")
                snapshot = await stats.snapshot()
                elapsed = max(time.monotonic() - snapshot["started_at"], 1e-9)
                avg_lookups_per_sec = snapshot["total_lookups"] / elapsed
                print(
                    f"queue_size={tx_queue.qsize()} "
                    f"avg_lookups_per_sec={avg_lookups_per_sec:.2f}"
                )

                # Instead of sending (public key, signature), Etherium sends (r, s, v)
                # Then nodes reconstruct public key and deriveaddress
                # tx.r = x-coordinate of a curve point
                # tx.s = proof component tying message + private key
                # tx.from is recovered and validated by the node from (r, s, v)

                print(tx)
            except Exception as err:
                if _is_rate_limit_error(err):
                    await stats.incr("rate_limit_errors")
                print(f"error: {err}")
        finally:
            tx_queue.task_done()


async def stream_pending_transactions() -> None:
    if not WSS_URL:
        raise ValueError("Missing WSS_URL in .env")
    if not WSS_URL.startswith("wss://"):
        raise ValueError("WSS_URL must be a websocket URL starting with wss://")

    subscription_id = None
    tx_queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    stats = Stats()
    rate_limiter = SharedRateLimiter(LOOKUP_INTERVAL_SECONDS)

    async with AsyncWeb3(WebSocketProvider(WSS_URL)) as w3:
        connected = await w3.is_connected(show_traceback=True)
        print(connected)
        if not connected:
            return

        subscription_id = await w3.eth.subscribe("newPendingTransactions")
        print(f"Subscribed to pending transactions: {subscription_id}")

        consumers = [
            asyncio.create_task(_consume_tx_hashes(w3, tx_queue, stats, rate_limiter))
            for _ in range(NUM_CONSUMERS)
        ]

        try:
            await _produce_tx_hashes(w3, tx_queue, stats)
            await tx_queue.join()
        finally:
            for _ in range(NUM_CONSUMERS):
                await tx_queue.put(None)
            await asyncio.gather(*consumers, return_exceptions=True)

            snapshot = await stats.snapshot()
            print(
                "Summary: "
                f"total_seen={snapshot['total_seen']}, "
                f"total_lookups={snapshot['total_lookups']}, "
                f"dropped_queue_full={snapshot['dropped_queue_full']}, "
                f"rate_limit_errors={snapshot['rate_limit_errors']}"
            )

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
