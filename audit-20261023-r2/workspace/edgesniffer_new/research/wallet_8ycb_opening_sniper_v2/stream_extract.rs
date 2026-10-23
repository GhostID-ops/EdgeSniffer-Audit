use std::collections::HashSet;
use std::env;
use std::fs::File;
use std::io::{self, BufRead, BufReader, BufWriter, Read, Write};

fn extract_json_string(line: &[u8], needle: &[u8]) -> Option<String> {
    let start = line.windows(needle.len()).position(|window| window == needle)? + needle.len();
    let end = line[start..].iter().position(|byte| *byte == b'"')? + start;
    String::from_utf8(line[start..end].to_vec()).ok()
}

fn main() -> io::Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 5 {
        eprintln!("usage: stream_extract <input.jsonl> <byte_limit> <mints.txt> <output.jsonl>");
        std::process::exit(2);
    }
    let input = &args[1];
    let byte_limit: u64 = args[2].parse().expect("byte_limit");
    let mint_file = &args[3];
    let output = &args[4];
    let mints: HashSet<String> = BufReader::new(File::open(mint_file)?)
        .lines()
        .filter_map(Result::ok)
        .map(|line| line.trim().to_string())
        .filter(|line| !line.is_empty())
        .collect();
    let reader = BufReader::with_capacity(16 * 1024 * 1024, File::open(input)?.take(byte_limit));
    let mut writer = BufWriter::with_capacity(4 * 1024 * 1024, File::create(output)?);
    let mut lines: u64 = 0;
    let mut matched: u64 = 0;
    let mut malformed_tail = 0;
    for result in reader.split(b'\n') {
        let mut line = result?;
        lines += 1;
        if line.is_empty() {
            continue;
        }
        if line.last() == Some(&b'\r') {
            line.pop();
        }
        let Some(mint) = extract_json_string(&line, b"\"mint\":\"") else {
            malformed_tail += 1;
            continue;
        };
        if mints.contains(&mint) {
            writer.write_all(&line)?;
            writer.write_all(b"\n")?;
            matched += 1;
        }
    }
    writer.flush()?;
    eprintln!("lines={} matched={} malformed_tail={} mints={} byte_limit={}", lines, matched, malformed_tail, mints.len(), byte_limit);
    Ok(())
}
