def format_interp_bench(iterations: int) -> int:
    sink = 0
    i = 0
    while i < iterations:
        sink += len(f"v={i}")
        i += 1
    return sink


def main() -> int:
    iterations = 1000
    result = format_interp_bench(iterations)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
