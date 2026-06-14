#include <stdint.h>
#include <stdio.h>

static inline int64_t numeric_mix_erased(int64_t seed, int64_t iterations) {
    int64_t x = seed % 1000003LL;
    int64_t y = 911382323LL % 1000003LL;
    int64_t z = 972663749LL % 1000003LL;
    int64_t acc = 0;
    for (int64_t i = 0; i < iterations; ++i) {
        x = (x * 110351LL + 12345LL + i) % 1000003LL;
        y = (y + x * 31LL + i * 17LL) % 1000033LL;
        z = x > y
            ? (z + x - y + 97LL) % 1000037LL
            : (z + y - x + 193LL) % 1000037LL;
        acc = (z % 7LL == 0LL)
            ? (acc + z * 3LL + x) % 1000000007LL
            : (acc + y * 5LL + z) % 1000000007LL;
    }
    return acc;
}

int main(void) {
    printf("%lld\n", (long long)numeric_mix_erased(1234567LL, 8000000LL));
    return 0;
}
