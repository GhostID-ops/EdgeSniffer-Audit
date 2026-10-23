use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader, Seek, SeekFrom, Write},
    thread::sleep,
    time::Duration,
};

use anyhow::{Context, Result};
use pumpfun_sidecar::parity_core::{evaluate_decision, DecisionInput, StrategyConfig};

fn main() -> Result<()> {
    let args = Args::parse()?;
    let config = strategy_config_from_env()?;
    eprintln!(
        "{}",
        serde_json::to_string(&serde_json::json!({
            "event": "teacher_impulse_receipt_started",
            "configVersion": config.config_version,
            "requireCreationProven": config.require_creation_proven,
            "requireCanonicalState": config.require_canonical_state,
            "minMarketCapUsd": config.min_market_cap_usd,
            "maxMarketCapUsd": config.max_market_cap_usd,
            "maxCreationAgeMs": config.max_creation_age_ms,
            "minNetBuySol60": config.min_net_buy_sol_60,
            "maxSellPressure60": config.max_sell_pressure_60,
            "maxDominantBuyShare60": config.max_dominant_buy_share_60,
            "minUniqueWallets60": config.min_unique_wallets_60,
            "maxFirstBuyerQuoteShareBps": config.max_first_buyer_quote_share_bps,
            "roundTripCostBps": config.round_trip_cost_bps,
        }))?
    );
    run(args, &config)
}

fn strategy_config_from_env() -> Result<StrategyConfig> {
    let mut config = StrategyConfig::default();
    if let Ok(value) = env::var("TEACHER_IMPULSE_STRATEGY") {
        config.strategy = value;
    }
    if let Ok(value) = env::var("TEACHER_IMPULSE_CONFIG_VERSION") {
        config.config_version = value;
    }
    if let Some(value) = env_bool("TEACHER_IMPULSE_REQUIRE_CREATION_PROVEN")? {
        config.require_creation_proven = value;
    }
    if let Some(value) = env_bool("TEACHER_IMPULSE_REQUIRE_CANONICAL_STATE")? {
        config.require_canonical_state = value;
    }
    if let Some(value) = env_optional_f64("TEACHER_IMPULSE_MIN_MARKET_CAP_USD")? {
        config.min_market_cap_usd = value;
    }
    if let Some(value) = env_optional_f64("TEACHER_IMPULSE_MAX_MARKET_CAP_USD")? {
        config.max_market_cap_usd = value;
    }
    if let Some(value) = env_optional_u64("TEACHER_IMPULSE_MAX_CREATION_AGE_MS")? {
        config.max_creation_age_ms = value;
    }
    if let Some(value) = env_optional_f64("TEACHER_IMPULSE_MIN_NET_BUY_SOL_60")? {
        config.min_net_buy_sol_60 = value;
    }
    if let Some(value) = env_optional_f64("TEACHER_IMPULSE_MAX_SELL_PRESSURE_60")? {
        config.max_sell_pressure_60 = value;
    }
    if let Some(value) = env_optional_f64("TEACHER_IMPULSE_MAX_DOMINANT_BUY_SHARE_60")? {
        config.max_dominant_buy_share_60 = value;
    }
    if let Some(value) = env_optional_u32("TEACHER_IMPULSE_MIN_UNIQUE_WALLETS_60")? {
        config.min_unique_wallets_60 = value;
    }
    if let Some(value) = env_optional_u32("TEACHER_IMPULSE_MAX_FIRST_BUYER_QUOTE_SHARE_BPS")? {
        config.max_first_buyer_quote_share_bps = value;
    }
    if let Ok(value) = env::var("TEACHER_IMPULSE_ROUND_TRIP_COST_BPS") {
        config.round_trip_cost_bps = value
            .parse()
            .with_context(|| "parse TEACHER_IMPULSE_ROUND_TRIP_COST_BPS")?;
    }
    Ok(config)
}

fn env_bool(name: &str) -> Result<Option<bool>> {
    let Ok(value) = env::var(name) else {
        return Ok(None);
    };
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(Some(true)),
        "0" | "false" | "no" | "off" => Ok(Some(false)),
        _ => anyhow::bail!("{name} must be boolean, got {value:?}"),
    }
}

