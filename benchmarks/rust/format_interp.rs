fn format_interp_bench(iterations: i64) -> i64 {
    let mut sink: i64 = 0;
    let mut i: i64 = 0;
    while i < iterations {
        sink += format!("v={}", i).len() as i64;
        i += 1;
    }
    sink
}

fn main() {
    let iterations: i64 = 1_000;
    let result = format_interp_bench(iterations);
    println!("{}", result);
}
