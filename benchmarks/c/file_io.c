#include <stdio.h>
#include <stdint.h>
#include <string.h>

#define PAYLOAD "abcdefghijklmnopqrstuvwxyz0123456789"


int32_t file_io_bench(int32_t iterations) {
    const char* path = "benchmarks/out/file_io_bench.txt";
    const size_t payload_len = (size_t)strlen(PAYLOAD);
    int64_t checksum = 0;
    char buffer[256];

    for (int32_t i = 0; i < iterations; i++) {
        FILE* out = fopen(path, "wb");
        if (out == NULL) {
            return -1;
        }
        fwrite(PAYLOAD, 1, payload_len, out);
        fclose(out);

        FILE* in = fopen(path, "rb");
        if (in == NULL) {
            return -1;
        }
        size_t read_len = fread(buffer, 1, sizeof(buffer), in);
        (void)fclose(in);
        checksum += (int64_t)read_len;
    }

    return (int32_t)checksum;
}

int main(void) {
    int32_t iterations = 2000;
    int64_t result = file_io_bench(iterations);
    printf("%lld\n", (long long)result);
    return 0;
}
