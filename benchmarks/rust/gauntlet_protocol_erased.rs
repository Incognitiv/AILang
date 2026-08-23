#[inline(always)]
fn protocol_scan_erased(iterations: i64) -> i64 {
    let packet_hash = 393_291_961_i64;
    let mut acc = 0_i64;
    let mut i = 0_i64;
    while i < iterations {
        acc = (acc + packet_hash + i) % 1_000_000_007;
        i += 1;
    }
    acc
}

fn main() {
    println!("{}", protocol_scan_erased(1_200_000));
}
