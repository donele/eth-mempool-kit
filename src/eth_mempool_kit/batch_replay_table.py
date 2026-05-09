import argparse
import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median
import sys


TX_HASH_RE = re.compile(r"TRANSACTION HASH:\s*([0-9a-fA-Fx]+)")
REPLAY_STATUS_RE = re.compile(r"^replay_status=(\d+)$")
SIMULATION_ERROR_RE = re.compile(r"^simulation_error=")
QUOTE_BEFORE_RE = re.compile(r"^quote_before=(.+)$")
QUOTE_DELTA_RE = re.compile(r"^quote_delta=(.+)$")
PATH_RE = re.compile(r"^path=(.+)$")
PATH_LAST_TOKEN_RE = re.compile(r"->\s*([^(]+)\((0x[0-9a-fA-F]+)\)\s*$")


@dataclass
class TxMetrics:
    tx_hash: str | None = None
    success: bool = False
    error: bool = False
    quote_before: int | None = None
    quote_delta: int | None = None
    quote_token_symbol: str | None = None
    quote_token_address: str | None = None


@dataclass
class BatchMetrics:
    tx_count: int
    success_count: int
    error_count: int
    quote_covered: int
    quote_total_success: int
    impacts: list[float]


def _parse_amount(raw: str) -> int | None:
    raw = raw.strip()
    if raw == "unavailable":
        return None
    raw = raw.replace(",", "")
    if raw.startswith("+"):
        raw = raw[1:]
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_path_last_token(path_value: str) -> tuple[str | None, str | None]:
    match = PATH_LAST_TOKEN_RE.search(path_value)
    if not match:
        return None, None
    return match.group(1).strip(), match.group(2).lower()


def _finalize_tx(metrics: TxMetrics, out: list[TxMetrics]) -> None:
    if metrics.success or metrics.error or metrics.quote_before is not None or metrics.quote_delta is not None:
        out.append(metrics)


def parse_batch_log_tx(path: Path) -> list[TxMetrics]:
    tx_items: list[TxMetrics] = []
    current: TxMetrics | None = None

    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            tx_hash_match = TX_HASH_RE.search(line)
            if tx_hash_match:
                if current is not None:
                    _finalize_tx(current, tx_items)
                current = TxMetrics(tx_hash=tx_hash_match.group(1))
                continue
            if current is None:
                continue

            if SIMULATION_ERROR_RE.match(line):
                current.error = True
                continue

            replay_status_match = REPLAY_STATUS_RE.match(line)
            if replay_status_match:
                current.success = replay_status_match.group(1) == "1"
                continue

            quote_before_match = QUOTE_BEFORE_RE.match(line)
            if quote_before_match:
                current.quote_before = _parse_amount(quote_before_match.group(1))
                continue

            quote_delta_match = QUOTE_DELTA_RE.match(line)
            if quote_delta_match:
                current.quote_delta = _parse_amount(quote_delta_match.group(1))
                continue

            path_match = PATH_RE.match(line)
            if path_match:
                symbol, address = _parse_path_last_token(path_match.group(1))
                current.quote_token_symbol = symbol
                current.quote_token_address = address
                continue

    if current is not None:
        _finalize_tx(current, tx_items)

    return tx_items


def _batch_from_tx(tx_items: list[TxMetrics]) -> BatchMetrics:
    success_count = sum(1 for tx in tx_items if tx.success)
    error_count = sum(1 for tx in tx_items if tx.error)

    impacts: list[float] = []
    quote_covered = 0
    for tx in tx_items:
        if not tx.success:
            continue
        if tx.quote_before is None or tx.quote_before <= 0 or tx.quote_delta is None:
            continue
        quote_covered += 1
        impacts.append(abs(tx.quote_delta) / tx.quote_before * 100.0)

    return BatchMetrics(
        tx_count=len(tx_items),
        success_count=success_count,
        error_count=error_count,
        quote_covered=quote_covered,
        quote_total_success=success_count,
        impacts=impacts,
    )


def parse_batch_log(path: Path) -> BatchMetrics:
    return _batch_from_tx(parse_batch_log_tx(path))


