#[inline(always)]
fn numeric_mix_erased(seed: i64, iterations: i64) -> i64 {
    let mut x = seed % 1_000_003;
    let mut y = 911_382_323_i64 % 1_000_003;
    let mut z = 972_663_749_i64 % 1_000_003;
    let mut acc = 0_i64;
    let mut i = 0_i64;
    while i < iterations {
        x = (x * 110_351 + 12_345 + i) % 1_000_003;
        y = (y + x * 31 + i * 17) % 1_000_033;
        z = if x > y {
            (z + x - y + 97) % 1_000_037
        } else {
            (z + y - x + 193) % 1_000_037
        };
        acc = if z % 7 == 0 {
            (acc + z * 3 + x) % 1_000_000_007
        } else {
            (acc + y * 5 + z) % 1_000_000_007
        };
        i += 1;
    }
    acc
}

fn main() {
    println!("{}", numeric_mix_erased(1_234_567, 8_000_000));
}
