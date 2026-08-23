fn fixed_array_sum_bench(iterations: i64) -> i64 {
    let arr: [i64; 8] = [3, 1, 4, 1, 5, 9, 2, 6];
    let mut acc: i64 = 0;
    for _ in 0..iterations {
        for j in 0..8 {
            acc += arr[j as usize];
        }
    }
    acc
}

fn main() {
    let iterations: i64 = 250_000;
    let result = fixed_array_sum_bench(iterations);
    println!("{result}");
}
