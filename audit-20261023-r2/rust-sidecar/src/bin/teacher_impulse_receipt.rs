use std::{
    env,
    fs::{File, OpenOptions},
    io::{self, BufRead, BufReader, Seek, SeekFrom, Write},
    thread::sleep,
    time::Duration,
};

use anyhow::{Context, Result};
use pumpfun_sidecar::parity_core::{evaluate_decision, DecisionInput, StrategyConfig};

fn main() -> Result<()> {
    let args = Args::parse()?;
    let config = strategy_config_from_env()?;
    run(args, &config)
}

#[derive(Debug, Clone)]
struct Args {
    input: Option<String>,
    output: Option<String>,
    follow: bool,
    start_at_end: bool,
    truncate_output: bool,
    poll_ms: u64,
    max_receipts: Option<u64>,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut args = env::args().skip(1);
        let mut parsed = Self {
            input: None,
            output: None,
            follow: false,
            start_at_end: false,
            truncate_output: false,
            poll_ms: 100,
            max_receipts: None,
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--input" => parsed.input = Some(next_arg(&mut args, "--input")?),
                "--output" => parsed.output = Some(next_arg(&mut args, "--output")?),
                "--follow" => parsed.follow = true,
                "--start-at-end" => parsed.start_at_end = true,
                "--truncate-output" => parsed.truncate_output = true,
                "--poll-ms" => parsed.poll_ms = next_arg(&mut args, "--poll-ms")?.parse()?,
                "--max-receipts" => {
                    parsed.max_receipts = Some(next_arg(&mut args, "--max-receipts")?.parse()?)
                }
                "--help" | "-h" => {
                    println!(
                        "usage: teacher_impulse_receipt [--input path|-] [--output path] [--follow] [--start-at-end] [--poll-ms ms] [--max-receipts n]"
                    );
                    std::process::exit(0);
                }
                _ if parsed.input.is_none() => parsed.input = Some(arg),
                _ => anyhow::bail!("unknown argument: {arg}"),
            }
        }
        if parsed.follow && parsed.input.as_deref().unwrap_or("-") == "-" {
            anyhow::bail!("--follow requires --input path");
        }
        Ok(parsed)
    }
}

fn next_arg(args: &mut impl Iterator<Item = String>, name: &str) -> Result<String> {
    args.next()
        .with_context(|| format!("{name} requires a value"))
}

fn strategy_config_from_env() -> Result<StrategyConfig> {
    let mut config = StrategyConfig::default();
    if let Some(value) = optional_env_string("TEACHER_IMPULSE_STRATEGY") {
        config.strategy = value;
    }
    if let Some(value) = optional_env_string("TEACHER_IMPULSE_CONFIG_VERSION") {
        config.config_version = value;
    }
    config.min_market_cap_usd =
        optional_env_f64("TEACHER_IMPULSE_MIN_MARKET_CAP_USD")?.or(config.min_market_cap_usd);
    config.max_market_cap_usd =
        optional_env_f64("TEACHER_IMPULSE_MAX_MARKET_CAP_USD")?.or(config.max_market_cap_usd);
    config.max_creation_age_ms =
        optional_env_u64("TEACHER_IMPULSE_MAX_CREATION_AGE_MS")?.or(config.max_creation_age_ms);
    config.min_net_buy_sol_60 =
        optional_env_f64("TEACHER_IMPULSE_MIN_NET_BUY_SOL_60")?.or(config.min_net_buy_sol_60);
    config.max_sell_pressure_60 =
        optional_env_f64("TEACHER_IMPULSE_MAX_SELL_PRESSURE_60")?.or(config.max_sell_pressure_60);
    config.max_dominant_buy_share_60 =
        optional_env_f64("TEACHER_IMPULSE_MAX_DOMINANT_BUY_SHARE_60")?
            .or(config.max_dominant_buy_share_60);
    config.min_unique_wallets_60 =
        optional_env_u32("TEACHER_IMPULSE_MIN_UNIQUE_WALLETS_60")?.or(config.min_unique_wallets_60);
    config.max_first_buyer_quote_share_bps =
        optional_env_u32("TEACHER_IMPULSE_MAX_FIRST_BUYER_QUOTE_SHARE_BPS")?
            .or(config.max_first_buyer_quote_share_bps);
    if let Some(value) = optional_env_u64("TEACHER_IMPULSE_ROUND_TRIP_COST_BPS")? {
        config.round_trip_cost_bps = value;
    }
    Ok(config)
}

