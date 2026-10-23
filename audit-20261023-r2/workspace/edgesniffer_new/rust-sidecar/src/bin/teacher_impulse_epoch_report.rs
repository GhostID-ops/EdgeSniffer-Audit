use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    fs::{self, File},
    io::{self, BufRead, BufReader, Write},
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result};
use pumpfun_sidecar::parity_core::{DecisionInput, DecisionReceipt, DecisionStatus};
use serde::Serialize;
use serde_json::Value;

fn main() -> Result<()> {
    let args = Args::parse()?;
    let report = build_report(&args)?;
    if let Some(path) = args.output_json.as_deref() {
        write_file(path, serde_json::to_string_pretty(&report)?.as_bytes())?;
    }
    let mut markdown = Vec::new();
    write_markdown(&report, &mut markdown)?;
    if let Some(path) = args.output_markdown.as_deref() {
        write_file(path, &markdown)?;
    } else {
        io::stdout().write_all(&markdown)?;
    }
    Ok(())
}

#[derive(Debug, Clone)]
struct Args {
    candidate_source: Option<String>,
    decision_inputs: Option<String>,
    decision_receipts: Option<String>,
    output_markdown: Option<String>,
    output_json: Option<String>,
    epoch_start_ms: Option<u64>,
    epoch_end_ms: Option<u64>,
    config_epoch: String,
    allow_missing: bool,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut args = env::args().skip(1);
        let mut parsed = Self {
            candidate_source: None,
            decision_inputs: None,
            decision_receipts: None,
            output_markdown: None,
            output_json: None,
            epoch_start_ms: None,
            epoch_end_ms: None,
            config_epoch: "manual".to_string(),
            allow_missing: false,
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--candidate-source" => {
                    parsed.candidate_source = Some(next_arg(&mut args, "--candidate-source")?)
                }
                "--decision-inputs" => {
                    parsed.decision_inputs = Some(next_arg(&mut args, "--decision-inputs")?)
                }
                "--decision-receipts" => {
                    parsed.decision_receipts = Some(next_arg(&mut args, "--decision-receipts")?)
                }
                "--output-markdown" => {
                    parsed.output_markdown = Some(next_arg(&mut args, "--output-markdown")?)
                }
                "--output-json" => parsed.output_json = Some(next_arg(&mut args, "--output-json")?),
                "--epoch-start-ms" => {
                    parsed.epoch_start_ms = Some(next_arg(&mut args, "--epoch-start-ms")?.parse()?)
                }
                "--epoch-end-ms" => {
                    parsed.epoch_end_ms = Some(next_arg(&mut args, "--epoch-end-ms")?.parse()?)
                }
                "--config-epoch" => parsed.config_epoch = next_arg(&mut args, "--config-epoch")?,
                "--allow-missing" => parsed.allow_missing = true,
                "--help" | "-h" => {
                    println!("usage: teacher_impulse_epoch_report [--candidate-source path] [--decision-inputs path] [--decision-receipts path] [--epoch-start-ms ms] [--epoch-end-ms ms] [--config-epoch name] [--allow-missing] [--output-markdown path] [--output-json path]");
                    std::process::exit(0);
                }
                _ => anyhow::bail!("unknown argument: {arg}"),
            }
        }
        Ok(parsed)
    }
}

fn next_arg(args: &mut impl Iterator<Item = String>, name: &str) -> Result<String> {
    args.next()
        .with_context(|| format!("{name} requires a value"))
}

#[derive(Debug, Default, Serialize)]
struct EpochReport {
    generated_at_ms: u64,
    config_epoch: String,
    strategy: String,
    files: FileSection,
    funnel: Funnel,
    reasons: BTreeMap<String, u64>,
    unavailable_fields: BTreeMap<String, u64>,
    mayhem_source: BTreeMap<String, u64>,
    diagnostic_arms: BTreeMap<String, ArmCounts>,
    accepted_market_cap: MarketCapSummary,
    parity_status: ParityStatus,
}

#[derive(Debug, Default, Serialize)]
struct FileSection {
    candidate_source: Option<String>,
    decision_inputs: Option<String>,
    decision_receipts: Option<String>,
}

