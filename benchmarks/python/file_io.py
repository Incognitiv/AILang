from pathlib import Path


def file_io_bench(iterations: int) -> int:
    path = Path("benchmarks/out/file_io_bench.txt")
    payload = "abcdefghijklmnopqrstuvwxyz0123456789"
    checksum = 0
    for _ in range(iterations):
        path.write_text(payload, encoding="utf-8")
        checksum += len(path.read_text(encoding="utf-8"))
    return checksum


def main() -> int:
    iterations = 2000
    result = file_io_bench(iterations)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
