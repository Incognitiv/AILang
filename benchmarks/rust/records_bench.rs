struct Point {
    x: i64,
    y: i64,
}

fn records_bench(iterations: i32) -> i64 {
    let mut p = Point { x: 1, y: 2 };
    let modulus = 1_000_000_007i64;
    let mut checksum: i64 = 0;

    for _ in 0..iterations {
        p.x = (p.x + p.y) % modulus;
        p.y = (p.y + 2) % modulus;
        checksum += p.x + p.y;
    }

    checksum
}

fn main() {
    let iterations = 4_000_000;
    let result = records_bench(iterations);
    println!("{}", result);
}
