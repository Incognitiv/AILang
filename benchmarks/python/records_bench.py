from dataclasses import dataclass


@dataclass
class Point:
    x: int
    y: int


def records_bench(iterations: int) -> int:
    p = Point(1, 2)
    modulus = 1_000_000_007
    checksum = 0
    for _ in range(iterations):
        p.x = (p.x + p.y) % modulus
        p.y = (p.y + 2) % modulus
        checksum += p.x + p.y
    return checksum


def main() -> int:
    iterations = 4000000
    result = records_bench(iterations)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
