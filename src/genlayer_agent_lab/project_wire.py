"""Lossless JSON transport for v2 projects; internal values remain Python ints.

JSON numbers outside JavaScript's safe integer range are tagged decimal strings.
Literal objects that resemble tags are escaped, preserving arbitrary JSON data.
Decoding never guesses that an ordinary numeric-looking string is an integer.
"""

import math
import re

INTEGER_ENCODING = "lab-tagged-decimal-v1"
INTEGER_TAG = "$lab_integer"
OBJECT_TAG = "$lab_object"
SAFE_INTEGER = 2**53 - 1
MAX_INTEGER_DIGITS = 256
MAX_WIRE_DEPTH = 48
MAX_WIRE_NODES = 100000
_DECIMAL = re.compile(r"(?:0|-[1-9][0-9]*|[1-9][0-9]*)\Z")


def decimal_integer(value: str) -> int:
    """A canonical bounded decimal representation, never exponent or hex syntax."""
    if (type(value) is not str or len(value.lstrip("-")) > MAX_INTEGER_DIGITS
            or not _DECIMAL.fullmatch(value)):
        raise ValueError("An exact integer requires a canonical bounded decimal string")
    return int(value)


def _transform(value, *, decode):
    remaining = MAX_WIRE_NODES

    def visit(item, depth, ancestors):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > MAX_WIRE_DEPTH:
            raise ValueError("Project wire data exceeds its structural limit")
        if type(item) in (dict, list):
            if id(item) in ancestors:
                raise ValueError("Project wire data contains a cycle")
            ancestors = ancestors | {id(item)}
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("Project wire object keys must be strings")
            keys = set(item)
            if decode and keys == {INTEGER_TAG}:
                return decimal_integer(item[INTEGER_TAG])
            if decode and keys == {OBJECT_TAG}:
                original = item[OBJECT_TAG]
                if type(original) is not dict or set(original) not in ({INTEGER_TAG}, {OBJECT_TAG}):
                    raise ValueError("Malformed escaped project wire object")
                return {key: visit(child, depth + 2, ancestors | {id(original)})
                        for key, child in original.items()}
            result = {key: visit(child, depth + 1, ancestors) for key, child in item.items()}
            if not decode and keys in ({INTEGER_TAG}, {OBJECT_TAG}):
                return {OBJECT_TAG: result}
            return result
        if type(item) is list:
            return [visit(child, depth + 1, ancestors) for child in item]
        if type(item) is int:
            encoded = str(item)
            if len(encoded.lstrip("-")) > MAX_INTEGER_DIGITS:
                raise ValueError("Project integer exceeds its transport digit limit")
            if not decode and abs(item) > SAFE_INTEGER:
                return {INTEGER_TAG: encoded}
            return item
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Project wire numbers must be finite")
            return item
        if type(item) in (str, bool, type(None)):
            return item
        raise ValueError("Project wire values must be JSON-compatible")

    return visit(value, 0, set())


def encode_project_wire(value):
    """Return detached JSON-safe data with exact tagged large integers."""
    return _transform(value, decode=False)


def decode_project_wire(value):
    """Decode explicit wire tags only, preserving ordinary domain strings."""
    return _transform(value, decode=True)
