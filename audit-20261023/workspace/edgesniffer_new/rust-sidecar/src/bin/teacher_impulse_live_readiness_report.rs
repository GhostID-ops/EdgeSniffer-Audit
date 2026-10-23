use std::{
    collections::{HashMap, HashSet},
    env,
    fs::File,
    io::{BufRead, BufReader},
};

use anyhow::{bail, Context, Result};
use serde_json::Value;
use sha2::{Digest, Sha256};

fn main() -> Result<()> {
    let args = Args::parse()?;
    run(args)
}

#[derive(Debug, Clone)]
struct Args {
    receipts: String,
    inputs: String,
    lane_name: String,
    max_rows: Option<usize>,
}

#[derive(Debug, Clone)]
struct ReceiptRow {
    mint: String,
    input_hash: String,
    market_cap_usd: Option<f64>,
    has_expected_entry: bool,
    has_expected_tokens_out: bool,
    has_min_tokens_out: bool,
    venue: Option<String>,
}

#[derive(Debug, Clone, Default)]
struct InputRow {
    source_ix_data_base64: Option<String>,
    token_program: Option<String>,
    creation_proven: Option<bool>,
    has_canonical_state: bool,
    canonical_venue: Option<String>,
    mayhem_status: Option<String>,
}

#[derive(Debug, Clone, Default)]
struct Counters {
    total: usize,
    ok: usize,
    missing_input: usize,
    missing_expected_entry: usize,
    missing_expected_tokens_out: usize,
    missing_min_tokens_out: usize,
    missing_source_ix: usize,
    missing_token_program: usize,
    creation_not_proven: usize,
    missing_canonical_state: usize,
    mayhem_not_clean: usize,
    non_pump_fun: usize,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut args = env::args().skip(1);
        let mut parsed = Self {
            receipts: String::new(),
            inputs: String::new(),
            lane_name: "teacher-impulse-live-candidate".to_string(),
            max_rows: None,
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--receipts" => parsed.receipts = next_arg(&mut args, "--receipts")?,
                "--inputs" => parsed.inputs = next_arg(&mut args, "--inputs")?,
                "--lane-name" => parsed.lane_name = next_arg(&mut args, "--lane-name")?,
                "--max-rows" => parsed.max_rows = Some(next_arg(&mut args, "--max-rows")?.parse()?),
                "--help" | "-h" => {
                    println!("usage: teacher_impulse_live_readiness_report --receipts path --inputs path [--lane-name name] [--max-rows n]");
                    std::process::exit(0);
                }
                _ => bail!("unknown argument: {arg}"),
            }
        }
        if parsed.receipts.is_empty() {
            bail!("--receipts is required");
        }
        if parsed.inputs.is_empty() {
            bail!("--inputs is required");
        }
        Ok(parsed)
    }
}

fn next_arg(args: &mut impl Iterator<Item = String>, name: &str) -> Result<String> {
    args.next()
        .with_context(|| format!("{name} requires a value"))
}

fn run(args: Args) -> Result<()> {
    let receipts = load_receipts(&args.receipts, args.max_rows)?;
    let needed = receipts
        .iter()
        .map(|row| row.input_hash.clone())
        .collect::<HashSet<_>>();
    let inputs = load_inputs(&args.inputs, &needed)?;
    let counters = score(&receipts, &inputs);

    println!("# Teacher Impulse Live Candidate Readiness Report");
    println!();
    println!("| Setting | Value |");
    println!("|---|---:|");
    println!("| Lane | {} |", args.lane_name);
    println!("| Accepted receipts checked | {} |", counters.total);
    println!(
        "| Ready for no-submit/live executor handoff | {} |",
        counters.ok
    );
    println!();
    println!("| Check | Count |");
    println!("|---|---:|");
    println!("| Missing input row | {} |", counters.missing_input);
    println!(
        "| Missing expected entry | {} |",
        counters.missing_expected_entry
    );
    println!(
        "| Missing expected tokens out | {} |",
        counters.missing_expected_tokens_out
    );
    println!(
        "| Missing min tokens out | {} |",
        counters.missing_min_tokens_out
    );
    println!(
        "| Missing source ix data | {} |",
        counters.missing_source_ix
    );
    println!(
        "| Missing token program | {} |",
        counters.missing_token_program
    );
    println!("| Creation not proven | {} |", counters.creation_not_proven);
    println!(
        "| Missing canonical state | {} |",
        counters.missing_canonical_state
    );
    println!("| Mayhem not clean | {} |", counters.mayhem_not_clean);
    println!("| Non pump.fun venue | {} |", counters.non_pump_fun);
    println!();

    if counters.ok == counters.total && counters.total > 0 {
        println!("Verdict: READY_FOR_DUST_CALIBRATION_NO_SUBMIT_FIELDS_OK");
    } else {
        println!("Verdict: NOT_READY_FIELD_GAPS_PRESENT");
    }
    Ok(())
}

