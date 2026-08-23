#include <stdint.h>
#include <stdio.h>

static inline int64_t protocol_scan_erased(int64_t iterations) {
    const int64_t packet_hash = 393291961LL;
    int64_t acc = 0;
    for (int64_t i = 0; i < iterations; ++i) {
        acc = (acc + packet_hash + i) % 1000000007LL;
    }
    return acc;
}

int main(void) {
    printf("%lld\n", (long long)protocol_scan_erased(1200000LL));
    return 0;
}
