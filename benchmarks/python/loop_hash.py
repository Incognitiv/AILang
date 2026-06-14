def loop_hash_bench(iterations: int) -> int:
    acc = 0
    i = 0
    while i < iterations:
        acc = acc + i
        if acc > 1000000000:
            acc = acc - 1000000000
        i += 1
    return acc


def main() -> int:
    iterations = 12_000_000
    result = loop_hash_bench(iterations)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
