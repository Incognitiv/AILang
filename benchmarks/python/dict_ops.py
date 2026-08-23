def dict_ops_bench(iterations: int) -> int:
    data = {"a": 1, "b": 2, "c": 3, "d": 4}
    modulus = 1_000_000_007
    checksum = 0
    for _ in range(iterations):
        a = data["a"]
        b = data["b"]
        c = data["c"]
        dval = data["d"]
        data["a"] = (a + b) % modulus
        data["b"] = (b + c) % modulus
        data["c"] = (c + dval) % modulus
        data["d"] = (dval + 1) % modulus
        checksum += data["a"] + data["b"] + data["c"] + data["d"]
    return checksum


def main() -> int:
    iterations = 300000
    result = dict_ops_bench(iterations)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
