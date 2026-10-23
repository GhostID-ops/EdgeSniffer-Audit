use std::{
    collections::HashMap,
    env,
    fs::{File, OpenOptions},
    io::{self, BufRead, BufReader, Write},
};

use anyhow::{bail, Context, Result};
use pumpfun_sidecar::{
    parity_capsule::{parse_capsule_line, parse_decision_input_line},
    parity_core::{stable_hash, DecisionInput, DecisionReceipt, DecisionStatus},
};

fn main() -> Result<()> {
    let args = Args::parse()?;
    run(args)
}

#[derive(Debug, Clone)]
struct Args {
    receipts: String,
    inputs: String,
    capsules: String,
    output: Option<String>,
    horizon_ms: u64,
    tp_pct: f64,
    sl_pct: f64,
    max_mark_mc_usd: f64,
}

#[derive(Debug, Clone)]
struct AcceptedEntry {
    mint: String,
    input_hash: String,
    ready_ts_ms: u64,
    entry_mc_usd: f64,
}

#[derive(Debug, Clone, Default)]
struct Outcome {
    marks: u64,
    max_mc_usd: Option<f64>,
    min_mc_usd: Option<f64>,
    latest_mc_usd: Option<f64>,
    first_tp_ms: Option<u64>,
    first_sl_ms: Option<u64>,
    max_mc_elapsed_ms: Option<u64>,
    min_mc_elapsed_ms: Option<u64>,
    invalid_marks: u64,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut args = env::args().skip(1);
        let mut parsed = Self {
            receipts: String::new(),
            inputs: String::new(),
            capsules: String::new(),
            output: None,
            horizon_ms: 60_000,
            tp_pct: 8.0,
            sl_pct: -20.0,
            max_mark_mc_usd: 500_000.0,
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--receipts" => parsed.receipts = next_arg(&mut args, "--receipts")?,
                "--inputs" => parsed.inputs = next_arg(&mut args, "--inputs")?,
                "--capsules" => parsed.capsules = next_arg(&mut args, "--capsules")?,
                "--output" => parsed.output = Some(next_arg(&mut args, "--output")?),
                "--horizon-ms" => {
                    parsed.horizon_ms = next_arg(&mut args, "--horizon-ms")?.parse()?
                }
                "--tp-pct" => parsed.tp_pct = next_arg(&mut args, "--tp-pct")?.parse()?,
                "--sl-pct" => parsed.sl_pct = next_arg(&mut args, "--sl-pct")?.parse()?,
                "--max-mark-mc-usd" => {
                    parsed.max_mark_mc_usd = next_arg(&mut args, "--max-mark-mc-usd")?.parse()?
                }
                "--help" | "-h" => {
                    println!("usage: teacher_impulse_outcome_report --receipts path --inputs path --capsules path [--output path] [--horizon-ms ms] [--tp-pct pct] [--sl-pct pct] [--max-mark-mc-usd usd]");
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
        if parsed.capsules.is_empty() {
            bail!("--capsules is required");
        }
        Ok(parsed)
    }
}

fn next_arg(args: &mut impl Iterator<Item = String>, name: &str) -> Result<String> {
    args.next()
        .with_context(|| format!("{name} requires a value"))
}

fn run(args: Args) -> Result<()> {
    let inputs = load_inputs_by_hash(&args.inputs)?;
    let accepted = load_unique_accepted_entries(&args.receipts, &inputs)?;
    let outcomes = load_outcomes(&args.capsules, &accepted, &args)?;
    let mut writer = output_writer(args.output.as_deref())?;
    write_report(&mut writer, &args, &accepted, &outcomes)?;
    Ok(())
}

fn load_inputs_by_hash(path: &str) -> Result<HashMap<String, DecisionInput>> {
    let file = File::open(path).with_context(|| format!("open decision inputs {path}"))?;
    let mut out = HashMap::new();
    for (line_no, line) in BufReader::new(file).lines().enumerate() {
        let line = line.with_context(|| format!("read decision input line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let input = parse_decision_input_line(&line, (line_no + 1) as u64)?;
        out.insert(stable_hash(&input), input);
    }
    Ok(out)
}

fn load_unique_accepted_entries(
    path: &str,
    inputs: &HashMap<String, DecisionInput>,
) -> Result<Vec<AcceptedEntry>> {
    let file = File::open(path).with_context(|| format!("open decision receipts {path}"))?;
    let mut by_mint: HashMap<String, AcceptedEntry> = HashMap::new();
    for (line_no, line) in BufReader::new(file).lines().enumerate() {
        let line = line.with_context(|| format!("read receipt line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let receipt: DecisionReceipt = serde_json::from_str(&line)
            .with_context(|| format!("parse receipt line {}", line_no + 1))?;
        if receipt.status != DecisionStatus::Accepted {
            continue;
        }
        let Some(input) = inputs.get(&receipt.input_hash) else {
            continue;
        };
        let Some(ready_ts_ms) = input.provider.candidate_ready_ts_ms else {
            continue;
        };
        let Some(entry_mc_usd) = receipt.calculated_market_cap_usd else {
            continue;
        };
        by_mint
            .entry(receipt.mint.clone())
            .and_modify(|existing| {
                if ready_ts_ms < existing.ready_ts_ms {
                    *existing = AcceptedEntry {
                        mint: receipt.mint.clone(),
                        input_hash: receipt.input_hash.clone(),
                        ready_ts_ms,
                        entry_mc_usd,
                    };
                }
            })
            .or_insert(AcceptedEntry {
                mint: receipt.mint,
                input_hash: receipt.input_hash,
                ready_ts_ms,
                entry_mc_usd,
            });
    }
    let mut entries = by_mint.into_values().collect::<Vec<_>>();
    entries.sort_by_key(|entry| entry.ready_ts_ms);
    Ok(entries)
}

fn load_outcomes(
    path: &str,
    accepted: &[AcceptedEntry],
    args: &Args,
) -> Result<HashMap<String, Outcome>> {
    let by_mint = accepted
        .iter()
        .map(|entry| (entry.mint.as_str(), entry))
        .collect::<HashMap<_, _>>();
    let mut outcomes = HashMap::<String, Outcome>::new();
    let file = File::open(path).with_context(|| format!("open candidate capsules {path}"))?;
    for (line_no, line) in BufReader::new(file).lines().enumerate() {
        let line = line.with_context(|| format!("read capsule line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let capsule = match parse_capsule_line(&line, (line_no + 1) as u64) {
            Ok(capsule) => capsule,
            Err(_) => continue,
        };
        let Some(entry) = by_mint.get(capsule.candidate.mint.as_str()) else {
            continue;
        };
        let mark_ts = capsule
            .candidate_ready_ts_ms
            .unwrap_or(capsule.local_receive_ts_ms);
        if mark_ts <= entry.ready_ts_ms
            || mark_ts > entry.ready_ts_ms.saturating_add(args.horizon_ms)
        {
            continue;
        }
        let Some(mc) = capsule
            .canonical_state
            .as_ref()
            .and_then(|state| state.market_cap_usd)
            .or(capsule.candidate.market_cap_usd)
        else {
            continue;
        };
        let outcome = outcomes.entry(entry.mint.clone()).or_default();
        if !(mc.is_finite() && mc > 0.0 && mc <= args.max_mark_mc_usd) {
            outcome.invalid_marks = outcome.invalid_marks.saturating_add(1);
            continue;
        }
        outcome.marks = outcome.marks.saturating_add(1);
        outcome.latest_mc_usd = Some(mc);
        let elapsed_ms = mark_ts.saturating_sub(entry.ready_ts_ms);
        if outcome.max_mc_usd.map_or(true, |old| mc > old) {
            outcome.max_mc_usd = Some(mc);
            outcome.max_mc_elapsed_ms = Some(elapsed_ms);
        }
        if outcome.min_mc_usd.map_or(true, |old| mc < old) {
            outcome.min_mc_usd = Some(mc);
            outcome.min_mc_elapsed_ms = Some(elapsed_ms);
        }
        let roi_pct = ((mc / entry.entry_mc_usd) - 1.0) * 100.0;
        if outcome.first_tp_ms.is_none() && roi_pct >= args.tp_pct {
            outcome.first_tp_ms = Some(elapsed_ms);
        }
        if outcome.first_sl_ms.is_none() && roi_pct <= args.sl_pct {
            outcome.first_sl_ms = Some(elapsed_ms);
        }
    }
    Ok(outcomes)
}

fn output_writer(path: Option<&str>) -> Result<Box<dyn Write>> {
    if let Some(path) = path {
        Ok(Box::new(
            OpenOptions::new()
                .create(true)
                .write(true)
                .truncate(true)
                .open(path)
                .with_context(|| format!("open output {path}"))?,
        ))
    } else {
        Ok(Box::new(io::stdout()))
    }
}

fn write_report<W: Write>(
    writer: &mut W,
    args: &Args,
    accepted: &[AcceptedEntry],
    outcomes: &HashMap<String, Outcome>,
) -> Result<()> {
    let mut tp = 0_u64;
    let mut sl = 0_u64;
    let mut no_marks = 0_u64;
    let mut unresolved = 0_u64;
    let mut invalid_marks = 0_u64;
    let mut first_tp = 0_u64;
    let mut first_sl = 0_u64;
    let mut best_rois = Vec::<(String, f64, u64)>::new();
    let mut worst_rois = Vec::<(String, f64, u64)>::new();
    let mut tp_times = Vec::<u64>::new();
    for entry in accepted {
        let Some(outcome) = outcomes.get(&entry.mint) else {
            no_marks = no_marks.saturating_add(1);
            continue;
        };
        invalid_marks = invalid_marks.saturating_add(outcome.invalid_marks);
        if let Some(tp_ms) = outcome.first_tp_ms {
            tp = tp.saturating_add(1);
            tp_times.push(tp_ms);
        }
        if outcome.first_sl_ms.is_some() {
            sl = sl.saturating_add(1);
        }
        if outcome.first_tp_ms.is_none() && outcome.first_sl_ms.is_none() {
            unresolved = unresolved.saturating_add(1);
        }
        match (outcome.first_tp_ms, outcome.first_sl_ms) {
            (Some(tp_ms), Some(sl_ms)) if tp_ms < sl_ms => first_tp = first_tp.saturating_add(1),
            (Some(_), None) => first_tp = first_tp.saturating_add(1),
            (Some(tp_ms), Some(sl_ms)) if sl_ms <= tp_ms => first_sl = first_sl.saturating_add(1),
            (None, Some(_)) => first_sl = first_sl.saturating_add(1),
            _ => {}
        }
        if let Some(max_mc) = outcome.max_mc_usd {
            best_rois.push((
                entry.mint.clone(),
                ((max_mc / entry.entry_mc_usd) - 1.0) * 100.0,
                outcome.max_mc_elapsed_ms.unwrap_or(args.horizon_ms),
            ));
        }
        if let Some(min_mc) = outcome.min_mc_usd {
            worst_rois.push((
                entry.mint.clone(),
                ((min_mc / entry.entry_mc_usd) - 1.0) * 100.0,
                outcome.min_mc_elapsed_ms.unwrap_or(args.horizon_ms),
            ));
        }
    }
    best_rois.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    worst_rois.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
    tp_times.sort_unstable();
    let marked = accepted.len().saturating_sub(no_marks as usize);
    let tp_rate = pct(tp as f64, marked as f64);
    let sl_rate = pct(sl as f64, marked as f64);
    let first_event_total = first_tp.saturating_add(first_sl);
    let first_event_pf_proxy = if first_sl == 0 {
        0.0
    } else {
        (first_tp as f64 * args.tp_pct) / (first_sl as f64 * args.sl_pct.abs())
    };
    writeln!(writer, "# Teacher Impulse Outcome Report")?;
    writeln!(writer)?;
    writeln!(writer, "Generated from Rust reset receipts/capsules only.")?;
    writeln!(writer)?;
    writeln!(writer, "| Metric | Value |")?;
    writeln!(writer, "|---|---:|")?;
    writeln!(writer, "| Unique accepted mints | {} |", accepted.len())?;
    writeln!(writer, "| Forward horizon ms | {} |", args.horizon_ms)?;
    writeln!(writer, "| TP threshold | {:.2}% |", args.tp_pct)?;
    writeln!(writer, "| SL threshold | {:.2}% |", args.sl_pct)?;
    writeln!(
        writer,
        "| Max accepted forward MC mark | ${:.0} |",
        args.max_mark_mc_usd
    )?;
    writeln!(writer, "| Mints with forward marks | {} |", marked)?;
    writeln!(writer, "| Mints without forward marks | {} |", no_marks)?;
    writeln!(
        writer,
        "| Invalid forward marks ignored | {} |",
        invalid_marks
    )?;
    writeln!(writer, "| TP hits | {} |", tp)?;
    writeln!(writer, "| SL hits | {} |", sl)?;
    writeln!(writer, "| First-event TP wins | {} |", first_tp)?;
    writeln!(writer, "| First-event SL losses | {} |", first_sl)?;
    writeln!(
        writer,
        "| First-event resolved mints | {} |",
        first_event_total
    )?;
    writeln!(
        writer,
        "| First-event PF proxy | {:.2} |",
        first_event_pf_proxy
    )?;
    writeln!(writer, "| Neither TP nor SL in horizon | {} |", unresolved)?;
    writeln!(writer, "| TP rate on marked mints | {:.2}% |", tp_rate)?;
    writeln!(writer, "| SL rate on marked mints | {:.2}% |", sl_rate)?;
    if !tp_times.is_empty() {
        writeln!(
            writer,
            "| Median time to TP ms | {} |",
            tp_times[tp_times.len() / 2]
        )?;
    }
    writeln!(writer)?;
    writeln!(writer, "## Top Forward Moves")?;
    writeln!(writer)?;
    writeln!(writer, "| Mint | Max ROI | Time Ref Ms |")?;
    writeln!(writer, "|---|---:|---:|")?;
    for (mint, roi, ms) in best_rois.iter().take(20) {
        writeln!(writer, "| `{mint}` | {:.2}% | {ms} |", roi)?;
    }
    writeln!(writer)?;
    writeln!(writer, "## Worst Forward Drawdowns")?;
    writeln!(writer)?;
    writeln!(writer, "| Mint | Min ROI | Time Ref Ms |")?;
    writeln!(writer, "|---|---:|---:|")?;
    for (mint, roi, ms) in worst_rois.iter().take(20) {
        writeln!(writer, "| `{mint}` | {:.2}% | {ms} |", roi)?;
    }
    Ok(())
}

fn pct(numerator: f64, denominator: f64) -> f64 {
    if denominator <= 0.0 {
        0.0
    } else {
        numerator * 100.0 / denominator
    }
}
