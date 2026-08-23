fn format_str_int_bench(iterations: i64) -> i64 {
    let mut sink: i64 = 0;
    let mut i: i64 = 0;
    while i < iterations {
        let s = i.to_string();
        sink += s.len() as i64 + s.as_bytes()[s.len() - 1] as i64;
        i += 1;
    }
    sink
}

fn main() {
    let iterations: i64 = 400_000;
    let result = format_str_int_bench(iterations);
    println!("{}", result);
}
