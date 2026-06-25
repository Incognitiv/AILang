"""Shared deterministic AILang validation program generator."""

from __future__ import annotations

import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

HELPER_OBJECT_TOKEN = "__AILANG_VALIDATION_HELPER_OBJECT__"


@dataclass(frozen=True)
class ProgramCase:
    name: str
    source: str
    expected_lines: list[str]
    tags: tuple[str, ...] = ()
    helper_c: str | None = None


def materialize_case(
    case: ProgramCase,
    directory: Path,
    *,
    helper_compiler: str | None = None,
    helper_flags: tuple[str, ...] = (),
) -> Path:
    """Write a generated case and optional native helper into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    source = case.source
    if case.helper_c is not None:
        if helper_compiler is None:
            raise ValueError(f"{case.name} needs a helper C compiler")
        helper_c = directory / f"{case.name}_helper.c"
        helper_o = directory / f"{case.name}_helper.o"
        helper_c.write_text(case.helper_c, encoding="utf-8")
        proc = subprocess.run(
            [
                helper_compiler,
                *helper_flags,
                "-c",
                str(helper_c),
                "-o",
                str(helper_o),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stdout + proc.stderr)[-1200:]
            raise RuntimeError(f"{case.name} helper compile failed: {detail}")
        source = source.replace(HELPER_OBJECT_TOKEN, helper_o.as_posix())

    path = directory / f"{case.name}.ail"
    path.write_text(source, encoding="utf-8")
    return path


def _numeric_case(rng: random.Random, index: int) -> ProgramCase:
    loops = rng.randint(3, 18)
    start = rng.randint(0, 200)
    add = rng.randint(1, 40)
    mul = rng.randint(2, 9)
    sub = rng.randint(0, 30)
    modulus = rng.randint(97, 997)
    adjust_mod = rng.randint(2, 11)
    adjust_hit = rng.randint(0, adjust_mod - 1)
    adjust_add = rng.randint(1, 60)
    adjust_sub = rng.randint(0, 30)

    acc = start
    for _ in range(loops):
        acc = ((acc + add) * mul - sub) % modulus
        if acc % adjust_mod == adjust_hit:
            acc += adjust_add
        else:
            acc -= adjust_sub

    source = f"""\
def adjust(x: int): int
    if x % {adjust_mod} == {adjust_hit} then
        return x + {adjust_add}
    else
        return x - {adjust_sub}
    end
end

def mix(x: int): int
    acc = x
    i = 0
    while i < {loops} then
        acc = ((acc + {add}) * {mul} - {sub}) % {modulus}
        acc = adjust(acc)
        i = i + 1
    end
    return acc
end

def main(): int
    print(mix({start}))
    return 0
end
"""
    return ProgramCase(
        f"generated_numeric_{index:03d}", source, [str(acc)], ("numeric",)
    )


def _branch_case(rng: random.Random, index: int) -> ProgramCase:
    values = [rng.randint(-50, 80) for _ in range(rng.randint(4, 10))]
    threshold = rng.randint(-10, 50)
    hi_mul = rng.randint(2, 7)
    lo_mul = rng.randint(1, 5)
    total = 0
    for value in values:
        if value >= threshold:
            total += value * hi_mul
        else:
            total -= value * lo_mul

    statements = "\n".join(f"    total = total + score({value})" for value in values)
    source = f"""\
def score(x: int): int
    if x >= {threshold} then
        return x * {hi_mul}
    else
        return 0 - (x * {lo_mul})
    end
end

def main(): int
    total = 0
{statements}
    print(total)
    return 0
end
"""
    return ProgramCase(
        f"generated_branch_{index:03d}", source, [str(total)], ("branch",)
    )


def _string_case(rng: random.Random, index: int) -> ProgramCase:
    left = rng.choice(["alpha", "beta", "gamma", "delta"])
    right = rng.choice(["red", "blue", "green", "gold"])
    seed = rng.randint(1, 99)
    expected = f"{left}_{seed}|{right}_{seed + 1}"
    source = f"""\
def make_text(prefix: string, seed: int): string
    return prefix + "_" + str(seed)
end

def main(): int
    first = make_text("{left}", {seed})
    second = make_text("{right}", {seed + 1})
    joined = first + "|" + second
    print(joined)
    print(strlen(joined))
    return 0
