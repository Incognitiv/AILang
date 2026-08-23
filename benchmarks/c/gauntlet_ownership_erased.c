#include <stdint.h>
#include <stdio.h>

static inline int64_t decimal_len_i64(int64_t value) {
    uint64_t v = value < 0 ? (uint64_t)(-value) : (uint64_t)value;
    int64_t len = value < 0 ? 1 : 0;
    do {
        ++len;
        v /= 10u;
    } while (v != 0u);
    return len;
}

static int64_t ownership_churn(int64_t iterations) {
    int64_t acc = 0;
    for (int64_t i = 0; i < iterations; ++i) {
        int64_t seed = i % 97LL;
        int64_t score = 4LL + decimal_len_i64(i) + seed + (seed + 1LL) + (seed + 2LL);
        acc = (acc + score) % 1000000007LL;
    }
    return acc;
}

int main(void) {
    printf("%lld\n", (long long)ownership_churn(8000000LL));
    return 0;
}