fn load_receipts(path: &str, max_rows: Option<usize>) -> Result<Vec<ReceiptRow>> {
    let file = File::open(path).with_context(|| format!("open receipts {path}"))?;
    let mut out = Vec::new();
    for (line_no, line) in BufReader::new(file).lines().enumerate() {
        let line = line.with_context(|| format!("read receipt line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let value: Value = serde_json::from_str(&line)
            .with_context(|| format!("parse receipt line {}", line_no + 1))?;
        if value.get("status").and_then(Value::as_str) != Some("accepted") {
            continue;
        }
        let Some(mint) = value.get("mint").and_then(Value::as_str) else {
            continue;
        };
        let Some(input_hash) = value.get("input_hash").and_then(Value::as_str) else {
            continue;
        };
        let expected = value.get("expected_entry").unwrap_or(&Value::Null);
        out.push(ReceiptRow {
            mint: mint.to_string(),
            input_hash: input_hash.to_string(),
            market_cap_usd: value
                .get("calculated_market_cap_usd")
                .and_then(Value::as_f64),
            has_expected_entry: !expected.is_null(),
            has_expected_tokens_out: expected
                .get("expected_tokens_out_raw")
                .map(|value| !value.is_null())
                .unwrap_or(false),
            has_min_tokens_out: expected
                .get("min_tokens_out_raw")
                .map(|value| !value.is_null())
                .unwrap_or(false),
            venue: expected
                .get("venue")
                .and_then(Value::as_str)
                .map(str::to_string),
        });
        if max_rows.is_some_and(|max| out.len() >= max) {
            break;
        }
    }
    Ok(out)
}

fn load_inputs(path: &str, needed: &HashSet<String>) -> Result<HashMap<String, InputRow>> {
    let file = File::open(path).with_context(|| format!("open inputs {path}"))?;
    let mut out = HashMap::new();
    for (line_no, line) in BufReader::new(file).lines().enumerate() {
        let line = line.with_context(|| format!("read input line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let hash = sha256_hex(line.as_bytes());
        if !needed.contains(&hash) {
            continue;
        }
        let value: Value = serde_json::from_str(&line)
            .with_context(|| format!("parse input line {}", line_no + 1))?;
        let candidate = value.get("candidate").unwrap_or(&Value::Null);
        let mayhem = value.get("mayhem").unwrap_or(&Value::Null);
        let canonical_state = value.get("canonical_state").unwrap_or(&Value::Null);
        out.insert(
            hash,
            InputRow {
                source_ix_data_base64: candidate
                    .get("source_ix_data_base64")
                    .and_then(Value::as_str)
                    .or_else(|| candidate.get("sourceIxDataBase64").and_then(Value::as_str))
                    .map(str::to_string),
                token_program: candidate
                    .get("token_program")
                    .and_then(Value::as_str)
                    .or_else(|| candidate.get("tokenProgram").and_then(Value::as_str))
                    .map(str::to_string),
                creation_proven: candidate
                    .get("creation_proven")
                    .and_then(Value::as_bool)
                    .or_else(|| candidate.get("creationProven").and_then(Value::as_bool)),
                has_canonical_state: !canonical_state.is_null(),
                canonical_venue: canonical_state
                    .get("venue")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                mayhem_status: mayhem
                    .get("status")
                    .and_then(Value::as_str)
                    .map(str::to_string),
            },
        );
    }
    Ok(out)
}

fn score(receipts: &[ReceiptRow], inputs: &HashMap<String, InputRow>) -> Counters {
    let mut c = Counters::default();
    c.total = receipts.len();
    for receipt in receipts {
        let Some(input) = inputs.get(&receipt.input_hash) else {
            c.missing_input += 1;
            continue;
        };
        let mut ok = true;
        if !receipt.has_expected_entry || receipt.market_cap_usd.is_none() {
            c.missing_expected_entry += 1;
            ok = false;
        }
        if !receipt.has_expected_tokens_out {
            c.missing_expected_tokens_out += 1;
            ok = false;
        }
        if !receipt.has_min_tokens_out {
            c.missing_min_tokens_out += 1;
            ok = false;
        }
        if input
            .source_ix_data_base64
            .as_deref()
            .unwrap_or("")
            .is_empty()
        {
            c.missing_source_ix += 1;
            ok = false;
        }
        if input.token_program.as_deref().unwrap_or("").is_empty() {
            c.missing_token_program += 1;
            ok = false;
        }
        if input.creation_proven != Some(true) {
            c.creation_not_proven += 1;
            ok = false;
        }
        if !input.has_canonical_state {
            c.missing_canonical_state += 1;
            ok = false;
        }
        if input.mayhem_status.as_deref() != Some("clean") {
            c.mayhem_not_clean += 1;
            ok = false;
        }
        let venue = receipt
            .venue
            .as_deref()
            .or(input.canonical_venue.as_deref());
        if venue != Some("pump_fun") {
            c.non_pump_fun += 1;
            ok = false;
        }
        if ok {
            let _ = &receipt.mint;
            c.ok += 1;
        }
    }
    c
}

fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    format!("{digest:x}")
}
