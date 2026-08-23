use std::collections::HashMap;

fn dict_ops_bench(iterations: usize) -> i64 {
    let mut data: HashMap<&str, i64> = HashMap::from([
        ("a", 1),
        ("b", 2),
        ("c", 3),
        ("d", 4),
    ]);
    let modulus = 1_000_000_007i64;

    let mut checksum: i64 = 0;
    for _ in 0..iterations {
        let a = data["a"];
        let b = data["b"];
        let c = data["c"];
        let dval = data["d"];
        data.insert("a", (a + b) % modulus);
        data.insert("b", (b + c) % modulus);
        data.insert("c", (c + dval) % modulus);
        data.insert("d", (dval + 1) % modulus);
        checksum += data["a"] + data["b"] + data["c"] + data["d"];
    }
    checksum
}

fn main() {
    let iterations = 300000usize;
    let result = dict_ops_bench(iterations);
    println!("{}", result);
}
