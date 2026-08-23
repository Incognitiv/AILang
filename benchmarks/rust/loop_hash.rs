fn loop_hash_bench(iterations: u32) -> u32 {
    let mut acc: u32 = 0u32;
    let mut i: u32 = 0;
    while i < iterations {
        acc += i;
        if acc > 1_000_000_000 {
            acc -= 1_000_000_000;
        }
        i += 1;
    }
    acc
}

fn main() {
    let iterations: u32 = 12_000_000;
    let result = loop_hash_bench(iterations);
    println!("{}", result);
}
