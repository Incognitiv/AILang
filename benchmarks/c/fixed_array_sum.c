#include <stdint.h>
#include <stdio.h>

static int64_t fixed_array_sum_bench(int64_t iterations) {
    int64_t arr[8] = {3, 1, 4, 1, 5, 9, 2, 6};
    int64_t acc = 0;
    for (int64_t i = 0; i < iterations; ++i) {
        for (int64_t j = 0; j < 8; ++j) {
            acc += arr[j];
        }
    }
    return acc;
}

int main(void) {
    const int64_t iterations = 250000;
    const int64_t result = fixed_array_sum_bench(iterations);
    printf("%lld\n", (long long)result);
    return 0;
}
