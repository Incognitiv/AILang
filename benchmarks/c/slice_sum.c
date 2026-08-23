#include <stdint.h>
#include <stdio.h>

typedef struct {
    int64_t *data;
    int64_t length;
} IntSlice;

static int64_t slice_sum_bench(int64_t iterations) {
    int64_t storage[8] = {3, 1, 4, 1, 5, 9, 2, 6};
    IntSlice view = {storage, 8};
    int64_t acc = 0;
    for (int64_t i = 0; i < iterations; ++i) {
        for (int64_t j = 0; j < view.length; ++j) {
            acc += view.data[j];
        }
    }
    return acc;
}

int main(void) {
    const int64_t iterations = 250000;
    const int64_t result = slice_sum_bench(iterations);
    printf("%lld\n", (long long)result);
    return 0;
}
