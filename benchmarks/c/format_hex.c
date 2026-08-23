#include <stdint.h>
#include <stdio.h>
#include <string.h>

static int64_t format_hex_bench(int64_t iterations) {
    int64_t sink = 0;
    char buf[32];
    for (int64_t i = 0; i < iterations; i++) {
        snprintf(buf, sizeof(buf), "0x%llX", (unsigned long long)i);
        size_t n = strlen(buf);
        sink += (int64_t)n + (unsigned char)buf[n - 1];
    }
    return sink;
}

int main(void) {
    const int64_t iterations = 400000;
    int64_t result = format_hex_bench(iterations);
    printf("%lld\n", (long long)result);
    return 0;
}
