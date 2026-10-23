use std::{
    cmp::Ordering,
    collections::{HashMap, HashSet},
    env,
    fs::File,
    io::{BufRead, BufReader},
};

use anyhow::{bail, Context, Result};
use serde_json::Value;
use sha2::{Digest, Sha256};

const DEFAULT_TPS: &[f64] = &[15.0, 20.0];
const DEFAULT_DELAYS: &[u64] = &[250, 500, 750, 1000];

fn main() -> Result<()> {
    let args = Args::parse()?;
    run(args)
}

#[derive(Debug, Clone)]
struct Args {
    receipts: Vec<LaneArg>,
    inputs: String,
    capsules: String,
    horizon_ms: u64,
    tp_pcts: Vec<f64>,
    sl_pct: f64,
    cost_bps: f64,
    delays_ms: Vec<u64>,
    max_mark_mc_usd: f64,
}

#[derive(Debug, Clone)]
struct LaneArg {
    name: String,
    path: String,
    derived: DerivedCut,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DerivedCut {
    None,
    Mc5000,
    Mc5000Dom55,
    Mc5000FirstBuyer5000,
}

#[derive(Debug, Clone)]
struct Entry {
    mint: String,
    input_hash: String,
    ready_ts_ms: u64,
    entry_mc_usd: f64,
    dominant_buy_share_60: Option<f64>,
    first_buyer_quote_share_bps: Option<f64>,
}

#[derive(Debug, Clone, Copy)]
struct Mark {
    ts_ms: u64,
    mc_usd: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Event {
    Win,
    Loss,
    Unresolved,
    NoFill,
}

#[derive(Debug, Clone)]
struct SimResult {
    event: Event,
    fill_mc_usd: Option<f64>,
    fill_delay_ms: Option<u64>,
    event_ms_after_fill: Option<u64>,
    entry_drift_pct: Option<f64>,
    pre_entry_shadow_sl: bool,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut receipts = Vec::new();
        let mut args = env::args().skip(1);
        let mut parsed = Self {
            receipts: Vec::new(),
            inputs: String::new(),
            capsules: String::new(),
            horizon_ms: 120_000,
            tp_pcts: DEFAULT_TPS.to_vec(),
            sl_pct: -20.0,
            cost_bps: 250.0,
            delays_ms: DEFAULT_DELAYS.to_vec(),
            max_mark_mc_usd: 500_000.0,
        };

        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--receipt-lane" => {
                    receipts.push(parse_lane(&next_arg(&mut args, "--receipt-lane")?)?)
                }
                "--inputs" => parsed.inputs = next_arg(&mut args, "--inputs")?,
                "--capsules" => parsed.capsules = next_arg(&mut args, "--capsules")?,
                "--horizon-ms" => {
                    parsed.horizon_ms = next_arg(&mut args, "--horizon-ms")?.parse()?
                }
                "--tp-pcts" => parsed.tp_pcts = parse_f64_list(&next_arg(&mut args, "--tp-pcts")?)?,
                "--sl-pct" => parsed.sl_pct = next_arg(&mut args, "--sl-pct")?.parse()?,
                "--cost-bps" => parsed.cost_bps = next_arg(&mut args, "--cost-bps")?.parse()?,
                "--delays-ms" => {
                    parsed.delays_ms = parse_u64_list(&next_arg(&mut args, "--delays-ms")?)?
                }
                "--max-mark-mc-usd" => {
                    parsed.max_mark_mc_usd = next_arg(&mut args, "--max-mark-mc-usd")?.parse()?
                }
                "--help" | "-h" => {
                    println!("usage: teacher_impulse_executable_parity_report --inputs path --capsules path --receipt-lane name=path [--receipt-lane name=path] [--receipt-lane name=path:derived=mc5000|mc5000_dom55|mc5000_fb5000] [--tp-pcts 15,20] [--sl-pct -20] [--delays-ms 250,500,750,1000] [--cost-bps 250] [--horizon-ms 120000] [--max-mark-mc-usd 500000]");
                    std::process::exit(0);
                }
                _ => bail!("unknown argument: {arg}"),
            }
        }

        if receipts.is_empty() {
            bail!("at least one --receipt-lane is required");
        }
        if parsed.inputs.is_empty() {
            bail!("--inputs is required");
        }
        if parsed.capsules.is_empty() {
            bail!("--capsules is required");
        }
        parsed.receipts = receipts;
        Ok(parsed)
    }
}

