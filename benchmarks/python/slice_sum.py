def slice_sum_bench(iterations: int) -> int:
    arr = [3, 1, 4, 1, 5, 9, 2, 6]
    view = arr[:]
    acc = 0
    i = 0
    while i < iterations:
        j = 0
        while j < len(view):
            acc += view[j]
            j += 1
        i += 1
    return acc


def main() -> None:
    iterations = 250000
    result = slice_sum_bench(iterations)
    print(result)


if __name__ == "__main__":
    main()
