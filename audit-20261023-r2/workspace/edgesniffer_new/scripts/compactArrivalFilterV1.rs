use std::collections::HashSet;
use std::convert::TryInto;
use std::env;
use std::fs::File;
use std::io::{self, BufWriter, Read, Seek, SeekFrom, Write};

const MAGIC: &[u8; 8] = b"AHCDV1\n\0";
const DIGEST_LEN: usize = 32;
const RECORD_LEN: usize = 48;
const RECORDS_PER_CHUNK: usize = 262_144;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 3 {
        return Err("usage: compact-arrival-filter-v1 INPUT TARGET_DIGESTS_BIN".into());
    }

    let target_bytes = std::fs::read(&args[2])?;
    if target_bytes.len() % DIGEST_LEN != 0 {
        return Err("target digest file length is not divisible by 32".into());
    }
    let targets: HashSet<[u8; DIGEST_LEN]> = target_bytes
        .chunks_exact(DIGEST_LEN)
        .map(|chunk| chunk.try_into().expect("fixed digest length"))
        .collect();

    let mut input = File::open(&args[1])?;
    let snapshot_len = input.metadata()?.len();
    if snapshot_len < MAGIC.len() as u64 {
        return Err("compact journal is shorter than its magic".into());
    }
    let mut magic = [0_u8; MAGIC.len()];
    input.read_exact(&mut magic)?;
    if &magic != MAGIC {
        return Err("compact journal magic mismatch".into());
    }

    let payload_len = snapshot_len - MAGIC.len() as u64;
    let mut records_remaining = payload_len / RECORD_LEN as u64;
    input.seek(SeekFrom::Start(MAGIC.len() as u64))?;
    let mut buffer = vec![0_u8; RECORD_LEN * RECORDS_PER_CHUNK];
    let stdout = io::stdout();
    let mut output = BufWriter::with_capacity(1024 * 1024, stdout.lock());

    while records_remaining > 0 {
        let records = records_remaining.min(RECORDS_PER_CHUNK as u64) as usize;
        let bytes = records * RECORD_LEN;
        input.read_exact(&mut buffer[..bytes])?;
        for record in buffer[..bytes].chunks_exact(RECORD_LEN) {
            let digest: &[u8; DIGEST_LEN] = record[..DIGEST_LEN]
                .try_into()
                .expect("fixed record digest length");
            if targets.contains(digest) {
                output.write_all(record)?;
            }
        }
        records_remaining -= records as u64;
    }
    output.flush()?;
    Ok(())
}
