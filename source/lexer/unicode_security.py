"""Unicode security checks for the lexer."""

from __future__ import annotations

DANGEROUS_UNICODE = {
    "\u202a": "LRE (Left-to-Right Embedding)",
    "\u202b": "RLE (Right-to-Left Embedding)",
    "\u202c": "PDF (Pop Directional Formatting)",
    "\u202d": "LRO (Left-to-Right Override)",
    "\u202e": "RLO (Right-to-Left Override)",
    "\u2066": "LRI (Left-to-Right Isolate)",
    "\u2067": "RLI (Right-to-Left Isolate)",
    "\u2068": "FSI (First Strong Isolate)",
    "\u2069": "PDI (Pop Directional Isolate)",
    "\u200f": "RLM (Right-to-Left Mark)",
    "\u200e": "LRM (Left-to-Right Mark)",
    "\u200b": "ZWSP (Zero Width Space)",
    "\u200c": "ZWNJ (Zero Width Non-Joiner)",
    "\u200d": "ZWJ (Zero Width Joiner)",
    "\ufeff": "BOM (Byte Order Mark)",
}


def check_unicode_security(code: str) -> None:
    """Reject Trojan Source bidirectional controls and hidden zero-width chars."""
    for i, char in enumerate(code):
        if char not in DANGEROUS_UNICODE:
            continue
        line = code[:i].count("\n") + 1
        char_name = DANGEROUS_UNICODE[char]
        raise SyntaxError(
            f"Line {line}: Dangerous Unicode character detected: {char_name} "
            f"(U+{ord(char):04X}). This character can be used in Trojan Source "
            "attacks to hide malicious code. Remove it or use ASCII equivalents."
        )
