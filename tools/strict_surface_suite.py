#!/usr/bin/env python3
"""Strict per-token/per-builtin smoke suite for AILang surface coverage."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATE_HUMAN_FMT = "%d.%m.%Y %H:%M:%S"
DATE_ISO_FMT = "%Y-%m-%dT%H:%M:%S"
DEFAULT_JSON = REPO_ROOT / "benchmarks" / "results" / "strict_surface_suite.json"
DEFAULT_MD = REPO_ROOT / "benchmarks" / "results" / "strict_surface_suite.md"


@dataclass
class TokenProbe:
    token_type: str
    probe: str
    passed: bool
    note: str


@dataclass
class BuiltinProbe:
    builtin: str
    status: str
    selected_call: str | None
    check_ms: float | None
    exit_code: int | None
    detail: str


def _source_root() -> str:
    return str(REPO_ROOT / "source")


def _ensure_source_import_path() -> None:
    root = _source_root()
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_catalogs() -> tuple[list[tuple[str, str]], set[str], set[str], set[str]]:
    _ensure_source_import_path()
    from diagnostics.diagnostics_catalog import CALLABLE_BUILTINS, LANGUAGE_SURFACE
    from lexer.scan import CONTEXTUAL_KEYWORDS, TOKEN_PATTERNS

    return (
        list(TOKEN_PATTERNS),
        {str(name) for name in CALLABLE_BUILTINS},
        {str(name) for name in LANGUAGE_SURFACE},
        {str(name) for name in CONTEXTUAL_KEYWORDS},
    )


def _extract_word_probe(pattern: str) -> str | None:
    m = re.fullmatch(r"\\b([A-Za-z_][A-Za-z0-9_]*)\\b", pattern)
    if m is not None:
        return m.group(1).lower()
    if pattern.startswith("@"):
        return pattern
    return None


def _symbol_probe_map() -> dict[str, str]:
    return {
        r"\+": "+",
        r"\+\+": "++",
        r"\+=": "+=",
        r"-": "-",
        r"--": "--",
        r"-=": "-=",
        r"\*": "*",
        r"\*=": "*=",
        r"/": "/",
        r"/=": "/=",
        r"%": "%",
        r"%=": "%=",
        r"\^": "^",
        r"&": "&",
        r"\|": "|",
        r"~": "~",
        r"\?": "?",
        r":": ":",
        r";": ";",
        r",": ",",
        r"\.": ".",
        r"\.\?": ".?",
        r"\(": "(",
        r"\)": ")",
        r"\[": "[",
        r"\]": "]",
        r"\{": "{",
        r"\}": "}",
        r"<<": "<<",
        r">>": ">>",
        r"==": "==",
        r"!=": "!=",
        r"<=": "<=",
        r">=": ">=",
        r"<": "<",
        r">": ">",
        r"=": "=",
        r":=": ":=",
        r"->": "->",
        r"=>": "=>",
        r"\.\.": "..",
        r"\.\.\.": "...",
        r"#template\b": "#template",
        r"#end\b": "#end",
        r"#cinclude\b": "#cinclude",
        r"#cimport\b": "#cimport",
        r"#link\b": "#link",
        r"\*\*": "**",
        r"\?\.": "?.",
    }


def _probe_for_token(token_type: str, pattern: str) -> str | None:
    if token_type == "FLOAT":
        return "3.14"
    if token_type == "NUMBER":
        return "42"
    if token_type == "STRING":
        return "string"
    if token_type == "STRLIT":
        return '"abc"'
    if token_type == "CHARLIT":
        return "'x'"
    if token_type == "INTERP_STRLIT":
        return '"v=#{1}"'
    if token_type == "HEREDOC":
        return '"""hi"""'
    if token_type == "INLINE_ASM":
        return 'asm "nop" end'
    if token_type in {"TEMPLATE_START", "TEMPLATE_END"}:
        return "#template\n#end"
    if token_type == "COMMENT":
        return "// x"
    if token_type == "COMMENT_BLOCK":
        return "/* x */"
    if token_type == "HASH_COMMENT":
        return "# x"
    if token_type == "IS_NOT":
        return "is not"
    if token_type == "NEWLINE":
        return "\n"
    if token_type == "IDENT":
        return "x"
    if token_type == "UI_INCLUDE":
        return 'include "layout.ail"'
    if token_type == "GATE_AND":
        return "band"
    if token_type == "GATE_OR":
        return "bor"
    if token_type == "GATE_XOR":
        return "bxor"
    if token_type == "GATE_NOT":
        return "bnot"
    word = _extract_word_probe(pattern)
    if word is not None:
        return word
    return _symbol_probe_map().get(pattern)


def _run_token_suite() -> list[TokenProbe]:
    _ensure_source_import_path()
    from lexer.scan import CONTEXTUAL_KEYWORDS, tokenize

    token_patterns, _callable, _surface, _contextual = _load_catalogs()
    probes: list[TokenProbe] = []
    acceptable_aliases: dict[str, set[str]] = {
        "TEMPLATE_START": {"TEMPLATE_BLOCK"},
        "TEMPLATE_END": {"TEMPLATE_BLOCK"},
        "CINCLUDE": {"CINCLUDE_LINE"},
        "CIMPORT": {"CIMPORT_LINE"},
        "LINK_DIR": {"LINK_LINE"},
    }
    for token_type, pattern in token_patterns:
        if token_type in {"SKIP", "MISMATCH"}:
            probes.append(
                TokenProbe(
                    token_type=token_type,
                    probe="",
                    passed=True,
                    note="skipped non-language lexer class",
                )
            )
            continue
        probe = _probe_for_token(token_type, pattern)
        if probe is None:
            probes.append(
                TokenProbe(
                    token_type=token_type,
                    probe="",
                    passed=False,
                    note=f"no probe mapping for pattern {pattern}",
                )
            )
            continue
        try:
            effective_probe = probe
            if token_type in CONTEXTUAL_KEYWORDS:
                effective_probe = f"{probe}("
            tokens = tokenize(effective_probe)
            non_emitting = {"COMMENT", "COMMENT_BLOCK", "HASH_COMMENT", "NEWLINE"}
            if token_type in non_emitting:
                probes.append(
                    TokenProbe(
                        token_type=token_type,
                        probe=probe,
                        passed=True,
                        note="filtered from token stream by lexer",
                    )
                )
                continue
            seen_types = set()
            for token in tokens:
                token_type_seen, *_rest = token
                seen_types.add(token_type_seen)
            expected = {token_type} | acceptable_aliases.get(token_type, set())
            passed = bool(expected & seen_types)
            note = "ok" if passed else f"got {sorted(seen_types)}"
            probes.append(
                TokenProbe(token_type=token_type, probe=probe, passed=passed, note=note)
            )
        except (ValueError, RuntimeError, OSError) as exc:
            probes.append(
                TokenProbe(
                    token_type=token_type,
                    probe=probe,
                    passed=False,
                    note=f"tokenize error: {exc}",
                )
            )
    return probes


def _run(cmd: list[str], timeout_s: int = 45) -> tuple[int, str, str, float]:
    start = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return proc.returncode, proc.stdout, proc.stderr, elapsed_ms


def _check_program(code: str, name: str) -> tuple[int, str, str, float]:
    tmp_dir = Path(tempfile.gettempdir()) / "ailang_strict_surface"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    src = tmp_dir / f"{name}_{time.time_ns()}.ail"
    src.write_text(code, encoding="utf-8")
    return _run([sys.executable, str(REPO_ROOT / "ailang.py"), str(src), "--check"])


def _base_main_prefix() -> list[str]:
    return [
        "def main(): int",
    ]


def _declaration_lines_for_call(call: str) -> list[str]:
    names = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", call))
    lines: list[str] = []
    if "i" in names:
        lines.append("    i = 1")
    if "j" in names:
        lines.append("    j = 2")
    if "s" in names:
        lines.append('    s = "x"')
    if "path" in names:
        lines.append('    path = "benchmarks/out/strict_surface_tmp.txt"')
    if "d" in names:
        lines.append('    d = {"a": 1, "b": 2}')
    if "arr" in names:
        lines.append("    arr = array_new(4)")
        lines.append("    arr = array_push(arr, 1)")
    if "sa" in names:
        lines.append("    sa = str_array_new(4)")
        lines.append('    sa = str_array_push(sa, "x")')
    if "p" in names:
        lines.append("    p = alloc(64)")
    if "mtx" in names:
        lines.append("    mtx = mutex_create()")
    if "cv" in names:
        lines.append("    cv = cond_create()")
    if "rw" in names:
        lines.append("    rw = rwlock_create()")
    if "ch" in names:
        lines.append("    ch = channel(2)")
    return lines


def _candidate_calls(name: str) -> list[str]:
    specific: dict[str, list[str]] = {
        "print": ['print("x")'],
        "puts": ['puts("x")'],
        "putc": ["putc(65)"],
        "read_file": ["read_file(path)"],
        "write_file": ['write_file(path, "x")'],
        "file_size": ["file_size(path)"],
        "len": ["len(s)", "len(arr)", "len(sa)"],
        "strlen": ["strlen(s)"],
        "substr": ["substr(s, 0, 1)"],
        "char_at": ["char_at(s, 0)"],
        "ord": ["ord(65)"],
        "chr": ["chr(65)"],
        "parse_int": ['parse_int("123")'],
        "concat": ["concat(s, s)"],
        "index_of": ["index_of(s, s)"],
        "startswith": ["startswith(s, s)"],
        "endswith": ["endswith(s, s)"],
        "str_replace": ["str_replace(s, s, s)"],
        "array_new": ["array_new(4)"],
        "array_push": ["array_push(arr, i)"],
        "array_get": ["array_get(arr, 0)"],
        "array_len": ["array_len(arr)"],
        "str_array_new": ["str_array_new(4)"],
        "str_array_push": ['str_array_push(sa, "x")'],
        "str_array_get": ["str_array_get(sa, 0)"],
        "str_array_len": ["str_array_len(sa)"],
        "str_array_join": ['str_array_join(sa, ",")'],
        "alloc": ["alloc(64)"],
        "dealloc": ["dealloc(p)"],
        "realloc": ["realloc(p, 128)"],
        "calloc": ["calloc(2, 32)"],
        "peek64": ["peek64(p, 0)"],
        "poke64": ["poke64(p, 0, 1)"],
        "peek32": ["peek32(p, 0)"],
        "poke32": ["poke32(p, 0, 1)"],
        "peek8": ["peek8(p, 0)"],
        "poke8": ["poke8(p, 0, 1)"],
        "ptr_add": ["ptr_add(p, 1)"],
        "ptr_sub": ["ptr_sub(p, 1)"],
        "memset": ["memset(p, 0, 16)"],
        "memmove": ["memmove(p, p, 16)"],
        "memcpy": ["memcpy(p, p, 16)"],
        "mutex_create": ["mutex_create()"],
        "mutex_lock": ["mutex_lock(mtx)"],
        "mutex_unlock": ["mutex_unlock(mtx)"],
        "mutex_destroy": ["mutex_destroy(mtx)"],
        "cond_create": ["cond_create()"],
        "cond_signal": ["cond_signal(cv)"],
        "cond_broadcast": ["cond_broadcast(cv)"],
        "cond_destroy": ["cond_destroy(cv)"],
        "rwlock_create": ["rwlock_create()"],
        "rwlock_read_lock": ["rwlock_read_lock(rw)"],
        "rwlock_write_lock": ["rwlock_write_lock(rw)"],
        "rwlock_read_unlock": ["rwlock_read_unlock(rw)"],
        "rwlock_write_unlock": ["rwlock_write_unlock(rw)"],
        "rwlock_destroy": ["rwlock_destroy(rw)"],
        "channel": ["channel(2)"],
        "chan_send": ["chan_send(ch, i)"],
        "chan_try_send": ["chan_try_send(ch, i)"],
        "chan_try_recv": ["chan_try_recv(ch)"],
        "chan_close": ["chan_close(ch)"],
        "sql_open": ['sql_open("test.db")'],
        "sql_exec": ['sql_exec(0, "select 1")'],
        "sql_close": ["sql_close(0)"],
        "time_ms": ["time_ms()"],
        "time_ns": ["time_ns()"],
        "clock_ns": ["clock_ns()"],
        "num_cpus": ["num_cpus()"],
    }
    if name in specific:
        return specific[name]

    generic_args = [
        "",
        "i",
        "s",
        "path",
        "arr",
        "sa",
        "d",
        "p",
        "i, j",
        "s, s",
        "path, s",
        "arr, i",
        "sa, s",
        "d, s",
        "p, i",
        "i, j, i",
    ]
    return [f"{name}({args})" if args else f"{name}()" for args in generic_args]


def _probe_builtin(name: str) -> BuiltinProbe:
    _ensure_source_import_path()
    from lexer.scan import CONTEXTUAL_KEYWORDS, tokenize

    tokenized = tokenize(f"{name}(")
    head_type = tokenized[0][0] if tokenized else ""
    if head_type not in {"IDENT"} and head_type not in CONTEXTUAL_KEYWORDS:
        return BuiltinProbe(
            builtin=name,
            status="skipped",
            selected_call=None,
            check_ms=None,
            exit_code=None,
            detail=f"non-callable token type: {head_type or 'none'}",
        )

    candidates = _candidate_calls(name)
    last_rc = 1
    last_ms = 0.0
    last_detail = "no candidate attempted"
    for call in candidates:
        decls = _declaration_lines_for_call(call)
        for stmt, printer in (
            (call, "print(0)"),
            (f"x = {call}", "print(x)"),
        ):
            lines = _base_main_prefix() + decls
            lines.extend([f"    {stmt}", f"    {printer}", "    return 0", "end"])
            code = "\n".join(lines) + "\n"
            rc, out, err, ms = _check_program(code, f"builtin_{name}")
            if rc == 0:
                return BuiltinProbe(
                    builtin=name,
                    status="passed",
                    selected_call=stmt,
                    check_ms=round(ms, 3),
                    exit_code=0,
                    detail="ok",
                )
            full = ((out or "") + "\n" + (err or "")).strip()
            last_rc = rc
            last_ms = ms
            last_detail = "\n".join(full.splitlines()[:4]) if full else "(no output)"
            if "Lexer error" in full:
                break

    return BuiltinProbe(
        builtin=name,
        status="failed",
        selected_call=None,
        check_ms=round(last_ms, 3),
        exit_code=last_rc,
        detail=last_detail,
    )


def _run_builtin_suite(
    builtins: set[str],
    max_builtins: int | None,
) -> list[BuiltinProbe]:
    ordered = sorted(builtins)
    if isinstance(max_builtins, int) and max_builtins > 0:
        ordered = ordered[:max_builtins]
    probes: list[BuiltinProbe] = []
    for name in ordered:
        probes.append(_probe_builtin(name))
    return probes


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_md(path: Path, payload: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Strict Surface Suite")
    lines.append("")
    lines.append(f"- Generated: {payload['timestamp_human']}")
    lines.append(
        f"- Tokens: {payload['token_summary']['passed']}/{payload['token_summary']['total']}"
    )
    lines.append(
        f"- Callable builtins: {payload['builtin_summary']['passed']}/{payload['builtin_summary']['total']}"
    )
    lines.append(
        f"- Non-callable surface symbols: {payload['non_callable_surface_total']}"
    )
    lines.append("")
    lines.append("## Token Failures")
    lines.append("")
    token_failures = [t for t in payload["tokens"] if not t["passed"]]
    if not token_failures:
        lines.append("- none")
    else:
        for row in token_failures[:120]:
            lines.append(
                f"- `{row['token_type']}` probe=`{row['probe']}` note=`{row['note']}`"
            )
    lines.append("")
    lines.append("## Builtin Failures")
    lines.append("")
    builtin_failures = [b for b in payload["builtins"] if b["status"] == "failed"]
    builtin_skips = [b for b in payload["builtins"] if b["status"] == "skipped"]
    if not builtin_failures:
        lines.append("- none")
    else:
        for row in builtin_failures[:200]:
            lines.append(
                f"- `{row['builtin']}` exit={row['exit_code']} detail=`{row['detail']}`"
            )
    lines.append("")
    lines.append("## Builtin Skips")
    lines.append("")
    if not builtin_skips:
        lines.append("- none")
    else:
        for row in builtin_skips[:200]:
            lines.append(f"- `{row['builtin']}` reason=`{row['detail']}`")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run strict token/builtin surface suite.")
    p.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_JSON,
        help="JSON output path.",
    )
    p.add_argument(
        "--output-md",
        type=Path,
        default=DEFAULT_MD,
        help="Markdown output path.",
    )
    p.add_argument(
        "--max-builtins",
        type=int,
        default=None,
        help="Optional builtin cap for faster smoke runs.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    token_patterns, callable_builtins, language_surface, _contextual = _load_catalogs()
    token_results = _run_token_suite()
    builtin_results = _run_builtin_suite(callable_builtins, args.max_builtins)

    token_passed = sum(1 for row in token_results if row.passed)
    builtin_passed = sum(1 for row in builtin_results if row.status == "passed")
    builtin_failed = sum(1 for row in builtin_results if row.status == "failed")
    builtin_skipped = sum(1 for row in builtin_results if row.status == "skipped")

    payload: dict[str, Any] = {
        "timestamp_human": datetime.now().strftime(DATE_HUMAN_FMT),
        "timestamp_iso": datetime.now().strftime(DATE_ISO_FMT),
        "token_inventory_total": len(token_patterns),
        "language_surface_total": len(language_surface),
        "callable_builtin_total": len(callable_builtins),
        "non_callable_surface_total": len(language_surface - callable_builtins),
        "token_summary": {"passed": token_passed, "total": len(token_results)},
        "builtin_summary": {
            "passed": builtin_passed,
            "failed": builtin_failed,
            "skipped": builtin_skipped,
            "total": len(builtin_results),
        },
        "tokens": [row.__dict__ for row in token_results],
        "builtins": [row.__dict__ for row in builtin_results],
    }

    out_json = args.output_json.resolve()
    out_md = args.output_md.resolve()
    _write_json(out_json, payload)
    _write_md(out_md, payload)
    print(f"strict suite json: {out_json}")
    print(f"strict suite md: {out_md}")
    print(f"token pass: {token_passed}/{len(token_results)}")
    print(
        "callable inventory: "
        f"{len(callable_builtins)} "
        f"(non-callable surface symbols: {len(language_surface - callable_builtins)})"
    )
    print(
        "callable builtin status: "
        f"passed={builtin_passed}, failed={builtin_failed}, skipped={builtin_skipped}, "
        f"total={len(builtin_results)}"
    )

    return 0 if token_passed == len(token_results) and builtin_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