#[derive(Debug, Default, Serialize)]
struct Funnel {
    source_rows: u64,
    source_unique_mints: u64,
    decision_inputs: u64,
    decision_input_unique_mints: u64,
    receipts_total: u64,
    receipts_unique_mints: u64,
    accepted: u64,
    rejected: u64,
    unavailable: u64,
    receipt_max_gap_ms: Option<u64>,
}

#[derive(Debug, Default, Serialize)]
struct ArmCounts {
    decision_inputs: u64,
    unique_mints: u64,
}

#[derive(Debug, Default, Serialize)]
struct MarketCapSummary {
    count: u64,
    min: Option<f64>,
    max: Option<f64>,
    avg: Option<f64>,
}

#[derive(Debug, Serialize)]
struct ParityStatus {
    mode: &'static str,
    live_shadow_comparison: &'static str,
    note: &'static str,
}

impl Default for ParityStatus {
    fn default() -> Self {
        Self {
            mode: "no_submit",
            live_shadow_comparison: "not_applicable_until_observed_execution_receipts_exist",
            note: "This report validates candidate and receipt plumbing, not live profitability.",
        }
    }
}

fn build_report(args: &Args) -> Result<EpochReport> {
    let mut report = EpochReport {
        generated_at_ms: now_ms(),
        config_epoch: args.config_epoch.clone(),
        strategy: "teacher-impulse-parity-v1".to_string(),
        files: FileSection {
            candidate_source: args.candidate_source.clone(),
            decision_inputs: args.decision_inputs.clone(),
            decision_receipts: args.decision_receipts.clone(),
        },
        ..EpochReport::default()
    };

    let mut source_mints = BTreeSet::new();
    if let Some(path) = args.candidate_source.as_deref() {
        read_json_lines(path, args, |row| {
            report.funnel.source_rows += 1;
            if let Some(mint) = string_at_any(
                row,
                &[&["mint"], &["candidate", "mint"], &["signal", "mint"]],
            ) {
                source_mints.insert(mint);
            }
            let mayhem = mayhem_class(row);
            *report.mayhem_source.entry(mayhem).or_default() += 1;
            Ok(())
        })?;
    }
    report.funnel.source_unique_mints = source_mints.len() as u64;

    let mut input_mints = BTreeSet::new();
    let mut arm_mints: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    if let Some(path) = args.decision_inputs.as_deref() {
        read_json_lines(path, args, |row| {
            let input: DecisionInput = serde_json::from_value(row.clone())?;
            report.funnel.decision_inputs += 1;
            input_mints.insert(input.candidate.mint.clone());
            for arm in diagnostic_arms(&input) {
                let counts = report.diagnostic_arms.entry(arm.clone()).or_default();
                counts.decision_inputs += 1;
                arm_mints
                    .entry(arm)
                    .or_default()
                    .insert(input.candidate.mint.clone());
            }
            Ok(())
        })?;
    }
    report.funnel.decision_input_unique_mints = input_mints.len() as u64;
    for (arm, mints) in arm_mints {
        if let Some(counts) = report.diagnostic_arms.get_mut(&arm) {
            counts.unique_mints = mints.len() as u64;
        }
    }

    let mut receipt_mints = BTreeSet::new();
    let mut receipt_times = Vec::new();
    let mut accepted_mc_sum = 0.0_f64;
    if let Some(path) = args.decision_receipts.as_deref() {
        read_json_lines(path, args, |row| {
            let receipt: DecisionReceipt = serde_json::from_value(row.clone())?;
            report.funnel.receipts_total += 1;
            receipt_mints.insert(receipt.mint.clone());
            if let Some(ts) = receipt_timestamp_ms(&receipt) {
                receipt_times.push(ts);
            }
            match receipt.status {
                DecisionStatus::Accepted => {
                    report.funnel.accepted += 1;
                    if let Some(mc) = receipt.calculated_market_cap_usd {
                        report.accepted_market_cap.count += 1;
                        report.accepted_market_cap.min =
                            Some(report.accepted_market_cap.min.map_or(mc, |v| v.min(mc)));
                        report.accepted_market_cap.max =
                            Some(report.accepted_market_cap.max.map_or(mc, |v| v.max(mc)));
                        accepted_mc_sum += mc;
                    }
                }
                DecisionStatus::Rejected => report.funnel.rejected += 1,
                DecisionStatus::Unavailable => report.funnel.unavailable += 1,
            }
            for reason in receipt.reason_codes {
                *report.reasons.entry(reason).or_default() += 1;
            }
            for field in receipt.unavailable_fields {
                *report.unavailable_fields.entry(field).or_default() += 1;
            }
            Ok(())
        })?;
    }
    report.funnel.receipts_unique_mints = receipt_mints.len() as u64;
    if report.accepted_market_cap.count > 0 {
        report.accepted_market_cap.avg =
            Some(accepted_mc_sum / report.accepted_market_cap.count as f64);
    }
    receipt_times.sort_unstable();
    report.funnel.receipt_max_gap_ms = receipt_times
        .windows(2)
        .map(|pair| pair[1].saturating_sub(pair[0]))
        .max();

    Ok(report)
}

