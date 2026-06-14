def format_str_int_bench(iterations: int) -> int:
    sink = 0
    i = 0
    while i < iterations:
        sink += len(str(i))
        i += 1
    return sink


def main() -> int:
    iterations = 400000
    result = format_str_int_bench(iterations)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
