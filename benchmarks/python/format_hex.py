def format_hex_bench(iterations: int) -> int:
    sink = 0
    i = 0
    while i < iterations:
        s = hex(i).upper().replace("X", "x")
        sink += len(s) + ord(s[-1])
        i += 1
    return sink


def main() -> int:
    iterations = 400000
    result = format_hex_bench(iterations)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
