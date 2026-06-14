fn slice_sum_bench(iterations: i64) -> i64 {
    let data: [i64; 8] = [3, 1, 4, 1, 5, 9, 2, 6];
    let view: &[i64] = &data;
    let mut acc: i64 = 0;
    for _ in 0..iterations {
        for &value in view {
            acc += value;
        }
    }
    acc
}

fn main() {
    let iterations: i64 = 250_000;
    let result = slice_sum_bench(iterations);
    println!("{result}");
}