end
"""
    return ProgramCase(
        f"generated_string_{index:03d}",
        source,
        [expected, str(len(expected))],
        ("string", "ownership"),
    )


def _array_case(rng: random.Random, index: int) -> ProgramCase:
    count = rng.randint(3, 8)
    values = [rng.randint(0, 30) for _ in range(count)]
    total = sum(value * (pos + 1) for pos, value in enumerate(values))
    literal = ", ".join(str(value) for value in values)
    source = f"""\
type Arr{count} = [int; {count}]

def main(): int
    Arr{count} arr = [{literal}]
    total = 0
    i = 0
    while i < {count} then
        total = total + arr[i] * (i + 1)
        i = i + 1
    end
    print(total)
    print(sizeof("Arr{count}"))
    return 0
end
"""
    return ProgramCase(
        f"generated_array_{index:03d}",
        source,
        [str(total), str(count * 8)],
        ("array", "fixed-array"),
    )


def _record_layout_case(rng: random.Random, index: int) -> ProgramCase:
    data_len = rng.randint(2, 9)
    tail_offset = 16 + data_len
    size = ((tail_offset + 1 + 7) // 8) * 8
    source = f"""\
record LayoutPacket then
    byte tag
    int value
    [byte;{data_len}] data
    byte tail
end

def main(): int
    print(offsetof("LayoutPacket", "tag"))
    print(offsetof("LayoutPacket", "value"))
    print(offsetof("LayoutPacket", "data"))
    print(offsetof("LayoutPacket", "tail"))
    print(sizeof("LayoutPacket"))
    print(alignof("LayoutPacket"))
    return 0
end
"""
    return ProgramCase(
        f"generated_record_layout_{index:03d}",
        source,
        ["0", "8", "16", str(tail_offset), str(size), "8"],
        ("record", "abi-layout"),
    )


def _record_value_case(rng: random.Random, index: int) -> ProgramCase:
    left = rng.randint(1, 40)
    right = rng.randint(1, 40)
    right_add = rng.randint(1, 20)
    param_add = rng.randint(1, 20)
    after_local = left + right + right_add
    bumped = left + param_add + right + right_add
    source = f"""\
record Pair then
    int left
    int right
end

def bumped_sum(p: Pair): int
    p.left = p.left + {param_add}
    return p.left + p.right
end

def main(): int
    Pair p = new Pair({left}, {right})
    p.right = p.right + {right_add}
    print(p.left + p.right)
    print(bumped_sum(p))
    print(p.left + p.right)
    return 0
end
"""
    return ProgramCase(
        f"generated_record_value_{index:03d}",
        source,
        [str(after_local), str(bumped), str(after_local)],
        ("record", "record-value", "field-access", "field-assign"),
    )


def _pointer_case(rng: random.Random, index: int) -> ProgramCase:
    left = rng.randint(1, 500)
    right = rng.randint(1, 500)
    source = f"""\
def main(): int
    p = alloc(16)
    q = ptr_add(p, 8)
    poke64(p, 0, {left})
    poke64(q, 0, {right})
    total = peek64(p, 0) + peek64(q, 0)
    dealloc(p)
    print(total)
    return 0
end
"""
    return ProgramCase(
        f"generated_pointer_{index:03d}",
        source,
        [str(left + right)],
        ("pointer", "memory"),
    )


def _ffi_case(rng: random.Random, index: int) -> ProgramCase:
    left = rng.randint(2, 12)
    right = rng.randint(2, 12)
    add = rng.randint(0, 30)
    value = left * right + add
    text = rng.choice(["ffi", "native", "abi", "link"])
    source = f"""\
#link "{HELPER_OBJECT_TOKEN}"

extern fn validation_mul_add(a: int, b: int, c: int): int
extern fn validation_strlen_plus(text: string, plus: int): int

def main(): int
    value = validation_mul_add({left}, {right}, {add})
    print(value)
    print(validation_strlen_plus("{text}", value))
    return 0
end
"""
    helper_c = """\
#include <stdint.h>
#include <string.h>

int64_t validation_mul_add(int64_t a, int64_t b, int64_t c) {
    return a * b + c;
}

