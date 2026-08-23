use std::fs;

fn file_io_bench(iterations: i32) -> i64 {
    let path = "benchmarks/out/file_io_bench.txt";
    let payload = "abcdefghijklmnopqrstuvwxyz0123456789";
    let mut checksum: i64 = 0;

    for _ in 0..iterations {
        fs::write(path, payload).expect("write file");
        let content = fs::read_to_string(path).expect("read file");
        checksum += content.len() as i64;
    }
    checksum
}

fn main() {
    let iterations = 2000;
    let result = file_io_bench(iterations);
    println!("{}", result);
}
