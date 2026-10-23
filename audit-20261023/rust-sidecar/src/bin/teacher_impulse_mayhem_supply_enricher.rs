use std::{
    collections::HashSet,
    env,
    fs::OpenOptions,
    io::{BufRead, BufReader, Seek, SeekFrom, Write},
    thread::sleep,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose, Engine as _};
use pumpfun_sidecar::{
    parity_core::{MayhemEvidence, MayhemStatus, ProviderSource},
    teacher_impulse_emitter::{parse_observation_line, ObservationSide, TeacherImpulseObservation},
};
use reqwest::Client;
use serde_json::{json, Value};

const TOKEN_2022_PROGRAM_ID: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const MAYHEM_SUPPLY_RAW: u64 = 2_000_000_000_000_000;
const MAYHEM_SUPPLY_FLOOR_RAW: u64 = 1_900_000_000_000_000;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    let args = Args::parse()?;
    run(args).await
}

#[derive(Debug, Clone)]
struct Args {
    input: String,
    output: String,
    follow: bool,
    start_at_end: bool,
    poll_ms: u64,
    max_checks: Option<u64>,
    request_timeout_ms: u64,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut args = env::args().skip(1);
        let mut parsed = Self {
            input: String::new(),
            output: String::new(),
            follow: false,
            start_at_end: false,
            poll_ms: 250,
            max_checks: None,
            request_timeout_ms: env_u64("TEACHER_IMPULSE_MAYHEM_RPC_TIMEOUT_MS").unwrap_or(1500),
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--input" => parsed.input = next_arg(&mut args, "--input")?,
                "--output" => parsed.output = next_arg(&mut args, "--output")?,
                "--follow" => parsed.follow = true,
                "--start-at-end" => parsed.start_at_end = true,
                "--poll-ms" => parsed.poll_ms = next_arg(&mut args, "--poll-ms")?.parse()?,
                "--max-checks" => {
                    parsed.max_checks = Some(next_arg(&mut args, "--max-checks")?.parse()?)
                }
                "--request-timeout-ms" => {
                    parsed.request_timeout_ms =
                        next_arg(&mut args, "--request-timeout-ms")?.parse()?
                }
                "--help" | "-h" => {
                    println!("usage: teacher_impulse_mayhem_supply_enricher --input path --output path [--follow] [--start-at-end] [--poll-ms ms] [--max-checks n] [--request-timeout-ms ms]");
                    std::process::exit(0);
                }
                _ => bail!("unknown argument: {arg}"),
            }
        }
        if parsed.input.is_empty() {
            bail!("--input is required");
        }
        if parsed.output.is_empty() {
            bail!("--output is required");
        }
        Ok(parsed)
    }
}

fn next_arg(args: &mut impl Iterator<Item = String>, name: &str) -> Result<String> {
    args.next()
        .with_context(|| format!("{name} requires a value"))
}

async fn run(args: Args) -> Result<()> {
    let rpc_url = rpc_url_from_env()?;
    let client = Client::builder()
        .timeout(Duration::from_millis(args.request_timeout_ms.max(250)))
        .build()?;
    let mut input = OpenOptions::new()
        .read(true)
        .open(&args.input)
        .with_context(|| format!("open observation input {}", args.input))?;
    if args.start_at_end {
        input.seek(SeekFrom::End(0))?;
    }
    let mut reader = BufReader::new(input);
    let mut output = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&args.output)
        .with_context(|| format!("open observation output {}", args.output))?;
    let mut seen = HashSet::new();
    let mut checked = 0_u64;
    let mut line = String::new();

    loop {
        line.clear();
        let line_start = reader.stream_position()?;
        let bytes = reader.read_line(&mut line)?;
        if bytes == 0 {
            if !args.follow || args.max_checks.is_some_and(|max| checked >= max) {
                return Ok(());
            }
            sleep(Duration::from_millis(args.poll_ms.max(25)));
            continue;
        }
        if !line.ends_with('\n') {
            reader.seek(SeekFrom::Start(line_start))?;
            sleep(Duration::from_millis(args.poll_ms.max(25)));
            continue;
        }
        let observation = match parse_observation_line(&line, checked + 1) {
            Ok(observation) => observation,
            Err(_) => continue,
        };
        if !needs_supply_check(&observation) || !seen.insert(observation.mint.clone()) {
            continue;
        }
        checked = checked.saturating_add(1);
        let proof = check_mint_supply(&client, &rpc_url, &observation.mint).await;
        let state_observation = build_state_observation(&observation, proof);
        let mut out = serde_json::to_string(&state_observation)?;
        out.push('\n');
        output.write_all(out.as_bytes())?;
        output.flush()?;
        if args.max_checks.is_some_and(|max| checked >= max) {
            return Ok(());
        }
    }
}

fn needs_supply_check(observation: &TeacherImpulseObservation) -> bool {
    observation.mint.ends_with("pump")
        && observation.token_program.as_deref() == Some(TOKEN_2022_PROGRAM_ID)
        && matches!(
            observation.mayhem.as_ref().map(|mayhem| &mayhem.status),
            None | Some(MayhemStatus::Unknown) | Some(MayhemStatus::Unavailable)
        )
}

