def recursive_fib(n: int) -> int:
    if n <= 1:
        return 1
    return recursive_fib(n - 1) + recursive_fib(n - 2)


def recursive_bench(iterations: int) -> int:
    return recursive_fib(iterations)


def main() -> int:
    depth = 32
    result = recursive_bench(depth)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