def _impact_text(impacts: list[float]) -> str:
    if not impacts:
        return "unavailable"
    med = median(impacts)
    max_v = max(impacts)
    return f"median `{med:.4f}%`, max `{max_v:.2f}%`"


def _estimate_potential(metrics: BatchMetrics) -> str:
    if metrics.success_count == 0 or metrics.quote_covered == 0:
        return "Blocked"
    med = median(metrics.impacts)
    if med < 0.01:
        return "Very low"
    if med < 0.1:
        return "Weak"
    return "Needs review"


def _default_comment(scope: str, path: Path, metrics: BatchMetrics) -> str:
    if metrics.success_count == 0:
        return f"Source: `{path}`. No successful replays, so no profitability signal yet."
    if metrics.quote_covered == 0:
        return f"Source: `{path}`. Replays worked, but quote metrics were unavailable."
    return f"Source: `{path}`. Quote impact is a rough triage signal, not proof of profit."


def _render_row(scope: str, path: Path, metrics: BatchMetrics) -> str:
    quote_coverage = f"`{metrics.quote_covered} / {metrics.quote_total_success}`"
    return (
        f"| {scope} | `{metrics.tx_count}` | `{metrics.success_count}` | `{metrics.error_count}` | "
        f"{quote_coverage} | {_impact_text(metrics.impacts)} | {_estimate_potential(metrics)} | "
        f"{_default_comment(scope, path, metrics)} |"
    )


def _tx_impact(tx: TxMetrics) -> float | None:
    if not tx.success or tx.quote_before is None or tx.quote_before <= 0 or tx.quote_delta is None:
        return None
    return abs(tx.quote_delta) / tx.quote_before * 100.0


