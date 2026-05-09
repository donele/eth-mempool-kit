import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from eth_abi import decode
import yaml


load_dotenv(find_dotenv(usecwd=True), override=True)
CONFIG_PATH = Path(__file__).with_name("decode_config.yaml")


def _load_decode_config(config_path: Path) -> tuple[dict, dict, dict]:
    with config_path.open("r", encoding="utf-8") as f:
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
        if not isinstance(arg_types, list) or not all(isinstance(item, str) for item in arg_types):
            raise ValueError(f"selector_decoders[{selector}].arg_types must be a list[str]")
        if not isinstance(arg_names, list) or not all(isinstance(item, str) for item in arg_names):
            raise ValueError(f"selector_decoders[{selector}].arg_names must be a list[str]")
        selector_decoders[selector] = (method_name, arg_types, arg_names)

    return router_labels, selector_labels, selector_decoders


ROUTER_LABELS, SELECTOR_LABELS, SELECTOR_DECODERS = _load_decode_config(CONFIG_PATH)
KNOWN_ROUTER_ADDRESSES = set(ROUTER_LABELS)


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


def _normalize_input_hex(tx_input) -> str | None:
    if tx_input is None:
        return None
    input_hex = tx_input.hex() if hasattr(tx_input, "hex") else str(tx_input)
    if not isinstance(input_hex, str):
        return None
    if not input_hex.startswith("0x"):
        input_hex = f"0x{input_hex}"
    if len(input_hex) < 10:
        return None
    return input_hex


def _stringify_decoded_value(value):
    if isinstance(value, (list, tuple)):
        return [_stringify_decoded_value(v) for v in value]
    if isinstance(value, bytes):
        return f"0x{value.hex()}"
    return value


def _decode_input_structured(tx_input) -> dict | None:
    input_hex = _normalize_input_hex(tx_input)
    if input_hex is None:
        return None

    selector = input_hex[:10].lower()
    label = SELECTOR_LABELS.get(selector, "unknown_selector")
    payload = input_hex[10:]
    result = {
        "input_hex": input_hex,
        "selector": selector,
        "selector_label": label,
        "payload": payload,
        "words": len(payload) // 64,
        "method_name": None,
        "args": None,
    }

    decoder = SELECTOR_DECODERS.get(selector)
    if decoder is None:
        return result

    method_name, arg_types, arg_names = decoder
    try:
        raw = bytes.fromhex(payload)
        values = decode(arg_types, raw)
    except Exception as err:
        result["decode_error"] = str(err)
        return result

    result["method_name"] = method_name
    structured_args = {}
    for arg_name, value in zip(arg_names, values):
        structured_args[arg_name] = _stringify_decoded_value(value)
    result["args"] = structured_args
    return result
