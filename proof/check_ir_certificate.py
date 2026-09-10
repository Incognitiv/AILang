#!/usr/bin/env python3
"""Generate real Typed IR certificates and require Lean to validate them."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"
PROOF = ROOT / "proof"

sys.path.insert(0, str(SOURCE))

from parser.parser import Parser  # noqa: E402

from ir import lower_program, serialize_function_certificate  # noqa: E402
from lexer.scan import tokenize  # noqa: E402


def _lower(source: str) -> str:
    program = Parser(tokenize(source)).parse_program()
    (function,) = lower_program(program)
    return serialize_function_certificate(function)


def _run_lean(certificate: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".aircert", encoding="utf-8", delete=False
    ) as handle:
        handle.write(certificate)
        path = Path(handle.name)
    try:
        return subprocess.run(
            ["lake", "exe", "ailangProofCertificateCheck", str(path)],
            cwd=PROOF,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        path.unlink(missing_ok=True)


def _forge_conversion_kind(certificate: str) -> str:
    marker = "\tlossless_widen\n"
    if marker not in certificate:
        raise AssertionError(
            "test certificate contains no lossless conversion to forge"
        )
    return certificate.replace(marker, "\tchecked\n", 1)


def _forge_undefined_operand(certificate: str) -> str:
    lines = certificate.splitlines()
    for index, line in enumerate(lines):
        fields = line.split("\t")
        if fields and fields[0] == "B":
            fields[2] = "%undefined"
            lines[index] = "\t".join(fields)
            return "\n".join(lines) + "\n"
    raise AssertionError("test certificate contains no binary instruction to forge")


def _forge_constant_kind(certificate: str) -> str:
    lines = certificate.splitlines()
    for index, line in enumerate(lines):
        fields = line.split("\t")
        if fields and fields[0] == "K":
            fields[3] = "int" if fields[3] == "float" else "float"
            lines[index] = "\t".join(fields)
            return "\n".join(lines) + "\n"
    raise AssertionError("test certificate contains no constant instruction to forge")


def _require_accept(label: str, certificate: str) -> None:
    result = _run_lean(certificate)
    if result.returncode == 0:
        return
    print(f"Lean rejected valid certificate {label!r}", file=sys.stderr)
    print(result.stdout, file=sys.stderr, end="")
    print(result.stderr, file=sys.stderr, end="")
    raise SystemExit(1)


def _require_reject(label: str, certificate: str) -> None:
    result = _run_lean(certificate)
    if result.returncode != 0:
        return
    print(f"Lean accepted forged certificate {label!r}", file=sys.stderr)
    print(result.stdout, file=sys.stderr, end="")
    raise SystemExit(1)


def main() -> int:
    sources = {
        "float-double-quad": """
quad funkcja(float a, double b):
    return a + b
end
""",
        "operand-order": """
double subtract(double a, float b):
    return b - a
end
""",
        "fixed-int-checked-return": """
i8 narrow(i256 a, i256 b):
    return a + b
end
""",
        "contextual-f32-literal": """
float half(float x):
    return x / 2.0
end
""",
        "typed-local-nested-expression": """
double compute(float a, double b):
    float half = a / 2.0
    double mixed = (half + b) * 4.0
    return mixed + b
end
""",
        "checked-local-boundary": """
i8 narrow_local(i256 a, i256 b):
    i8 value = a + b
    return value
end
""",
        "exact-f128-literal": """
quad exact_quad():
    return 1.234567890123456789012345678901234q
end
""",
    }

    certificates = {label: _lower(source) for label, source in sources.items()}
    for label, certificate in certificates.items():
        _require_accept(label, certificate)

    exact_quad = certificates["exact-f128-literal"]
    exact_row = "K\t%t0\tf128\tfloat\t1.234567890123456789012345678901234\n"
    if exact_row not in exact_quad:
        print("exact f128 literal text did not survive into the certificate", file=sys.stderr)
        return 1

    golden = certificates["float-double-quad"]
    contextual = certificates["contextual-f32-literal"]
    _require_reject("forged-conversion-kind", _forge_conversion_kind(golden))
    _require_reject("undefined-ssa-operand", _forge_undefined_operand(golden))
    _require_reject("forged-constant-kind", _forge_constant_kind(contextual))

    print(
        "Lean typed IR certificate bridge: "
        f"{len(certificates)}/{len(certificates)} real certificates accepted, "
        "3/3 forged certificates rejected; exact f128 literal preserved"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
