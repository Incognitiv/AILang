from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Interval:
    low: int
    high: int

    def union(self, other: "Interval") -> "Interval":
        return Interval(min(self.low, other.low), max(self.high, other.high))


@dataclass(frozen=True, slots=True)
class StringInfo:
    min_len: int
    max_len: int
    max_digit_run: int

    def union(self, other: "StringInfo") -> "StringInfo":
        return StringInfo(
            min(self.min_len, other.min_len),
            max(self.max_len, other.max_len),
            max(self.max_digit_run, other.max_digit_run),
        )


def string_info_from_literal(value: str) -> StringInfo:
    current_run = 0
    max_run = 0
    for ch in value:
        if "0" <= ch <= "9":
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 0
    length = len(value)
    return StringInfo(length, length, max_run)


def string_info_from_format_call(name: str, low: int, high: int) -> StringInfo | None:
    if name == "str":
        max_len = max(len(str(low)), len(str(high)))
        return StringInfo(1, max_len, max_len)
    base = {"hex": 16, "bin": 2, "oct": 8}.get(name)
    if base is None:
        return None
    if low < 0:
        digits = {16: 16, 2: 64, 8: 22}[base]
    else:
        digits = _unsigned_digits(max(0, high), base)
    return StringInfo(1, 2 + digits, digits)


def _unsigned_digits(value: int, base: int) -> int:
    digits = 1
    while value >= base:
        value //= base
        digits += 1
    return digits