int64_t validation_strlen_plus(const char *text, int64_t plus) {
    return (int64_t)strlen(text) + plus;
}
"""
    return ProgramCase(
        f"generated_ffi_{index:03d}",
        source,
        [str(value), str(len(text) + value)],
        ("ffi", "native-helper"),
        helper_c,
    )


def runtime_surface_cases() -> list[ProgramCase]:
    """Curated runtime cases for language-surface health checks.

    These cases are intentionally small and deterministic. Keyword parser smoke
    proves every spelling reaches the grammar; this suite proves representative
    runtime-bearing feature families compile, execute, agree across backends,
    and can be passed to ASAN/Valgrind.
    """

    return [
        ProgramCase(
            "surface_logic_keywords",
            """\
def main(): int
    value = 0
    if true and not false then
        value = value + 1
    else
        value = value + 100
    end
    if false or true then
        value = value + 2
    end
    if 1 is 1 then
        value = value + 3
    end
    if 1 is not 2 then
        value = value + 4
    end
    print(value)
    return 0
end
""",
            ["10"],
            (
                "surface-runtime",
                "kw:TRUE",
                "kw:FALSE",
                "kw:AND",
                "kw:OR",
                "kw:NOT",
                "kw:IF",
                "kw:ELSE",
                "kw:IS",
                "kw:IS_NOT",
            ),
        ),
        ProgramCase(
            "surface_gate_keywords",
            """\
def main(): int
    print(7 AND 3)
    print(7 OR 3)
    print(7 XOR 3)
    print(7 NAND 3)
    print(7 NOR 3)
    print(7 XNOR 3)
    print(7 band 3)
    print(7 bor 3)
    print(7 bxor 3)
    print(bnot 0)
    print(8 shl 1)
    print(8 shr 1)
    print(8 ushr 1)
    print(8 SHL 1)
    print(8 SHR 1)
    print(8 USHR 1)
    return 0
end
""",
            [
                "3",
                "7",
                "4",
                "-4",
                "-8",
                "-5",
                "3",
                "7",
                "4",
                "-1",
                "16",
                "4",
                "4",
                "16",
                "4",
                "4",
            ],
            (
                "surface-runtime",
                "kw:GATE_AND",
                "kw:GATE_OR",
                "kw:GATE_XOR",
                "kw:GATE_NAND",
                "kw:GATE_NOR",
                "kw:GATE_XNOR",
                "kw:GATE_NOT",
                "kw:SHL",
                "kw:SHR",
                "kw:USHR",
            ),
        ),
        ProgramCase(
            "surface_control_keywords",
            """\
def main(): int
    total = 0
    if false then
        total = 99
    elsif false then
        total = 98
    otherwise true then
        total = 1
    else
        total = 97
    end
    unless false then
        total = total + 2
    end
    i = 0
    while i < 3 then
        i = i + 1
        if i == 2 then
            continue
        end
        total = total + i
    end
    j = 0
    until j == 2 then
        j = j + 1
    end
    total = total + j
    do then
        total = total + 1
    end while false
    repeat 2 times then
        total = total + 1
    end
    loop max: 4 then
        total = total + 1
        break
    end
    for (k = 0; k < 2; k = k + 1) then
        total = total + k
    end
    print(total)
    return 0
end
""",
            ["14"],
            (
                "surface-runtime",
                "kw:ELSIF",
                "kw:OTHERWISE",
                "kw:UNLESS",
                "kw:WHILE",
                "kw:UNTIL",
                "kw:DO",
                "kw:REPEAT",
                "kw:TIMES",
                "kw:LOOP",
                "kw:INFINITY",
                "kw:BREAK",
                "kw:CONTINUE",
                "kw:FOR",
            ),
        ),
        ProgramCase(
            "surface_memory_keywords",
            """\
def main(): int
    p = alloc(16)
    q = ptr_add(p, 8)
    poke64(p, 0, 40)
    poke64(q, 0, 2)
    print(peek64(p, 0) + peek64(q, 0))
    poke32(p, 0, 7)
    print(peek32(p, 0))
    poke8(p, 0, 5)
    print(peek8(p, 0))
    dealloc(p)
    return 0