fn optional_env_string(name: &str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn optional_env_f64(name: &str) -> Result<Option<f64>> {
    optional_env_string(name)
        .map(|value| {
            value
                .parse()
                .with_context(|| format!("parse {name}={value}"))
        })
        .transpose()
}

fn optional_env_u64(name: &str) -> Result<Option<u64>> {
    optional_env_string(name)
        .map(|value| {
            value
                .parse()
                .with_context(|| format!("parse {name}={value}"))
        })
        .transpose()
}

fn optional_env_u32(name: &str) -> Result<Option<u32>> {
    optional_env_string(name)
        .map(|value| {
            value
                .parse()
                .with_context(|| format!("parse {name}={value}"))
        })
        .transpose()
}

fn run(args: Args, config: &StrategyConfig) -> Result<()> {
    if args.follow {
        follow_file(args, config)
    } else if let Some(path) = args.input.as_deref().filter(|path| *path != "-") {
        let file = File::open(path).with_context(|| format!("open decision input file {path}"))?;
        let writer = output_writer(args.output.as_deref(), args.truncate_output)?;
        emit_receipts(BufReader::new(file), writer, config, args.max_receipts).map(|_| ())
    } else {
        let stdin = io::stdin();
        let writer = output_writer(args.output.as_deref(), args.truncate_output)?;
        emit_receipts(stdin.lock(), writer, config, args.max_receipts).map(|_| ())
    }
}

fn follow_file(args: Args, config: &StrategyConfig) -> Result<()> {
    let input = args.input.as_ref().expect("follow input checked");
    let mut file = OpenOptions::new()
        .read(true)
        .open(input)
        .with_context(|| format!("open decision input file {input}"))?;
    if args.start_at_end {
        file.seek(SeekFrom::End(0))?;
    }
    let mut reader = BufReader::new(file);
    let mut writer = output_writer(args.output.as_deref(), args.truncate_output)?;
    let mut emitted = 0_u64;
    let mut carry = String::new();
    loop {
        carry.clear();
        let line_start = reader.stream_position()?;
        let bytes = reader.read_line(&mut carry)?;
        if bytes == 0 {
            if args.max_receipts.is_some_and(|max| emitted >= max) {
                return Ok(());
            }
            sleep(Duration::from_millis(args.poll_ms));
            continue;
        }
        if !carry.ends_with('\n') {
            reader.seek(SeekFrom::Start(line_start))?;
            sleep(Duration::from_millis(args.poll_ms));
            continue;
        }
        if carry.trim().is_empty() {
            continue;
        }
        emit_one(&carry, &mut writer, config, emitted + 1)?;
        emitted += 1;
        if args.max_receipts.is_some_and(|max| emitted >= max) {
            return Ok(());
        }
    }
}

fn output_writer(path: Option<&str>, truncate: bool) -> Result<Box<dyn Write>> {
    if let Some(path) = path {
        let file = OpenOptions::new()
            .create(true)
            .write(true)
            .append(!truncate)
            .truncate(truncate)
            .open(path)
            .with_context(|| format!("open output receipt file {path}"))?;
        Ok(Box::new(file))
    } else {
        Ok(Box::new(io::stdout()))
    }
}

fn emit_receipts<R: BufRead, W: Write>(
    reader: R,
    mut writer: W,
    config: &StrategyConfig,
    max_receipts: Option<u64>,
) -> Result<u64> {
    let mut emitted = 0_u64;
    for (line_no, line) in reader.lines().enumerate() {
        let line = line.with_context(|| format!("read line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        emit_one(&line, &mut writer, config, (line_no + 1) as u64)?;
        emitted += 1;
        if max_receipts.is_some_and(|max| emitted >= max) {
            break;
        }
    }
    Ok(emitted)
}

fn emit_one<W: Write>(
    line: &str,
    writer: &mut W,
    config: &StrategyConfig,
    line_no: u64,
) -> Result<()> {
    let input: DecisionInput = serde_json::from_str(line)
        .with_context(|| format!("parse DecisionInput line {line_no}"))?;
    let receipt = evaluate_decision(&input, config);
    let mut out = serde_json::to_string(&receipt)?;
    out.push('\n');
    writer.write_all(out.as_bytes())?;
    writer.flush()?;
    Ok(())
}
