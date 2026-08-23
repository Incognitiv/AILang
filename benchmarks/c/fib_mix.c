#include <stdint.h>
#include <stdio.h>

uint32_t fib_mix_bench(uint32_t iterations) {
    uint32_t a = 0u;
    uint32_t b = 7u;
    for (uint32_t i = 0; i < iterations; i++) {
        a = a + b;
        if (a > 1000000000u) {
            a = a - 1000000000u;
        }
        b = b + 1;
        if (b > 1000000000u) {
            b = b - 1000000000u;
        }
    }
    return a;
}

int main(void) {
    const uint32_t iterations = 8000000u;
    uint32_t result = fib_mix_bench(iterations);
    printf("%u\n", result);
    return 0;
}