end
""",
            ["42", "7", "5"],
            (
                "surface-runtime",
                "kw:ALLOC",
                "kw:DEALLOC",
                "kw:PEEK64",
                "kw:POKE64",
                "kw:PEEK32",
                "kw:POKE32",
                "kw:PEEK8",
                "kw:POKE8",
                "kw:PTR",
            ),
        ),
        ProgramCase(
            "surface_string_keywords",
            """\
def main(): int
    text = "abcde"
    print(strlen(text))
    print(len(text))
    print(char_at(text, 1))
    print(ord("A"))
    print(chr(66))
    print(substr(text, 1, 3))
    putc(90)
    puts("!")
    return 0
end
""",
            ["5", "5", "98", "65", "B", "bcd", "Z!"],
            (
                "surface-runtime",
                "kw:STRING",
                "kw:STRLEN",
                "kw:LEN",
                "kw:CHAR_AT",
                "kw:ORD",
                "kw:CHR",
                "kw:SUBSTR",
                "kw:PUTC",
                "kw:PUTS",
            ),
        ),
        ProgramCase(
            "surface_record_keywords",
            """\
record Pair then
    int left
    int right
end

def pair_sum(p: Pair): int
    p.left = p.left + 1
    return p.left + p.right
end

def main(): int
    Pair p = new Pair(5, 7)
    print(p.left + p.right)
    print(pair_sum(p))
    print(p.left + p.right)
    return 0
end
""",
            ["12", "13", "12"],
            (
                "surface-runtime",
                "kw:RECORD",
                "kw:NEW",
                "kw:DEF",
                "kw:RETURN",
                "kw:INT",
            ),
        ),
        ProgramCase(
            "surface_class_raii_string_field",
            """\
class Session then
    string token
    public def init(text: string):
        this.token = text
    end
    public def set_token(text: string):
        this.token = text
    end
end

def main(): int
    Session first = new Session("owned" + str(7))
    Session second = new Session("literal")
    second.set_token("next" + str(8))
    print(strlen(first.token) + strlen(second.token))
    return 0
end
""",
            ["11"],
            (
                "surface-runtime",
                "class-raii",
                "owned-string-field",
                "method-call",
            ),
        ),
        ProgramCase(
            "surface_class_raii_owned_fields",
            """\
class Child then
    string token
end

class Bag then
    Child child
    array values
    str_array parts
    dict meta
    public def init(child_arg: Child):
        this.child = child_arg
        this.values = array_new(2)
        this.values = array_push(this.values, 41)
        this.parts = str_array_new(2)
        this.parts = str_array_push(this.parts, "x")
        this.meta = dict_new()
    end
end

def main(): int
    Bag bag = new Bag(new Child("owned" + str(7)))
    print(strlen(bag.child.token))
    print(array_len(bag.values) + str_array_len(bag.parts) + dict_size(bag.meta))
    return 0
end
""",
            ["6", "2"],
            (
                "surface-runtime",
                "class-raii",
                "owned-field",
                "dynamic-array",
                "dict",
            ),
        ),
        ProgramCase(
            "surface_alias_type_keywords",
            """\
type Percent = 0..100
typedef int Count
const int LIMIT = 10
static int counter = 0

def main(): int
    Count count = 7
    Percent percent = 5
    counter = counter + count + percent + LIMIT
    print(counter)
    return 0
end
""",
            ["22"],
            (
                "surface-runtime",
                "kw:TYPE",
                "kw:TYPEDEF",
                "kw:CONST",
                "kw:STATIC",
                "kw:RANGE",
            ),
        ),
        ProgramCase(
            "surface_decorator_keywords",
            """\
@inline
@pure
@unchecked
def decorated(value: int): int
    return value + 1
end

static int decorator_seed = 41

def main(): int
    print(decorated(decorator_seed))
    return 0
end
""",
            ["42"],
            (
                "surface-runtime",
                "kw:INLINE",
                "kw:PURE",
                "kw:UNCHECKED",
            ),
        ),
    ]


def generated_cases(count: int, seed: int) -> list[ProgramCase]:
    rng = random.Random(seed)
    cases: list[ProgramCase] = []
    factories = (
        _numeric_case,
        _branch_case,
        _string_case,
        _array_case,
        _record_layout_case,
        _record_value_case,
        _pointer_case,
        _ffi_case,
    )
    for index in range(count):
        factory = factories[index % len(factories)]
        cases.append(factory(rng, index))
    return cases
