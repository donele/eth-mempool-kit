import argparse
import re
from pathlib import Path

from decode_mempool import _decode_input, _decode_input_verbose


TX_HASH_PREFIX = "TRANSACTION HASH:"
QUEUE_PREFIX = "queue_size="
ROUTER_PREFIX = "router="
INPUT_PATTERN = re.compile(r"'input': HexBytes\('([^']+)'\)")


def _print_decoded_record(
    tx_hash_line: str | None,
    queue_line: str | None,
    router_line: str | None,
    tx_line: str,
) -> None:
    match = INPUT_PATTERN.search(tx_line)
    if match is None:
        return

    tx_input = match.group(1)
    if tx_hash_line:
        print(tx_hash_line)
    if queue_line:
        print(queue_line)
    if router_line:
        print(router_line)
    print(_decode_input(tx_input))
    for line in _decode_input_verbose(tx_input, max_words=None):
        print(line)
    print()


def decode_transactions(log_path: Path) -> None:
    tx_hash_line = None
    queue_line = None
    router_line = None

    with log_path.open() as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if TX_HASH_PREFIX in line:
                tx_hash_line = line
                queue_line = None
                router_line = None
                continue
            if line.startswith(QUEUE_PREFIX):
                queue_line = line
                continue
            if line.startswith(ROUTER_PREFIX):
                router_line = line
                continue
            if "AttributeDict(" not in line:
                continue

            _print_decoded_record(tx_hash_line, queue_line, router_line, line)
            tx_hash_line = None
            queue_line = None
            router_line = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("filename", help="log file containing captured transactions")
    args = parser.parse_args()

    decode_transactions(Path(args.filename))


if __name__ == "__main__":
    main()
