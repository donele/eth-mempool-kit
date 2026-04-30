import asyncio
import os
import time

from dotenv import load_dotenv
from web3 import AsyncWeb3, WebSocketProvider


load_dotenv(override=True)
WSS_URL = os.getenv("WSS_URL")
RUN_SECONDS = 180
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

                printable_hash = (
                    tx_hash.hex()
                    if isinstance(tx_hash, (bytes, bytearray))
                    else tx_hash
                )
                print("TRANSACTION HASH:", printable_hash)
                try:
                    # get_transaction() takes 5 - 50 ms
                    tx = await w3.eth.get_transaction(tx_hash)

                    # Instead of sending (public key, signature), Etherium sends (r, s, v)
                    # Then nodes reconstruct public key and deriveaddress
                    # tx.r = x-coordinate of a curve point
                    # tx.s = proof component tying message + private key
                    # tx.from is recovered and validated by the node from (r, s, v)

                    print(tx)
                except Exception as err:
                    print(f"error: {err}")
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
