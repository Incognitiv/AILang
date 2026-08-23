fn format_print_bench(iterations: i64) -> i64 {
    let mut sink: i64 = 0;
    let mut i: i64 = 0;
    while i < iterations {
        println!("{}", i);
        sink += i;
        i += 1;
    }
    sink
}

fn main() {
    let iterations: i64 = 100;
    let result = format_print_bench(iterations);
    println!("{}", result);
}