fn parse_lane(raw: &str) -> Result<LaneArg> {
    let (name, rest) = raw
        .split_once('=')
        .with_context(|| format!("lane must be name=path, got {raw}"))?;
    let (path, derived) = match rest.split_once(":derived=") {
        Some((path, derived)) => (path.to_string(), parse_derived(derived)?),
        None => (rest.to_string(), DerivedCut::None),
    };
    if name.trim().is_empty() || path.trim().is_empty() {
        bail!("lane name and path are required: {raw}");
    }
    Ok(LaneArg {
        name: name.to_string(),
        path,
        derived,
    })
}

fn parse_derived(raw: &str) -> Result<DerivedCut> {
    match raw {
        "none" => Ok(DerivedCut::None),
        "mc5000" => Ok(DerivedCut::Mc5000),
        "mc5000_dom55" => Ok(DerivedCut::Mc5000Dom55),
        "mc5000_fb5000" => Ok(DerivedCut::Mc5000FirstBuyer5000),
        _ => bail!("unknown derived cut: {raw}"),
    }
}

fn parse_f64_list(raw: &str) -> Result<Vec<f64>> {
    raw.split(',')
        .map(|part| part.trim().parse::<f64>().context("parse f64 list item"))
        .collect()
}

fn parse_u64_list(raw: &str) -> Result<Vec<u64>> {
    raw.split(',')
        .map(|part| part.trim().parse::<u64>().context("parse u64 list item"))
        .collect()
}

fn next_arg(args: &mut impl Iterator<Item = String>, name: &str) -> Result<String> {
    args.next()
        .with_context(|| format!("{name} requires a value"))
}

fn run(args: Args) -> Result<()> {
    let mut lanes = HashMap::<String, HashMap<String, Entry>>::new();
    let mut needed_hashes = HashSet::<String>::new();

    for lane in &args.receipts {
        let entries = load_receipt_entries(&lane.path)?;
        needed_hashes.extend(entries.values().map(|entry| entry.input_hash.clone()));
        lanes.insert(lane.name.clone(), entries);
    }

    hydrate_entries_from_inputs(&args.inputs, &needed_hashes, &mut lanes)?;
    apply_derived_cuts(&args.receipts, &mut lanes);

    let mint_filter = lanes
        .values()
        .flat_map(|entries| entries.keys().cloned())
        .collect::<HashSet<_>>();
    let marks = load_marks(&args.capsules, &mint_filter, args.max_mark_mc_usd)?;

    println!("# Teacher Impulse Executable-Delay Parity Report");
    println!();
    println!("Generated from reset Rust receipts, raw decision inputs, and forward capsule marks.");
    println!();
    println!("| Setting | Value |");
    println!("|---|---:|");
    println!("| Horizon ms after fill | {} |", args.horizon_ms);
    println!("| SL threshold | {:.2}% |", args.sl_pct);
    println!(
        "| Cost model | {:.0} bps / resolved round trip |",
        args.cost_bps
    );
    println!("| Max forward MC mark | ${:.0} |", args.max_mark_mc_usd);
    println!("| Entry delays tested | {:?} |", args.delays_ms);
    println!();

    for lane in &args.receipts {
        let Some(entries) = lanes.get(&lane.name) else {
            continue;
        };
        print_lane_report(&args, lane, entries, &marks);
    }

    Ok(())
}

fn load_receipt_entries(path: &str) -> Result<HashMap<String, Entry>> {
    let file = File::open(path).with_context(|| format!("open receipts {path}"))?;
    let mut out = HashMap::<String, Entry>::new();
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
        let Some(entry_mc_usd) = value
            .get("calculated_market_cap_usd")
            .and_then(Value::as_f64)
            .filter(|value| value.is_finite() && *value > 0.0)
        else {
            continue;
        };
        out.entry(mint.to_string()).or_insert(Entry {
            mint: mint.to_string(),
            input_hash: input_hash.to_string(),
            ready_ts_ms: 0,
            entry_mc_usd,
            dominant_buy_share_60: None,
            first_buyer_quote_share_bps: None,
        });
    }
    Ok(out)
}

