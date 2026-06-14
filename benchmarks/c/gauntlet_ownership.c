#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct Packet {
    char *label;
    int64_t *values;
    int64_t len;
} Packet;

static char *make_label(int64_t i) {
    char buffer[64];
    int written = snprintf(buffer, sizeof(buffer), "pkt_%lld", (long long)i);
    if (written < 0) {
        return NULL;
    }
    size_t n = (size_t)written + 1u;
    char *out = (char *)malloc(n);
    if (!out) {
        return NULL;
    }
    memcpy(out, buffer, n);
    return out;
}

static Packet packet_new(int64_t i) {
    Packet p;
    p.label = make_label(i);
    p.values = (int64_t *)malloc(3u * sizeof(int64_t));
    p.len = 3;
    int64_t seed = i % 97LL;
    if (p.values) {
        p.values[0] = seed;
        p.values[1] = seed + 1LL;
        p.values[2] = seed + 2LL;
    }
    return p;
}

static int64_t packet_score(const Packet *p) {
    return (int64_t)strlen(p->label) + p->values[0] + p->values[1] + p->values[2];
}

static void packet_free(Packet *p) {
    free(p->label);
    free(p->values);
    p->label = NULL;
    p->values = NULL;
    p->len = 0;
}

static int64_t ownership_churn(int64_t iterations) {
    int64_t acc = 0;
    for (int64_t i = 0; i < iterations; ++i) {
        Packet p = packet_new(i);
        acc = (acc + packet_score(&p)) % 1000000007LL;
        packet_free(&p);
    }
    return acc;
}

int main(void) {
    printf("%lld\n", (long long)ownership_churn(8000000LL));
    return 0;
}
