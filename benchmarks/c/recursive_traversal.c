#include <stdio.h>
#include <stdint.h>

int64_t recursive_fib(int32_t n) {
    if (n <= 1) {
        return 1;
    }
    return recursive_fib(n - 1) + recursive_fib(n - 2);
}

int64_t recursive_bench(int32_t iterations) {
    return recursive_fib(iterations);
}

int main(int argc, char **argv) {
    (void)argv;
    int32_t depth = 31 + argc;
    int64_t result = recursive_bench(depth);
    printf("%lld\n", (long long)result);
    return 0;
}
