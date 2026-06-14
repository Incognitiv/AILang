#include <stdint.h>
#include <stdio.h>

static int64_t format_print_bench(int64_t iterations) {
    int64_t sink = 0;
    for (int64_t i = 0; i < iterations; i++) {
        printf("%lld\n", (long long)i);
        sink += i;
    }
    return sink;
}

int main(void) {
    const int64_t iterations = 100;
    int64_t result = format_print_bench(iterations);
    printf("%lld\n", (long long)result);
    return 0;
}