fn env_optional_f64(name: &str) -> Result<Option<Option<f64>>> {
    let Ok(value) = env::var(name) else {
        return Ok(None);
    };
    if env_none(&value) {
        return Ok(Some(None));
    }
    Ok(Some(Some(
        value
            .trim()
            .parse()
            .with_context(|| format!("parse {name}"))?,
    )))
}

fn env_optional_u64(name: &str) -> Result<Option<Option<u64>>> {
    let Ok(value) = env::var(name) else {
        return Ok(None);
    };
    if env_none(&value) {
        return Ok(Some(None));
    }
    Ok(Some(Some(
        value
            .trim()
            .parse()
            .with_context(|| format!("parse {name}"))?,
    )))
}

fn env_optional_u32(name: &str) -> Result<Option<Option<u32>>> {
    let Ok(value) = env::var(name) else {
        return Ok(None);
    };
    if env_none(&value) {
        return Ok(Some(None));
    }
    Ok(Some(Some(
        value
            .trim()
            .parse()
            .with_context(|| format!("parse {name}"))?,
    )))
}

fn env_none(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "" | "none" | "null" | "off" | "disabled"
    )
}

#[derive(Debug, Clone)]
struct Args {
    input: Option<String>,
    output: Option<String>,
    follow: bool,
    start_at_end: bool,
    poll_ms: u64,
    max_receipts: Option<u64>,
    create_input_if_missing: bool,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut args = env::args().skip(1);
        let mut parsed = Self {
            input: None,
            output: None,
            follow: false,
            start_at_end: false,
            poll_ms: 100,
            max_receipts: None,
            create_input_if_missing: false,
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--input" => parsed.input = Some(next_arg(&mut args, "--input")?),
                "--output" => parsed.output = Some(next_arg(&mut args, "--output")?),
                "--follow" => parsed.follow = true,
                "--start-at-end" => parsed.start_at_end = true,
                "--poll-ms" => parsed.poll_ms = next_arg(&mut args, "--poll-ms")?.parse()?,
                "--max-receipts" => {
                    parsed.max_receipts = Some(next_arg(&mut args, "--max-receipts")?.parse()?)
                }
                "--create-input-if-missing" => parsed.create_input_if_missing = true,
                "--help" | "-h" => {
                    println!(
                        "usage: teacher_impulse_receipt [--input path|-] [--output path] [--follow] [--start-at-end] [--poll-ms ms] [--max-receipts n] [--create-input-if-missing]"
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

fn run(args: Args, config: &StrategyConfig) -> Result<()> {
    if args.follow {
        follow_file(args, config)
    } else if let Some(path) = args.input.as_deref().filter(|path| *path != "-") {
        let file = File::open(path).with_context(|| format!("open decision input file {path}"))?;
        let writer = output_writer(args.output.as_deref())?;
        emit_receipts(BufReader::new(file), writer, config, args.max_receipts).map(|_| ())
    } else {
        let stdin = io::stdin();
        let writer = output_writer(args.output.as_deref())?;
        emit_receipts(stdin.lock(), writer, config, args.max_receipts).map(|_| ())
    }
}

fn follow_file(args: Args, config: &StrategyConfig) -> Result<()> {
    let input = args.input.as_ref().expect("follow input checked");
    if args.create_input_if_missing {
        if let Some(parent) = std::path::Path::new(input).parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("create decision input directory {}", parent.display()))?;
        }
        OpenOptions::new()
            .create(true)
            .append(true)
            .open(input)
            .with_context(|| format!("create decision input file {input}"))?;
    }
    let mut file = OpenOptions::new()
        .read(true)
        .open(input)
        .with_context(|| format!("open decision input file {input}"))?;
    if args.start_at_end {
        file.seek(SeekFrom::End(0))?;
    }
    let mut reader = BufReader::new(file);
    let mut writer = output_writer(args.output.as_deref())?;
    let mut emitted = 0_u64;
    let mut carry = String::new();
    let mut pending = String::new();
    loop {
        carry.clear();
        let bytes = reader.read_line(&mut carry)?;
        if bytes == 0 {
            if args.max_receipts.is_some_and(|max| emitted >= max) {
                return Ok(());
            }
            sleep(Duration::from_millis(args.poll_ms));
            continue;
        }
        if carry.trim().is_empty() {
            continue;
        }
        pending.push_str(&carry);
        if !pending.ends_with('\n') {
            sleep(Duration::from_millis(args.poll_ms));
            continue;
        }
        emit_one(&pending, &mut writer, config, emitted + 1)?;
        pending.clear();
        emitted += 1;
        if args.max_receipts.is_some_and(|max| emitted >= max) {
            return Ok(());
        }
    }
}

fn output_writer(path: Option<&str>) -> Result<Box<dyn Write>> {
    if let Some(path) = path {
        if let Some(parent) = std::path::Path::new(path).parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("create output receipt directory {}", parent.display()))?;
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
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
    writeln!(writer, "{}", serde_json::to_string(&receipt)?)?;
    writer.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn strategy_config_reads_child_lane_env_overrides() {
        let _guard = ENV_LOCK.lock().expect("env lock poisoned");
        clear_teacher_impulse_env();
        env::set_var(
            "TEACHER_IMPULSE_CONFIG_VERSION",
            "teacher-impulse-parity-v1-child-mc5000-dom55-test",
        );
        env::set_var("TEACHER_IMPULSE_MAX_MARKET_CAP_USD", "5000");
        env::set_var("TEACHER_IMPULSE_MAX_DOMINANT_BUY_SHARE_60", "0.55");
        env::set_var("TEACHER_IMPULSE_MAX_FIRST_BUYER_QUOTE_SHARE_BPS", "5000");

        let config = strategy_config_from_env().unwrap();

        assert_eq!(
            config.config_version,
            "teacher-impulse-parity-v1-child-mc5000-dom55-test"
        );
        assert_eq!(config.max_market_cap_usd, Some(5000.0));
        assert_eq!(config.max_dominant_buy_share_60, Some(0.55));
        assert_eq!(config.max_first_buyer_quote_share_bps, Some(5000));
        clear_teacher_impulse_env();
    }

    #[test]
    fn strategy_config_env_can_disable_optional_gates() {
        let _guard = ENV_LOCK.lock().expect("env lock poisoned");
        clear_teacher_impulse_env();
        env::set_var("TEACHER_IMPULSE_MAX_MARKET_CAP_USD", "none");
        env::set_var("TEACHER_IMPULSE_MAX_CREATION_AGE_MS", "off");

        let config = strategy_config_from_env().unwrap();

        assert_eq!(config.max_market_cap_usd, None);
        assert_eq!(config.max_creation_age_ms, None);
        clear_teacher_impulse_env();
    }

    fn clear_teacher_impulse_env() {
        for key in [
            "TEACHER_IMPULSE_STRATEGY",
            "TEACHER_IMPULSE_CONFIG_VERSION",
            "TEACHER_IMPULSE_REQUIRE_CREATION_PROVEN",
            "TEACHER_IMPULSE_REQUIRE_CANONICAL_STATE",
            "TEACHER_IMPULSE_MIN_MARKET_CAP_USD",
            "TEACHER_IMPULSE_MAX_MARKET_CAP_USD",
            "TEACHER_IMPULSE_MAX_CREATION_AGE_MS",
            "TEACHER_IMPULSE_MIN_NET_BUY_SOL_60",
            "TEACHER_IMPULSE_MAX_SELL_PRESSURE_60",
            "TEACHER_IMPULSE_MAX_DOMINANT_BUY_SHARE_60",
            "TEACHER_IMPULSE_MIN_UNIQUE_WALLETS_60",
            "TEACHER_IMPULSE_MAX_FIRST_BUYER_QUOTE_SHARE_BPS",
            "TEACHER_IMPULSE_ROUND_TRIP_COST_BPS",
        ] {
            env::remove_var(key);
        }
    }
}