def _parse_key_value_pairs(items: list[str], flag_name: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Invalid {flag_name} value `{item}`; expected KEY=VALUE")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise SystemExit(f"Invalid {flag_name} value `{item}`; empty KEY")
        try:
            out[key] = float(value)
        except ValueError as err:
            raise SystemExit(f"Invalid {flag_name} numeric value `{item}`") from err
    return out


def _lookup_token_price_usd(
    tx: TxMetrics,
    token_usd_by_symbol: dict[str, float],
    token_usd_by_address: dict[str, float],
    nominal_eth_usd: float | None,
) -> float | None:
    if tx.quote_token_address and tx.quote_token_address in token_usd_by_address:
        return token_usd_by_address[tx.quote_token_address]
    if tx.quote_token_symbol and tx.quote_token_symbol in token_usd_by_symbol:
        return token_usd_by_symbol[tx.quote_token_symbol]
    if nominal_eth_usd is not None and tx.quote_token_symbol and tx.quote_token_symbol.upper() in {"ETH", "WETH"}:
        return nominal_eth_usd
    return None


def _tx_max_profit_usd(
    tx: TxMetrics,
    nominal_eth_usd: float | None,
    token_usd_by_symbol: dict[str, float],
    token_usd_by_address: dict[str, float],
    token_decimals_by_symbol: dict[str, float],
    token_decimals_by_address: dict[str, float],
) -> float | None:
    if tx.quote_delta is None:
        return None
    unit_price = _lookup_token_price_usd(
        tx, token_usd_by_symbol=token_usd_by_symbol, token_usd_by_address=token_usd_by_address, nominal_eth_usd=nominal_eth_usd
    )
    if unit_price is None:
        return None

    decimals = None
    if tx.quote_token_address and tx.quote_token_address in token_decimals_by_address:
        decimals = token_decimals_by_address[tx.quote_token_address]
    elif tx.quote_token_symbol and tx.quote_token_symbol in token_decimals_by_symbol:
        decimals = token_decimals_by_symbol[tx.quote_token_symbol]
    elif tx.quote_token_symbol and tx.quote_token_symbol.upper() in {"ETH", "WETH"}:
        decimals = 18.0

    if decimals is None:
        return None
    return abs(tx.quote_delta) / (10 ** decimals) * unit_price


def _tx_max_profit_usd_or_zero(
    tx: TxMetrics,
    nominal_eth_usd: float | None,
    token_usd_by_symbol: dict[str, float],
    token_usd_by_address: dict[str, float],
    token_decimals_by_symbol: dict[str, float],
    token_decimals_by_address: dict[str, float],
) -> float:
    impact = _tx_impact(tx)
    if impact is not None and impact > 10.0:
        return 0.0
    value = _tx_max_profit_usd(
        tx,
        nominal_eth_usd=nominal_eth_usd,
        token_usd_by_symbol=token_usd_by_symbol,
        token_usd_by_address=token_usd_by_address,
        token_decimals_by_symbol=token_decimals_by_symbol,
        token_decimals_by_address=token_decimals_by_address,
    )
    return 0.0 if value is None else value


def _short_tx_hash(tx_hash: str | None) -> str:
    if not tx_hash:
        return "unknown"
    return tx_hash[:6]


def _sorted_tx_rows(
    logs: list[Path],
    nominal_eth_usd: float | None,
    token_usd_by_symbol: dict[str, float],
    token_usd_by_address: dict[str, float],
    token_decimals_by_symbol: dict[str, float],
    token_decimals_by_address: dict[str, float],
) -> list[tuple[str, Path, TxMetrics, float | None, float]]:
    rows: list[tuple[str, Path, TxMetrics, float | None, float]] = []
    for path in logs:
        scope = path.name
        for tx in parse_batch_log_tx(path):
            impact = _tx_impact(tx)
            profit_usd = _tx_max_profit_usd_or_zero(
                tx,
                nominal_eth_usd=nominal_eth_usd,
                token_usd_by_symbol=token_usd_by_symbol,
                token_usd_by_address=token_usd_by_address,
                token_decimals_by_symbol=token_decimals_by_symbol,
                token_decimals_by_address=token_decimals_by_address,
            )
            rows.append((scope, path, tx, impact, profit_usd))

    def _sort_key(item: tuple[str, Path, TxMetrics, float | None, float]) -> tuple[float, str, str]:
        scope, _path, tx, _impact, profit_usd = item
        return (profit_usd, scope, tx.tx_hash or "")

    rows.sort(key=_sort_key)
    return rows


def _render_aggregate_markdown(
    logs: list[Path],
    nominal_eth_usd: float | None,
    token_usd_by_symbol: dict[str, float],
    token_usd_by_address: dict[str, float],
    token_decimals_by_symbol: dict[str, float],
    token_decimals_by_address: dict[str, float],
) -> None:
    print("| Transactions | Successful Replays | Errors | Quote Coverage | Observed Quote Impact | Estimated Potential | Comment | Max Theoretical Profit (USD) |")
    print("| ---: | ---: | ---: | ---: | --- | --- | --- | ---: |")
    grand_total_profit = 0.0
    for path in logs:
        metrics = parse_batch_log(path)
        tx_items = parse_batch_log_tx(path)
        total_profit = 0.0
        for tx in tx_items:
            v = _tx_max_profit_usd(
                tx,
                nominal_eth_usd=nominal_eth_usd,
                token_usd_by_symbol=token_usd_by_symbol,
                token_usd_by_address=token_usd_by_address,
                token_decimals_by_symbol=token_decimals_by_symbol,
                token_decimals_by_address=token_decimals_by_address,
            )
            if v is not None:
                total_profit += v
        grand_total_profit += total_profit
        profit_text = f"`{total_profit:.6f}`"
        quote_coverage = f"`{metrics.quote_covered} / {metrics.quote_total_success}`"
        print(
            f"| `{metrics.tx_count}` | `{metrics.success_count}` | `{metrics.error_count}` | "
            f"{quote_coverage} | {_impact_text(metrics.impacts)} | {_estimate_potential(metrics)} | "
            f"{_default_comment('', path, metrics)} | {profit_text} |"
        )
    print(
        f"| `TOTAL` | `` | `` | `` | `` | `` | `Sum of max_theoretical_profit_usd` | `{grand_total_profit:.6f}` |"
    )


def _render_aggregate_csv(
    logs: list[Path],
    nominal_eth_usd: float | None,
    token_usd_by_symbol: dict[str, float],
    token_usd_by_address: dict[str, float],
    token_decimals_by_symbol: dict[str, float],
    token_decimals_by_address: dict[str, float],
) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(
        [
            "transactions",
            "successful_replays",
            "errors",
            "quote_coverage",
            "observed_quote_impact",
            "estimated_potential",
            "comment",
            "max_theoretical_profit_usd",
        ]
    )
    grand_total_profit = 0.0
    for path in logs:
        scope = path.name
        metrics = parse_batch_log(path)
        tx_items = parse_batch_log_tx(path)
        total_profit = 0.0
        for tx in tx_items:
            v = _tx_max_profit_usd(
                tx,
                nominal_eth_usd=nominal_eth_usd,
                token_usd_by_symbol=token_usd_by_symbol,
                token_usd_by_address=token_usd_by_address,
                token_decimals_by_symbol=token_decimals_by_symbol,
                token_decimals_by_address=token_decimals_by_address,
            )
            if v is not None:
                total_profit += v
        grand_total_profit += total_profit
        writer.writerow(
            [
                metrics.tx_count,
                metrics.success_count,
                metrics.error_count,
                f"{metrics.quote_covered} / {metrics.quote_total_success}",
                _impact_text(metrics.impacts).replace("`", ""),
                _estimate_potential(metrics),
                _default_comment("", path, metrics).replace("`", ""),
                f"{total_profit:.6f}",
            ]
        )
    writer.writerow(
        ["TOTAL", "", "", "", "", "", "Sum of max_theoretical_profit_usd", f"{grand_total_profit:.6f}"]
    )


def _render_tx_markdown(
    logs: list[Path],
    nominal_eth_usd: float | None,
    token_usd_by_symbol: dict[str, float],
    token_usd_by_address: dict[str, float],
    token_decimals_by_symbol: dict[str, float],
    token_decimals_by_address: dict[str, float],
) -> None:
    print("| Tx Hash | Replay Status | Error | Quote Token Symbol | Quote Before | Quote Delta | Quote Impact | Max Theoretical Profit (USD) |")
    print("| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |")
    total_profit = 0.0
    for scope, _path, tx, impact, profit in _sorted_tx_rows(
        logs,
        nominal_eth_usd=nominal_eth_usd,
        token_usd_by_symbol=token_usd_by_symbol,
        token_usd_by_address=token_usd_by_address,
        token_decimals_by_symbol=token_decimals_by_symbol,
        token_decimals_by_address=token_decimals_by_address,
    ):
        impact_text = "unavailable" if impact is None else f"`{impact:.4f}%`"
        profit_text = f"`{profit:.6f}`"
        total_profit += profit
        print(
            f"| `{_short_tx_hash(tx.tx_hash)}` | "
            f"`{1 if tx.success else 0}` | `{1 if tx.error else 0}` | "
            f"`{tx.quote_token_symbol or 'unknown'}` | "
            f"`{'unavailable' if tx.quote_before is None else tx.quote_before}` | "
            f"`{'unavailable' if tx.quote_delta is None else tx.quote_delta}` | {impact_text} | "
            f"{profit_text} |"
        )
    print(
        f"| `TOTAL` | `` | `` | `` | `` | `` | `` | `{total_profit:.6f}` |"
    )


def _render_tx_csv(
    logs: list[Path],
    nominal_eth_usd: float | None,
    token_usd_by_symbol: dict[str, float],
    token_usd_by_address: dict[str, float],
    token_decimals_by_symbol: dict[str, float],
    token_decimals_by_address: dict[str, float],
) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(
        [
            "tx_hash",
            "replay_status",
            "error",
            "quote_token_symbol",
            "quote_before",
            "quote_delta",
            "quote_impact_pct",
            "max_theoretical_profit_usd",
        ]
    )
    total_profit = 0.0
    for scope, path, tx, impact, profit in _sorted_tx_rows(
        logs,
        nominal_eth_usd=nominal_eth_usd,
        token_usd_by_symbol=token_usd_by_symbol,
        token_usd_by_address=token_usd_by_address,
        token_decimals_by_symbol=token_decimals_by_symbol,
        token_decimals_by_address=token_decimals_by_address,
    ):
        total_profit += profit
        writer.writerow(
            [
                _short_tx_hash(tx.tx_hash),
                1 if tx.success else 0,
                1 if tx.error else 0,
                tx.quote_token_symbol or "unknown",
                "unavailable" if tx.quote_before is None else tx.quote_before,
                "unavailable" if tx.quote_delta is None else tx.quote_delta,
                "unavailable" if impact is None else f"{impact:.6f}",
                f"{profit:.6f}",
            ]
        )
    writer.writerow(["TOTAL", "", "", "", "", "", "", f"{total_profit:.6f}"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate replay metrics from simulate_v3 batch logs"
    )
    parser.add_argument(
        "logs",
        nargs="+",
        type=Path,
        help="One or more batch replay log files",
    )
    parser.add_argument(
        "-m",
        "--markdown",
        action="store_true",
        help="output markdown table format",
    )
    parser.add_argument(
        "-c",
        "--csv",
        action="store_true",
        help="output csv format",
    )
    parser.add_argument(
        "-t",
        "--transactions",
        action="store_true",
        help="output one row per transaction instead of one row per log",
    )
    parser.add_argument(
        "-a",
        "--aggregate",
        action="store_true",
        help="output one row per log (aggregation mode)",
    )
    parser.add_argument(
        "--nominal-eth-usd",
        type=float,
        help="nominal ETH/USD price used when quote token is ETH or WETH",
    )
    parser.add_argument(
        "--nominal-token-usd",
        action="append",
        default=[],
        metavar="KEY=PRICE",
        help="nominal token USD price by symbol or address (repeatable, e.g. USDC=1 or 0xa0b8...=1)",
    )
    parser.add_argument(
        "--token-decimals",
        action="append",
        default=[],
        metavar="KEY=DECIMALS",
        help="token decimals by symbol or address (repeatable, e.g. USDC=6 or 0xa0b8...=6)",
    )
    args = parser.parse_args()

    if args.markdown and args.csv:
        raise SystemExit("Choose only one output format: --markdown or --csv")
    if args.transactions and args.aggregate:
        raise SystemExit("Choose only one row mode: --transactions or --aggregate")

    # New defaults:
    # - format: CSV
    # - row mode: per transaction
    use_csv = args.csv or not args.markdown
    use_aggregate = args.aggregate
    if args.transactions:
        use_aggregate = False

    nominal_token_usd = _parse_key_value_pairs(args.nominal_token_usd, "--nominal-token-usd")
    token_decimals_raw = _parse_key_value_pairs(args.token_decimals, "--token-decimals")
    token_usd_by_symbol = {k: v for k, v in nominal_token_usd.items() if not k.lower().startswith("0x")}
    token_usd_by_address = {k.lower(): v for k, v in nominal_token_usd.items() if k.lower().startswith("0x")}
    token_decimals_by_symbol = {k: v for k, v in token_decimals_raw.items() if not k.lower().startswith("0x")}
    token_decimals_by_address = {k.lower(): v for k, v in token_decimals_raw.items() if k.lower().startswith("0x")}

    if not use_aggregate:
        if use_csv:
            _render_tx_csv(
                args.logs,
                nominal_eth_usd=args.nominal_eth_usd,
                token_usd_by_symbol=token_usd_by_symbol,
                token_usd_by_address=token_usd_by_address,
                token_decimals_by_symbol=token_decimals_by_symbol,
                token_decimals_by_address=token_decimals_by_address,
            )
        else:
            _render_tx_markdown(
                args.logs,
                nominal_eth_usd=args.nominal_eth_usd,
                token_usd_by_symbol=token_usd_by_symbol,
                token_usd_by_address=token_usd_by_address,
                token_decimals_by_symbol=token_decimals_by_symbol,
                token_decimals_by_address=token_decimals_by_address,
            )
        return

    if use_csv:
        _render_aggregate_csv(
            args.logs,
            nominal_eth_usd=args.nominal_eth_usd,
            token_usd_by_symbol=token_usd_by_symbol,
            token_usd_by_address=token_usd_by_address,
            token_decimals_by_symbol=token_decimals_by_symbol,
            token_decimals_by_address=token_decimals_by_address,
        )
    else:
        _render_aggregate_markdown(
            args.logs,
            nominal_eth_usd=args.nominal_eth_usd,
            token_usd_by_symbol=token_usd_by_symbol,
            token_usd_by_address=token_usd_by_address,
            token_decimals_by_symbol=token_decimals_by_symbol,
            token_decimals_by_address=token_decimals_by_address,
        )


if __name__ == "__main__":
    main()
