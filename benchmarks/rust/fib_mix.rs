fn fib_mix_bench(iterations: u32) -> u32 {
    let mut a: u32 = 0;
    let mut b: u32 = 7;
    let mut i: u32 = 0;
    while i < iterations {
        a = a + b;
        if a > 1_000_000_000 {
            a -= 1_000_000_000;
        }
        b += 1;
        if b > 1_000_000_000 {
            b -= 1_000_000_000;
        }
        i += 1;
    }
    a
}

fn main() {
    let iterations: u32 = 8000000;
    let result = fib_mix_bench(iterations);
    println!("{}", result);
}
