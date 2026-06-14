#include <stdint.h>
#include <stdio.h>

uint32_t loop_hash_bench(uint32_t iterations) {
    uint32_t acc = 0u;
    for (uint32_t i = 0; i < iterations; i++) {
        acc += i;
        if (acc > 1000000000u) {
            acc -= 1000000000u;
        }
    }
    return acc;
}

int main(void) {
    const uint32_t iterations = 12000000u;
    uint32_t result = loop_hash_bench(iterations);
    printf("%u\n", result);
    return 0;
}
