use std::collections::{HashMap, VecDeque};
use std::env;
use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, Read, Write};

fn string_field(line: &[u8], needle: &[u8]) -> Option<String> {
    let start = line.windows(needle.len()).position(|window| window == needle)? + needle.len();
    let end = line[start..].iter().position(|byte| *byte == b'"')? + start;
    String::from_utf8(line[start..end].to_vec()).ok()
}

fn u64_field(line: &[u8], needle: &[u8]) -> Option<u64> {
    let start = line.windows(needle.len()).position(|window| window == needle)? + needle.len();
    let end = line[start..]
        .iter()
        .position(|byte| !byte.is_ascii_digit())
        .map(|offset| start + offset)
        .unwrap_or(line.len());
    if end == start {
        return None;
    }
    std::str::from_utf8(&line[start..end]).ok()?.parse().ok()
}

fn clean(value: Option<String>) -> String {
    value.unwrap_or_default().replace('\t', " ").replace('\n', " ")
}

fn main() -> io::Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 {
        eprintln!("usage: extract_frozen_stream <input.jsonl> <byte_limit> <output.tsv>");
        std::process::exit(2);
    }
    let byte_limit: u64 = args[2].parse().expect("byte_limit");
    let reader = BufReader::with_capacity(32 * 1024 * 1024, File::open(&args[1])?.take(byte_limit));
    let mut writer = BufWriter::with_capacity(8 * 1024 * 1024, File::create(&args[3])?);
    writer.write_all(b"ts_ms\tslot\ttx_index\tix_index\tsignature\tmint\tside\twallet\tquote_lamports\tsell_token_raw\n")?;

    let mut active: HashMap<String, u64> = HashMap::new();
    let mut expiries: VecDeque<(u64, String, u64)> = VecDeque::new();
    let mut lines = 0_u64;
    let mut creates = 0_u64;
    let mut events = 0_u64;
    let mut malformed = 0_u64;
    for result in reader.split(b'\n') {
        let line = result?;
        lines += 1;
        if line.is_empty() {
            continue;
        }
        let Some(ts_ms) = u64_field(&line, b"\"local_receive_ts_ms\":") else {
            malformed += 1;
            continue;
        };
        while let Some((expiry, mint, create_ts)) = expiries.front().cloned() {
            if expiry >= ts_ms {
                break;
            }
            expiries.pop_front();
            if active.get(&mint) == Some(&create_ts) {
                active.remove(&mint);
            }
        }
        let Some(mint) = string_field(&line, b"\"mint\":\"") else {
            malformed += 1;
            continue;
        };
        let Some(side) = string_field(&line, b"\"side\":\"") else {
            continue;
        };
        if side == "create" {
            active.insert(mint.clone(), ts_ms);
            expiries.push_back((ts_ms + 120_000, mint.clone(), ts_ms));
            creates += 1;
        } else if side != "buy" && side != "sell" {
            continue;
        }
        let Some(create_ts) = active.get(&mint).copied() else {
            continue;
        };
        if ts_ms > create_ts + 120_000 {
            continue;
        }
        let slot = u64_field(&line, b"\"source_slot\":").unwrap_or(0);
        let tx_index = u64_field(&line, b"\"source_transaction_index\":").unwrap_or(0);
        let ix_index = u64_field(&line, b"\"instructionIndex\":").unwrap_or(0);
        let signature = clean(string_field(&line, b"\"transaction_signature\":\""));
        let wallet = clean(string_field(&line, b"\"wallet\":\""));
        let quote = u64_field(&line, b"\"quote_lamports\":").unwrap_or(0);
        let sell_tokens = u64_field(&line, b"\"sell_token_amount_raw\":").unwrap_or(0);
        writeln!(
            writer,
            "{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}",
            ts_ms, slot, tx_index, ix_index, signature, mint, side, wallet, quote, sell_tokens
        )?;
        events += 1;
    }
    writer.flush()?;
    eprintln!(
        "lines={} creates={} compact_rows={} malformed={} byte_limit={}",
        lines, creates, events, malformed, byte_limit
    );
    Ok(())
}
