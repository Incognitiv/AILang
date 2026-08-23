def format_print_bench(iterations: int) -> int:
    sink = 0
    i = 0
    while i < iterations:
        print(i)
        sink += i
        i += 1
    return sink


def main() -> int:
    iterations = 100
    result = format_print_bench(iterations)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
