fn scan_packet(body: &[u8]) -> i64 {
    let mut i = 0usize;
    let mut acc = 0_i64;
    while i < body.len() {
        let c = body[i];
        if c.is_ascii_digit() {
            let mut value = 0_i64;
            while i < body.len() {
                let d = body[i];
                if !d.is_ascii_digit() {
                    break;
                }
                value = value * 10 + i64::from(d - b'0');
                i += 1;
            }
            acc = (acc * 131 + value) % 1_000_000_007;
        } else {
            i += 1;
        }
    }
    acc
}

fn protocol_scan(iterations: i64) -> i64 {
    let packet = b"ADAPTC1 700 42 100 987 654 321 88 77 66 55 44 33 22 11 999\n";
    let mut acc = 0_i64;
    let mut i = 0_i64;
    while i < iterations {
        acc = (acc + scan_packet(packet) + i) % 1_000_000_007;
        i += 1;
    }
    acc
}

fn main() {
    println!("{}", protocol_scan(1_200_000));
}
