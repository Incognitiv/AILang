fn format_hex_bench(iterations: i64) -> i64 {
    let mut sink: i64 = 0;
    let mut i: i64 = 0;
    while i < iterations {
        sink += format!("0x{:X}", i).len() as i64;
        i += 1;
    }
    sink
}

fn main() {
    let iterations: i64 = 400_000;
    let result = format_hex_bench(iterations);
    println!("{}", result);
}
