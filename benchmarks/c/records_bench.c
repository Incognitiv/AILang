#include <stdint.h>
#include <stdio.h>

typedef struct {
    int64_t x;
    int64_t y;
} Point;

int64_t records_bench(int32_t iterations) {
    Point p = {1, 2};
    const int64_t modulus = 1000000007;
    int64_t checksum = 0;

    for (int32_t i = 0; i < iterations; i++) {
        p.x = (p.x + p.y) % modulus;
        p.y = (p.y + 2) % modulus;
        checksum += p.x + p.y;
    }

    return checksum;
}

int main(void) {
    int32_t iterations = 4000000;
    int64_t result = records_bench(iterations);
    printf("%lld\n", (long long)result);
    return 0;
}
