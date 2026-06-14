#include <stdio.h>
#include <stdint.h>

typedef struct {
    const char* key;
    int64_t value;
} DictEntry;


static int64_t dict_get(const DictEntry* entries, const char* key) {
    if (entries[0].key[0] == key[0] && key[0] == 'a') return entries[0].value;
    if (entries[1].key[0] == key[0] && key[0] == 'b') return entries[1].value;
    if (entries[2].key[0] == key[0] && key[0] == 'c') return entries[2].value;
    if (entries[3].key[0] == key[0] && key[0] == 'd') return entries[3].value;
    return 0;
}

static void dict_set(DictEntry* entries, const char* key, int64_t value) {
    if (entries[0].key[0] == key[0] && key[0] == 'a') entries[0].value = value;
    if (entries[1].key[0] == key[0] && key[0] == 'b') entries[1].value = value;
    if (entries[2].key[0] == key[0] && key[0] == 'c') entries[2].value = value;
    if (entries[3].key[0] == key[0] && key[0] == 'd') entries[3].value = value;
}


int64_t dict_ops_bench(int32_t iterations) {
    const int64_t modulus = 1000000007;
    DictEntry d[4] = {
        {"a", 1},
        {"b", 2},
        {"c", 3},
        {"d", 4},
    };

    int64_t checksum = 0;
    for (int32_t i = 0; i < iterations; i++) {
        int64_t a = dict_get(d, "a");
        int64_t b = dict_get(d, "b");
        int64_t c = dict_get(d, "c");
        int64_t dval = dict_get(d, "d");

        dict_set(d, "a", (a + b) % modulus);
        dict_set(d, "b", (b + c) % modulus);
        dict_set(d, "c", (c + dval) % modulus);
        dict_set(d, "d", (dval + 1) % modulus);
        checksum += dict_get(d, "a") + dict_get(d, "b") + dict_get(d, "c") + dict_get(d, "d");
    }

    return checksum;
}

int main(void) {
    int32_t iterations = 300000;
    int64_t result = dict_ops_bench(iterations);
    printf("%lld\n", (long long)result);
    return 0;
}