fn hydrate_entries_from_inputs(
    path: &str,
    needed_hashes: &HashSet<String>,
    lanes: &mut HashMap<String, HashMap<String, Entry>>,
) -> Result<()> {
    let file = File::open(path).with_context(|| format!("open inputs {path}"))?;
    let mut found = HashMap::<String, Value>::new();
    for (line_no, line) in BufReader::new(file).lines().enumerate() {
        let line = line.with_context(|| format!("read input line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let hash = sha256_hex(line.as_bytes());
        if !needed_hashes.contains(&hash) {
            continue;
        }
        let value: Value = serde_json::from_str(&line)
            .with_context(|| format!("parse decision input line {}", line_no + 1))?;
        found.insert(hash, value);
    }

    for entries in lanes.values_mut() {
        entries.retain(|_, entry| {
            let Some(input) = found.get(&entry.input_hash) else {
                return false;
            };
            let provider = input.get("provider").unwrap_or(&Value::Null);
            let candidate = input.get("candidate").unwrap_or(&Value::Null);
            let ready_ts_ms = provider
                .get("candidate_ready_ts_ms")
                .and_then(Value::as_u64)
                .or_else(|| {
                    provider
                        .get("decode_complete_ts_ms")
                        .and_then(Value::as_u64)
                })
                .or_else(|| provider.get("local_receive_ts_ms").and_then(Value::as_u64));
            let Some(ready_ts_ms) = ready_ts_ms else {
                return false;
            };
            entry.ready_ts_ms = ready_ts_ms;
            entry.dominant_buy_share_60 = candidate
                .get("dominant_buy_share_60")
                .and_then(Value::as_f64)
                .or_else(|| candidate.get("dominantBuyShare60").and_then(Value::as_f64));
            entry.first_buyer_quote_share_bps = candidate
                .get("first_buyer_quote_share_bps")
                .and_then(Value::as_f64)
                .or_else(|| {
                    candidate
                        .get("firstBuyerQuoteShareBps")
                        .and_then(Value::as_f64)
                });
            true
        });
    }
    Ok(())
}

fn apply_derived_cuts(lane_args: &[LaneArg], lanes: &mut HashMap<String, HashMap<String, Entry>>) {
    for lane in lane_args {
        if lane.derived == DerivedCut::None {
            continue;
        }
        let Some(entries) = lanes.get_mut(&lane.name) else {
            continue;
        };
        entries.retain(|_, entry| match lane.derived {
            DerivedCut::None => true,
            DerivedCut::Mc5000 => entry.entry_mc_usd <= 5_000.0,
            DerivedCut::Mc5000Dom55 => {
                entry.entry_mc_usd <= 5_000.0
                    && entry.dominant_buy_share_60.is_some_and(|v| v <= 0.55)
            }
            DerivedCut::Mc5000FirstBuyer5000 => {
                entry.entry_mc_usd <= 5_000.0
                    && entry
                        .first_buyer_quote_share_bps
                        .is_some_and(|v| v <= 5_000.0)
            }
        });
    }
}

fn load_marks(
    path: &str,
    mint_filter: &HashSet<String>,
    max_mark_mc_usd: f64,
) -> Result<HashMap<String, Vec<Mark>>> {
    let file = File::open(path).with_context(|| format!("open capsules {path}"))?;
    let mut out = HashMap::<String, Vec<Mark>>::new();
    for (line_no, line) in BufReader::new(file).lines().enumerate() {
        let line = line.with_context(|| format!("read capsule line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let Ok(value) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let candidate = value.get("candidate").unwrap_or(&Value::Null);
        let Some(mint) = candidate.get("mint").and_then(Value::as_str) else {
            continue;
        };
        if !mint_filter.contains(mint) {
            continue;
        }
        let Some(ts_ms) = value
            .get("candidate_ready_ts_ms")
            .and_then(Value::as_u64)
            .or_else(|| value.get("local_receive_ts_ms").and_then(Value::as_u64))
        else {
            continue;
        };
        let canonical_state = value.get("canonical_state").unwrap_or(&Value::Null);
        let mc_usd = canonical_state
            .get("market_cap_usd")
            .and_then(Value::as_f64)
            .or_else(|| candidate.get("market_cap_usd").and_then(Value::as_f64));
        let Some(mc_usd) = mc_usd else {
            continue;
        };
        if !(mc_usd.is_finite() && mc_usd > 0.0 && mc_usd <= max_mark_mc_usd) {
            continue;
        }
        out.entry(mint.to_string())
            .or_default()
            .push(Mark { ts_ms, mc_usd });
    }
    for marks in out.values_mut() {
        marks.sort_by(|a, b| {
            a.ts_ms
                .cmp(&b.ts_ms)
                .then_with(|| a.mc_usd.partial_cmp(&b.mc_usd).unwrap_or(Ordering::Equal))
        });
    }
    Ok(out)
}

fn print_lane_report(
    args: &Args,
    lane: &LaneArg,
    entries: &HashMap<String, Entry>,
    marks_by_mint: &HashMap<String, Vec<Mark>>,
) {
    println!("## {}", lane.name);
    println!();
    println!("| TP | Target Delay | Fillable | Resolved | W | L | Unresolved | Win% | PF | Gross SOL | Fee-Aware SOL | Median Actual Fill ms | Median Fill Drift | P90 Fill Drift | Median Event ms | Shadow Wins Preserved | Shadow Wins Missed | Pre-Entry SL |");
    println!("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|");

    for &tp_pct in &args.tp_pcts {
        let perfect = simulate_lane(
            entries,
            marks_by_mint,
            tp_pct,
            args.sl_pct,
            args.horizon_ms,
            0,
        );
        let perfect_win_mints = perfect
            .iter()
            .filter_map(|(mint, result)| (result.event == Event::Win).then_some(mint.clone()))
            .collect::<HashSet<_>>();
        print_row(args, tp_pct, 0, &perfect, &perfect_win_mints);

        for &delay_ms in &args.delays_ms {
            if delay_ms == 0 {
                continue;
            }
            let delayed = simulate_lane(
                entries,
                marks_by_mint,
                tp_pct,
                args.sl_pct,
                args.horizon_ms,
                delay_ms,
            );
            print_row(args, tp_pct, delay_ms, &delayed, &perfect_win_mints);
        }
    }
    println!();
}

fn simulate_lane(
    entries: &HashMap<String, Entry>,
    marks_by_mint: &HashMap<String, Vec<Mark>>,
    tp_pct: f64,
    sl_pct: f64,
    horizon_ms: u64,
    delay_ms: u64,
) -> HashMap<String, SimResult> {
    let mut out = HashMap::new();
    for entry in entries.values() {
        let Some(marks) = marks_by_mint.get(&entry.mint) else {
            out.insert(entry.mint.clone(), no_fill());
            continue;
        };
        let target_fill_ts = entry.ready_ts_ms.saturating_add(delay_ms);
        let fill = marks
            .iter()
            .copied()
            .find(|mark| mark.ts_ms >= target_fill_ts);
        let Some(fill) = fill else {
            out.insert(entry.mint.clone(), no_fill());
            continue;
        };
        let fill_delay_ms = fill.ts_ms.saturating_sub(entry.ready_ts_ms);
        let entry_drift_pct = ((fill.mc_usd / entry.entry_mc_usd) - 1.0) * 100.0;
        let shadow_sl_threshold = entry.entry_mc_usd * (1.0 + sl_pct / 100.0);
        let pre_entry_shadow_sl = delay_ms > 0
            && marks
                .iter()
                .any(|mark| mark.ts_ms < fill.ts_ms && mark.mc_usd <= shadow_sl_threshold);
        let tp_threshold = fill.mc_usd * (1.0 + tp_pct / 100.0);
        let sl_threshold = fill.mc_usd * (1.0 + sl_pct / 100.0);
        let max_ts = fill.ts_ms.saturating_add(horizon_ms);
        let mut event = Event::Unresolved;
        let mut event_ms_after_fill = None;
        for mark in marks {
            if mark.ts_ms <= fill.ts_ms {
                continue;
            }
            if mark.ts_ms > max_ts {
                break;
            }
            if mark.mc_usd >= tp_threshold {
                event = Event::Win;
                event_ms_after_fill = Some(mark.ts_ms.saturating_sub(fill.ts_ms));
                break;
            }
            if mark.mc_usd <= sl_threshold {
                event = Event::Loss;
                event_ms_after_fill = Some(mark.ts_ms.saturating_sub(fill.ts_ms));
                break;
            }
        }
        out.insert(
            entry.mint.clone(),
            SimResult {
                event,
                fill_mc_usd: Some(fill.mc_usd),
                fill_delay_ms: Some(fill_delay_ms),
                event_ms_after_fill,
                entry_drift_pct: Some(entry_drift_pct),
                pre_entry_shadow_sl,
            },
        );
    }
    out
}

fn no_fill() -> SimResult {
    SimResult {
        event: Event::NoFill,
        fill_mc_usd: None,
        fill_delay_ms: None,
        event_ms_after_fill: None,
        entry_drift_pct: None,
        pre_entry_shadow_sl: false,
    }
}

fn print_row(
    args: &Args,
    tp_pct: f64,
    delay_ms: u64,
    results: &HashMap<String, SimResult>,
    perfect_win_mints: &HashSet<String>,
) {
    let mut fillable = 0_u64;
    let mut wins = 0_u64;
    let mut losses = 0_u64;
    let mut unresolved = 0_u64;
    let mut drift = Vec::new();
    let mut fill_delay = Vec::new();
    let mut event_ms = Vec::new();
    let mut shadow_wins_preserved = 0_u64;
    let mut pre_entry_sl = 0_u64;

    for (mint, result) in results {
        if result.fill_mc_usd.is_some() {
            fillable += 1;
        }
        if let Some(value) = result.fill_delay_ms {
            fill_delay.push(value);
        }
        if result.pre_entry_shadow_sl {
            pre_entry_sl += 1;
        }
        if let Some(value) = result.entry_drift_pct {
            drift.push(value);
        }
        if let Some(value) = result.event_ms_after_fill {
            event_ms.push(value);
        }
        match result.event {
            Event::Win => {
                wins += 1;
                if perfect_win_mints.contains(mint) {
                    shadow_wins_preserved += 1;
                }
            }
            Event::Loss => losses += 1,
            Event::Unresolved => unresolved += 1,
            Event::NoFill => {}
        }
    }

    let resolved = wins + losses;
    let win_rate = if resolved == 0 {
        0.0
    } else {
        wins as f64 * 100.0 / resolved as f64
    };
    let pf = if losses == 0 {
        if wins == 0 {
            None
        } else {
            Some(f64::INFINITY)
        }
    } else {
        Some((wins as f64 * (tp_pct / 100.0)) / (losses as f64 * (args.sl_pct.abs() / 100.0)))
    };
    let gross_sol = wins as f64 * (tp_pct / 100.0) - losses as f64 * (args.sl_pct.abs() / 100.0);
    let fee_aware_sol = gross_sol - resolved as f64 * (args.cost_bps / 10_000.0);
    drift.sort_by(|a, b| a.partial_cmp(b).unwrap_or(Ordering::Equal));
    fill_delay.sort_unstable();
    event_ms.sort_unstable();
    let median_drift = median_f64(&drift);
    let p90_drift = percentile_f64(&drift, 0.90);
    let median_fill_delay = median_u64(&fill_delay);
    let median_event_ms = median_u64(&event_ms);
    let shadow_wins_missed = perfect_win_mints
        .len()
        .saturating_sub(shadow_wins_preserved as usize);

    println!(
        "| {:.0}% | {}ms | {} | {} | {} | {} | {} | {:.2}% | {} | {:+.2} | {:+.2} | {} | {} | {} | {} | {} | {} | {} |",
        tp_pct,
        delay_ms,
        fillable,
        resolved,
        wins,
        losses,
        unresolved,
        win_rate,
        pf.map(format_pf).unwrap_or_else(|| "0.00".to_string()),
        gross_sol,
        fee_aware_sol,
        median_fill_delay
            .map(|value| value.to_string())
            .unwrap_or_else(|| "n/a".to_string()),
        median_drift
            .map(|value| format!("{value:+.2}%"))
            .unwrap_or_else(|| "n/a".to_string()),
        p90_drift
            .map(|value| format!("{value:+.2}%"))
            .unwrap_or_else(|| "n/a".to_string()),
        median_event_ms
            .map(|value| value.to_string())
            .unwrap_or_else(|| "n/a".to_string()),
        shadow_wins_preserved,
        shadow_wins_missed,
        pre_entry_sl,
    );
}

fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    format!("{digest:x}")
}

fn format_pf(value: f64) -> String {
    if value.is_infinite() {
        "inf".to_string()
    } else {
        format!("{value:.2}")
    }
}

fn median_f64(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        None
    } else {
        Some(values[values.len() / 2])
    }
}

fn percentile_f64(values: &[f64], pct: f64) -> Option<f64> {
    if values.is_empty() {
        None
    } else {
        let idx = ((values.len().saturating_sub(1)) as f64 * pct).round() as usize;
        values.get(idx).copied()
    }
}

fn median_u64(values: &[u64]) -> Option<u64> {
    if values.is_empty() {
        None
    } else {
        Some(values[values.len() / 2])
    }
}