fn read_json_lines<F>(path: &str, args: &Args, mut on_row: F) -> Result<()>
where
    F: FnMut(&Value) -> Result<()>,
{
    let file = match File::open(path) {
        Ok(file) => file,
        Err(err) if args.allow_missing && err.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(err) => return Err(err).with_context(|| format!("open {path}")),
    };
    for (line_no, line) in BufReader::new(file).lines().enumerate() {
        let line = line.with_context(|| format!("read {path}:{}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let row: Value =
            serde_json::from_str(&line).with_context(|| format!("parse {path}:{}", line_no + 1))?;
        if !inside_epoch(&row, args) {
            continue;
        }
        on_row(&row)?;
    }
    Ok(())
}

fn inside_epoch(row: &Value, args: &Args) -> bool {
    let ts = row_ts_ms(row);
    if let (Some(start), Some(ts)) = (args.epoch_start_ms, ts) {
        if ts < start {
            return false;
        }
    }
    if let (Some(end), Some(ts)) = (args.epoch_end_ms, ts) {
        if ts > end {
            return false;
        }
    }
    true
}

fn row_ts_ms(row: &Value) -> Option<u64> {
    u64_at_any(
        row,
        &[
            &["provider", "candidate_ready_ts_ms"],
            &["provider", "local_receive_ts_ms"],
            &["candidateReadyTsMs"],
            &["candidate_ready_ts_ms"],
            &["selectedAtMs"],
            &["selected_at_ms"],
            &["evalAtMs"],
            &["sourceEventAtMs"],
            &["observedAtMs"],
            &["observed_at_ms"],
            &["signal", "triggerReceivedAtMs"],
            &["tsMs"],
            &["ts_ms"],
        ],
    )
}

fn receipt_timestamp_ms(receipt: &DecisionReceipt) -> Option<u64> {
    receipt
        .observed_execution
        .submitted_at_ms
        .or(receipt.observed_execution.landed_at_ms)
        .or(receipt.observed_execution.first_inventory_proof_at_ms)
}

fn mayhem_class(row: &Value) -> String {
    if bool_at_any(
        row,
        &[
            &["mayhemHardBlock"],
            &["mayhem_hard_block"],
            &["isMayhem"],
            &["features", "isMayhem"],
        ],
    )
    .unwrap_or(false)
    {
        "hard_block".to_string()
    } else if bool_at_any(
        row,
        &[
            &["mayhemUnavailable"],
            &["mayhem_unavailable"],
            &["isWeakMayhemRecovered"],
            &["features", "isWeakMayhemRecovered"],
        ],
    )
    .unwrap_or(false)
        || string_at_any(
            row,
            &[
                &["mayhemBlockReason"],
                &["features", "mayhemBlockReason"],
                &["mayhem", "reason"],
            ],
        )
        .is_some_and(|reason| reason == "mayhem_source_check_unavailable")
    {
        "unavailable".to_string()
    } else if bool_at_any(row, &[&["mayhemClean"], &["mayhem_clean"], &["isClean"]])
        .unwrap_or(false)
        || string_at_any(row, &[&["mayhemStatus"], &["mayhem", "status"]])
            .is_some_and(|status| status == "clean")
    {
        "clean".to_string()
    } else {
        "unknown".to_string()
    }
}

fn diagnostic_arms(input: &DecisionInput) -> Vec<String> {
    let mut arms = Vec::new();
    let mc = input
        .canonical_state
        .as_ref()
        .and_then(|state| state.market_cap_usd)
        .or(input.candidate.market_cap_usd);
    let net = input.candidate.net_buy_sol_60;
    if mc.is_some_and(|value| value <= 2_200.0) {
        arms.push("direct_mc_le_2200".to_string());
    }
    if mc.is_some_and(|value| value <= 2_500.0) {
        arms.push("direct_mc_le_2500".to_string());
    }
    if mc.is_some_and(|value| value <= 3_000.0) && net.is_some_and(|value| value >= 7.0) {
        arms.push("direct_mc_le_3000_net_ge_7".to_string());
    }
    if mc.is_some_and(|value| value <= 3_000.0) && net.is_some_and(|value| value >= 8.0) {
        arms.push("direct_mc_le_3000_net_ge_8".to_string());
    }
    if mc.is_some_and(|value| (3_000.0..=3_200.0).contains(&value))
        && net.is_some_and(|value| value >= 7.0)
    {
        arms.push("direct_mc_3000_3200_net_ge_7".to_string());
    }
    arms
}

fn write_markdown<W: Write>(report: &EpochReport, writer: &mut W) -> Result<()> {
    writeln!(writer, "# Teacher Impulse Epoch Report")?;
    writeln!(writer)?;
    writeln!(writer, "| Contract | Value |")?;
    writeln!(writer, "|---|---|")?;
    writeln!(writer, "| Strategy | `{}` |", report.strategy)?;
    writeln!(writer, "| Config epoch | `{}` |", report.config_epoch)?;
    writeln!(writer, "| Mode | `{}` |", report.parity_status.mode)?;
    writeln!(
        writer,
        "| Live-shadow comparison | `{}` |",
        report.parity_status.live_shadow_comparison
    )?;
    writeln!(
        writer,
        "| Candidate source | `{}` |",
        opt(report.files.candidate_source.as_deref())
    )?;
    writeln!(
        writer,
        "| Decision inputs | `{}` |",
        opt(report.files.decision_inputs.as_deref())
    )?;
    writeln!(
        writer,
        "| Decision receipts | `{}` |",
        opt(report.files.decision_receipts.as_deref())
    )?;
    writeln!(writer)?;
    writeln!(writer, "## End-To-End Funnel")?;
    writeln!(writer)?;
    writeln!(writer, "| Metric | Value |")?;
    writeln!(writer, "|---|---:|")?;
    writeln!(writer, "| Source rows | {} |", report.funnel.source_rows)?;
    writeln!(
        writer,
        "| Source unique mints | {} |",
        report.funnel.source_unique_mints
    )?;
    writeln!(
        writer,
        "| Decision inputs | {} |",
        report.funnel.decision_inputs
    )?;
    writeln!(
        writer,
        "| Decision input unique mints | {} |",
        report.funnel.decision_input_unique_mints
    )?;
    writeln!(
        writer,
        "| Receipts total | {} |",
        report.funnel.receipts_total
    )?;
    writeln!(
        writer,
        "| Receipts unique mints | {} |",
        report.funnel.receipts_unique_mints
    )?;
    writeln!(writer, "| Accepted | {} |", report.funnel.accepted)?;
    writeln!(writer, "| Rejected | {} |", report.funnel.rejected)?;
    writeln!(writer, "| Unavailable | {} |", report.funnel.unavailable)?;
    writeln!(
        writer,
        "| Receipt max gap ms | {} |",
        report
            .funnel
            .receipt_max_gap_ms
            .map(|value| value.to_string())
            .unwrap_or_else(|| "n/a".to_string())
    )?;
    writeln!(writer)?;
    writeln!(writer, "## Accepted Market Cap")?;
    writeln!(writer)?;
    writeln!(writer, "| Metric | Value |")?;
    writeln!(writer, "|---|---:|")?;
    writeln!(writer, "| Count | {} |", report.accepted_market_cap.count)?;
    writeln!(
        writer,
        "| Min | {} |",
        fmt_f64(report.accepted_market_cap.min)
    )?;
    writeln!(
        writer,
        "| Avg | {} |",
        fmt_f64(report.accepted_market_cap.avg)
    )?;
    writeln!(
        writer,
        "| Max | {} |",
        fmt_f64(report.accepted_market_cap.max)
    )?;
    write_counts(writer, "Reason Counts", &report.reasons)?;
    write_counts(
        writer,
        "Unavailable Field Counts",
        &report.unavailable_fields,
    )?;
    write_counts(writer, "Mayhem Source Counts", &report.mayhem_source)?;
    writeln!(writer)?;
    writeln!(writer, "## Diagnostic Arms")?;
    writeln!(writer)?;
    if report.diagnostic_arms.is_empty() {
        writeln!(writer, "None.")?;
    } else {
        writeln!(writer, "| Arm | Decision Inputs | Unique Mints |")?;
        writeln!(writer, "|---|---:|---:|")?;
        for (arm, counts) in &report.diagnostic_arms {
            writeln!(
                writer,
                "| `{arm}` | {} | {} |",
                counts.decision_inputs, counts.unique_mints
            )?;
        }
    }
    writeln!(writer)?;
    writeln!(writer, "## Note")?;
    writeln!(writer)?;
    writeln!(writer, "{}", report.parity_status.note)?;
    Ok(())
}

fn write_counts<W: Write>(
    writer: &mut W,
    title: &str,
    counts: &BTreeMap<String, u64>,
) -> Result<()> {
    writeln!(writer)?;
    writeln!(writer, "## {title}")?;
    writeln!(writer)?;
    if counts.is_empty() {
        writeln!(writer, "None.")?;
    } else {
        writeln!(writer, "| Key | Count |")?;
        writeln!(writer, "|---|---:|")?;
        for (key, count) in counts {
            if key.starts_with("__source_unique_mints:") {
                continue;
            }
            writeln!(writer, "| `{key}` | {count} |")?;
        }
    }
    Ok(())
}

fn opt(value: Option<&str>) -> &str {
    value.unwrap_or("n/a")
}

fn fmt_f64(value: Option<f64>) -> String {
    value
        .map(|value| format!("{value:.2}"))
        .unwrap_or_else(|| "n/a".to_string())
}

fn write_file(path: &str, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = std::path::Path::new(path).parent() {
        fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    }
    fs::write(path, bytes).with_context(|| format!("write {path}"))
}

fn string_at_any(row: &Value, paths: &[&[&str]]) -> Option<String> {
    paths.iter().find_map(|path| {
        value_at(row, path).and_then(|value| match value {
            Value::String(raw) if !raw.trim().is_empty() => Some(raw.trim().to_string()),
            Value::Number(number) => Some(number.to_string()),
            _ => None,
        })
    })
}

fn bool_at_any(row: &Value, paths: &[&[&str]]) -> Option<bool> {
    paths.iter().find_map(|path| {
        value_at(row, path).and_then(|value| match value {
            Value::Bool(value) => Some(*value),
            Value::String(raw) => match raw.to_ascii_lowercase().as_str() {
                "true" | "1" | "yes" => Some(true),
                "false" | "0" | "no" => Some(false),
                _ => None,
            },
            _ => None,
        })
    })
}

fn u64_at_any(row: &Value, paths: &[&[&str]]) -> Option<u64> {
    paths.iter().find_map(|path| {
        value_at(row, path).and_then(|value| match value {
            Value::Number(number) => number.as_u64(),
            Value::String(raw) => raw.parse::<u64>().ok(),
            _ => None,
        })
    })
}

fn value_at<'a>(row: &'a Value, path: &[&str]) -> Option<&'a Value> {
    let mut cursor = row;
    for key in path {
        cursor = cursor.get(*key)?;
    }
    Some(cursor)
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
