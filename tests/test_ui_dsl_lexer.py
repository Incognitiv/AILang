from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "source"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from lexer.scan import tokenize  # noqa: E402
from parser.parser import Parser  # noqa: E402


def _types_and_text(source: str) -> list[tuple[str, str]]:
    return [(token_type, text) for token_type, text, _line, _col in tokenize(source)]


def test_ui_dsl_arrow_and_color_literals_are_real_tokens() -> None:
    tokens = _types_and_text(
        """
window demo:
    width -> 520 px
    background -> #fafafa
    color -> #ff5f56
end
"""
    )

    assert ("UI_ARROW", "->") in tokens
    assert ("UI_COLOR", "#fafafa") in tokens
    assert ("UI_COLOR", "#ff5f56") in tokens
    assert ("HASH_COMMENT", "#fafafa") not in tokens


def test_hash_comments_still_win_outside_ui_arrow_context() -> None:
    tokens = _types_and_text(
        """
#fafafa is still a comment when it is not a UI property value
def main():
    return 0
end
"""
    )

    assert ("UI_COLOR", "#fafafa") not in tokens
    assert ("DEF", "def") in tokens


def test_ui_include_is_contextual() -> None:
    include_tokens = _types_and_text('include "config.ail"\n')
    identifier_tokens = _types_and_text("int include = 1\n")

    assert include_tokens[0] == ("UI_INCLUDE", "include")
    assert identifier_tokens[1] == ("IDENT", "include")


def test_canonical_ui_dsl_corpus_tokenizes_and_parses() -> None:
    corpus = REPO_ROOT / "archived" / "source-cruft" / "Desktop Experiment"
    files = sorted(corpus.glob("*.ail"))
    if not files:
        pytest.skip("optional archived UI DSL corpus is not included")

    for path in files:
        tokens = tokenize(path.read_text(encoding="utf-8"))
        Parser(tokens).parse_program()
