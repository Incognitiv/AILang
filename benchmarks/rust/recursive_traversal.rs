fn recursive_fib(n: i32) -> i64 {
    if n <= 1 {
        return 1;
    }
    recursive_fib(n - 1) + recursive_fib(n - 2)
}

fn recursive_bench(iterations: i32) -> i64 {
    recursive_fib(iterations)
}

fn main() {
    let depth = 32;
    let result = recursive_bench(depth);
    println!("{}", result);
}
