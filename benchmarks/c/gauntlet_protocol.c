#include <stdint.h>
#include <stdio.h>
#include <string.h>

static int64_t scan_packet(const char *body) {
    size_t n = strlen(body);
    size_t i = 0;
    int64_t acc = 0;
    while (i < n) {
        unsigned char c = (unsigned char)body[i];
        if (c >= '0' && c <= '9') {
            int64_t value = 0;
            while (i < n) {
                unsigned char d = (unsigned char)body[i];
                if (d < '0' || d > '9') {
                    break;
                }
                value = value * 10 + (int64_t)(d - '0');
                ++i;
            }
            acc = (acc * 131 + value) % 1000000007LL;
        } else {
            ++i;
        }
    }
    return acc;
}

static int64_t protocol_scan(int64_t iterations) {
    const char *packet = "ADAPTC1 700 42 100 987 654 321 88 77 66 55 44 33 22 11 999\n";
    int64_t acc = 0;
    for (int64_t i = 0; i < iterations; ++i) {
        acc = (acc + scan_packet(packet) + i) % 1000000007LL;
    }
    return acc;
}

int main(void) {
    printf("%lld\n", (long long)protocol_scan(1200000LL));
    return 0;
}
