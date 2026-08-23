struct Packet {
    label: String,
    values: Vec<i64>,
}

impl Packet {
    fn new(i: i64) -> Self {
        let seed = i % 97;
        Self {
            label: format!("pkt_{}", i),
            values: vec![seed, seed + 1, seed + 2],
        }
    }

    fn score(&self) -> i64 {
        self.label.len() as i64 + self.values[0] + self.values[1] + self.values[2]
    }
}

fn ownership_churn(iterations: i64) -> i64 {
    let mut acc = 0_i64;
    let mut i = 0_i64;
    while i < iterations {
        let p = Packet::new(i);
        acc = (acc + p.score()) % 1_000_000_007;
        i += 1;
    }
    acc
}

fn main() {
    println!("{}", ownership_churn(8_000_000));
}
