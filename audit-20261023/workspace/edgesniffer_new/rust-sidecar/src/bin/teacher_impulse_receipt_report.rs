use std::{
    collections::BTreeMap,
    env,
    fs::File,
    io::{self, BufRead, BufReader, Write},
};

use anyhow::{Context, Result};
use pumpfun_sidecar::parity_core::{DecisionReceipt, DecisionStatus};

fn main() -> Result<()> {
    let args = Args::parse()?;
    let summary = if let Some(path) = args.input.as_deref().filter(|path| *path != "-") {
        let file = File::open(path).with_context(|| format!("open receipt file {path}"))?;
        summarize(BufReader::new(file))?
    } else {
        let stdin = io::stdin();
        summarize(stdin.lock())?
    };
    write_report(&summary, &mut io::stdout())
}

#[derive(Debug, Clone)]
struct Args {
    input: Option<String>,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut args = env::args().skip(1);
        let mut parsed = Self { input: None };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--input" => parsed.input = Some(next_arg(&mut args, "--input")?),
                "--help" | "-h" => {
                    println!("usage: teacher_impulse_receipt_report [--input path|-]");
                    std::process::exit(0);
                }
                _ if parsed.input.is_none() => parsed.input = Some(arg),
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

#[derive(Debug, Default)]
struct Summary {
    total: u64,
    accepted: u64,
    rejected: u64,
    unavailable: u64,
    reason_counts: BTreeMap<String, u64>,
    unavailable_field_counts: BTreeMap<String, u64>,
    accepted_mc_min: Option<f64>,
    accepted_mc_max: Option<f64>,
    accepted_mc_sum: f64,
    accepted_mc_count: u64,
}

fn summarize<R: BufRead>(reader: R) -> Result<Summary> {
    let mut summary = Summary::default();
    for (line_no, line) in reader.lines().enumerate() {
        let line = line.with_context(|| format!("read line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let receipt: DecisionReceipt = serde_json::from_str(&line)
            .with_context(|| format!("parse DecisionReceipt line {}", line_no + 1))?;
        summary.total += 1;
        match receipt.status {
            DecisionStatus::Accepted => {
                summary.accepted += 1;
                if let Some(mc) = receipt.calculated_market_cap_usd {
                    summary.accepted_mc_min =
                        Some(summary.accepted_mc_min.map_or(mc, |v| v.min(mc)));
                    summary.accepted_mc_max =
                        Some(summary.accepted_mc_max.map_or(mc, |v| v.max(mc)));
                    summary.accepted_mc_sum += mc;
                    summary.accepted_mc_count += 1;
                }
            }
            DecisionStatus::Rejected => summary.rejected += 1,
            DecisionStatus::Unavailable => summary.unavailable += 1,
        }
        for reason in receipt.reason_codes {
            *summary.reason_counts.entry(reason).or_default() += 1;
        }
        for field in receipt.unavailable_fields {
            *summary.unavailable_field_counts.entry(field).or_default() += 1;
        }
    }
    Ok(summary)
}

fn write_report<W: Write>(summary: &Summary, writer: &mut W) -> Result<()> {
    writeln!(writer, "# Teacher Impulse Receipt Report")?;
    writeln!(writer)?;
    writeln!(writer, "| Metric | Value |")?;
    writeln!(writer, "|---|---:|")?;
    writeln!(writer, "| Total receipts | {} |", summary.total)?;
    writeln!(writer, "| Accepted | {} |", summary.accepted)?;
    writeln!(writer, "| Rejected | {} |", summary.rejected)?;
    writeln!(writer, "| Unavailable | {} |", summary.unavailable)?;
    if summary.accepted_mc_count > 0 {
        writeln!(
            writer,
            "| Accepted MC avg | {:.2} |",
            summary.accepted_mc_sum / summary.accepted_mc_count as f64
        )?;
        writeln!(
            writer,
            "| Accepted MC min | {:.2} |",
            summary.accepted_mc_min.unwrap_or_default()
        )?;
        writeln!(
            writer,
            "| Accepted MC max | {:.2} |",
            summary.accepted_mc_max.unwrap_or_default()
        )?;
    }
    write_counts(writer, "Reason Counts", &summary.reason_counts)?;
    write_counts(
        writer,
        "Unavailable Field Counts",
        &summary.unavailable_field_counts,
    )?;
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
        return Ok(());
    }
    writeln!(writer, "| Key | Count |")?;
    writeln!(writer, "|---|---:|")?;
    for (key, count) in counts {
        writeln!(writer, "| `{key}` | {count} |")?;
    }
    Ok(())
}