async fn check_mint_supply(client: &Client, rpc_url: &str, mint: &str) -> MayhemEvidence {
    let checked_at_ms = now_ms();
    let request = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [
            mint,
            {
                "encoding": "base64",
                "commitment": "processed"
            }
        ]
    });
    let response = match client.post(rpc_url).json(&request).send().await {
        Ok(response) => response,
        Err(err) => {
            return MayhemEvidence {
                status: MayhemStatus::Unavailable,
                reason: Some(format!("mint_supply_rpc_error:{err}")),
                checked_at_ms: Some(checked_at_ms),
            }
        }
    };
    let body = match response.json::<Value>().await {
        Ok(body) => body,
        Err(err) => {
            return MayhemEvidence {
                status: MayhemStatus::Unavailable,
                reason: Some(format!("mint_supply_rpc_json_error:{err}")),
                checked_at_ms: Some(checked_at_ms),
            }
        }
    };
    let owner = body
        .pointer("/result/value/owner")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if owner != TOKEN_2022_PROGRAM_ID {
        return MayhemEvidence {
            status: MayhemStatus::Clean,
            reason: Some("mint_owner_not_token2022".to_string()),
            checked_at_ms: Some(checked_at_ms),
        };
    }
    let Some(encoded) = body.pointer("/result/value/data/0").and_then(Value::as_str) else {
        return MayhemEvidence {
            status: MayhemStatus::Unavailable,
            reason: Some("mint_supply_data_missing".to_string()),
            checked_at_ms: Some(checked_at_ms),
        };
    };
    let data = match general_purpose::STANDARD.decode(encoded) {
        Ok(data) => data,
        Err(err) => {
            return MayhemEvidence {
                status: MayhemStatus::Unavailable,
                reason: Some(format!("mint_supply_base64_decode_error:{err}")),
                checked_at_ms: Some(checked_at_ms),
            }
        }
    };
    let Some(supply) = read_u64_le(&data, 36) else {
        return MayhemEvidence {
            status: MayhemStatus::Unavailable,
            reason: Some("mint_supply_layout_missing".to_string()),
            checked_at_ms: Some(checked_at_ms),
        };
    };
    if supply >= MAYHEM_SUPPLY_FLOOR_RAW {
        MayhemEvidence {
            status: MayhemStatus::HardBlock,
            reason: Some(format!("mint_supply_mayhem:{supply}")),
            checked_at_ms: Some(checked_at_ms),
        }
    } else {
        MayhemEvidence {
            status: MayhemStatus::Clean,
            reason: Some(format!(
                "mint_supply_below_mayhem:{supply}:threshold:{MAYHEM_SUPPLY_RAW}"
            )),
            checked_at_ms: Some(checked_at_ms),
        }
    }
}

fn build_state_observation(
    source_observation: &TeacherImpulseObservation,
    mayhem: MayhemEvidence,
) -> TeacherImpulseObservation {
    let now = mayhem.checked_at_ms.unwrap_or_else(now_ms);
    TeacherImpulseObservation {
        source: ProviderSource::Rpc,
        observation_id: Some(format!("mayhem-supply:{}:{now}", source_observation.mint)),
        raw_record_identity: Some(format!("mayhem-supply:{}:{now}", source_observation.mint)),
        raw_record_hash: None,
        raw_record: Some(json!({
            "kind": "teacherImpulseMayhemSupplyProof",
            "mint": source_observation.mint,
            "tokenProgram": source_observation.token_program,
            "sourceObservationId": source_observation.observation_id,
        })),
        source_slot: source_observation.source_slot,
        source_transaction_index: source_observation.source_transaction_index,
        provider_sequence: Some(format!("mayhem-supply:{}:{now}", source_observation.mint)),
        local_receive_ts_ms: now,
        decode_complete_ts_ms: Some(now),
        transaction_signature: source_observation.transaction_signature.clone(),
        mint: source_observation.mint.clone(),
        token_program: source_observation.token_program.clone(),
        source_ix_data_base64: None,
        creation_proven: None,
        creation_ts_ms: None,
        side: ObservationSide::State,
        quote_sol: None,
        quote_lamports: None,
        sell_quote_lamports: None,
        sell_token_amount_raw: None,
        wallet: None,
        canonical_state: None,
        mayhem: Some(mayhem),
        execution: None,
        extra: json!({
            "adapter": "teacher_impulse_mayhem_supply_enricher",
            "outsideHotPath": true,
        }),
    }
}

fn read_u64_le(data: &[u8], offset: usize) -> Option<u64> {
    let bytes = data.get(offset..offset + 8)?;
    let mut out = [0u8; 8];
    out.copy_from_slice(bytes);
    Some(u64::from_le_bytes(out))
}

fn rpc_url_from_env() -> Result<String> {
    for name in [
        "TEACHER_IMPULSE_MAYHEM_RPC_URL",
        "SECURE_RPC_URL",
        "RPC_URL",
        "READ_RPC_URL",
        "HELIUS_RPC_URL",
        "SOLANA_RPC_URL",
    ] {
        if let Ok(value) = env::var(name) {
            let trimmed = value.trim();
            if !trimmed.is_empty() {
                return Ok(trimmed.to_string());
            }
        }
    }
    bail!("missing RPC URL for teacher impulse Mayhem supply enricher")
}

fn env_u64(name: &str) -> Option<u64> {
    env::var(name)
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u64::MAX as u128) as u64
}
