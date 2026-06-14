def fib_mix_bench(iterations: int) -> int:
    a = 0
    b = 7
    i = 0
    while i < iterations:
        a = a + b
        if a > 1_000_000_000:
            a = a - 1_000_000_000
        b += 1
        if b > 1_000_000_000:
            b = b - 1_000_000_000
        i += 1
    return a


def main() -> int:
    iterations = 8000000
    result = fib_mix_bench(iterations)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
