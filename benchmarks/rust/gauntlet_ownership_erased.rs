#[inline(always)]
fn decimal_len_i64(value: i64) -> i64 {
    let mut v = if value < 0 {
        value.wrapping_neg() as u64
    } else {
        value as u64
    };
    let mut len = if value < 0 { 1_i64 } else { 0_i64 };
    loop {
        len += 1;
        v /= 10;
        if v == 0 {
            return len;
        }
    }
}

fn ownership_churn(iterations: i64) -> i64 {
    let mut acc = 0_i64;
    let mut i = 0_i64;
    while i < iterations {
        let seed = i % 97;
        let score = 4 + decimal_len_i64(i) + seed + (seed + 1) + (seed + 2);
        acc = (acc + score) % 1_000_000_007;
        i += 1;
    }
    acc
}

fn main() {
    println!("{}", ownership_churn(8_000_000));
}
