use std::{
    collections::HashMap,
    env,
    fs::{File, OpenOptions},
    io::{BufRead, BufReader, Write},
};

use anyhow::{bail, Context, Result};
use pumpfun_sidecar::{
    parity_capsule::{parse_capsule_line, parse_decision_input_line},
    parity_core::{stable_hash, CandidateFeatures, DecisionInput, DecisionReceipt, DecisionStatus},
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
    min_cut_sample: usize,
}

#[derive(Debug, Clone)]
struct AcceptedEntry {
    mint: String,
    ready_ts_ms: u64,
    entry_mc_usd: f64,
    features: CandidateFeatures,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FirstEvent {
    Tp,
    Sl,
    Unresolved,
    NoMarks,
}

#[derive(Debug, Clone, Default)]
struct Outcome {
    marks: u64,
    invalid_marks: u64,
    max_roi_pct: Option<f64>,
    min_roi_pct: Option<f64>,
    first_tp_ms: Option<u64>,
    first_sl_ms: Option<u64>,
}

#[derive(Debug, Clone)]
struct LabeledEntry {
    entry: AcceptedEntry,
    outcome: Outcome,
    first_event: FirstEvent,
}

#[derive(Debug, Clone)]
struct Rule {
    name: String,
    predicate: Predicate,
}

#[derive(Debug, Clone)]
enum Predicate {
    MaxMc(f64),
    RangeMc(f64, f64),
    MaxAge(u64),
    RangeAge(u64, u64),
    MinNet(f64),
    MinBuy100(u32),
    MinBuy250(u32),
    MinBuy500(u32),
    MinBuy1000(u32),
    MaxSellPressure(f64),
    RangeSellPressure(f64, f64),
    MaxDominance(f64),
    RangeDominance(f64, f64),
    MinUnique(u32),
    MaxFirstBuyerBps(u32),
    MaxLargestWalletBps(u32),
}

#[derive(Debug, Clone)]
struct CutResult {
    name: String,
    sample: usize,
    resolved: usize,
    wins: usize,
    losses: usize,
    unresolved: usize,
    no_marks: usize,
    pf_proxy: f64,
    win_rate_resolved: f64,
    median_tp_ms: Option<u64>,
    median_entry_mc: Option<f64>,
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
            min_cut_sample: 25,
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
                "--min-cut-sample" => {
                    parsed.min_cut_sample = next_arg(&mut args, "--min-cut-sample")?.parse()?
                }
                "--help" | "-h" => {
                    println!("usage: teacher_impulse_fingerprint_report --receipts path --inputs path --capsules path [--output path] [--horizon-ms ms] [--tp-pct pct] [--sl-pct pct] [--max-mark-mc-usd usd] [--min-cut-sample n]");
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
    let labeled = label_entries(accepted, outcomes);
    let mut writer = output_writer(args.output.as_deref())?;
    write_report(&mut writer, &args, &labeled)
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
        let entry = AcceptedEntry {
            mint: receipt.mint.clone(),
            ready_ts_ms,
            entry_mc_usd,
            features: input.candidate.clone(),
        };
        by_mint
            .entry(receipt.mint)
            .and_modify(|existing| {
                if entry.ready_ts_ms < existing.ready_ts_ms {
                    *existing = entry.clone();
                }
            })
            .or_insert(entry);
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
        let elapsed_ms = mark_ts.saturating_sub(entry.ready_ts_ms);
        let roi_pct = ((mc / entry.entry_mc_usd) - 1.0) * 100.0;
        outcome.max_roi_pct = Some(outcome.max_roi_pct.map_or(roi_pct, |old| old.max(roi_pct)));
        outcome.min_roi_pct = Some(outcome.min_roi_pct.map_or(roi_pct, |old| old.min(roi_pct)));
        if outcome.first_tp_ms.is_none() && roi_pct >= args.tp_pct {
            outcome.first_tp_ms = Some(elapsed_ms);
        }
        if outcome.first_sl_ms.is_none() && roi_pct <= args.sl_pct {
            outcome.first_sl_ms = Some(elapsed_ms);
        }
    }
    Ok(outcomes)
}

fn label_entries(
    accepted: Vec<AcceptedEntry>,
    outcomes: HashMap<String, Outcome>,
) -> Vec<LabeledEntry> {
    accepted
        .into_iter()
        .map(|entry| {
            let outcome = outcomes.get(&entry.mint).cloned().unwrap_or_default();
            let first_event = match (outcome.first_tp_ms, outcome.first_sl_ms, outcome.marks) {
                (_, _, 0) => FirstEvent::NoMarks,
                (Some(tp), Some(sl), _) if tp < sl => FirstEvent::Tp,
                (Some(_), None, _) => FirstEvent::Tp,
                (Some(tp), Some(sl), _) if sl <= tp => FirstEvent::Sl,
                (None, Some(_), _) => FirstEvent::Sl,
                _ => FirstEvent::Unresolved,
            };
            LabeledEntry {
                entry,
                outcome,
                first_event,
            }
        })
        .collect()
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
        Ok(Box::new(std::io::stdout()))
    }
}

fn write_report<W: Write>(writer: &mut W, args: &Args, entries: &[LabeledEntry]) -> Result<()> {
    let baseline = summarize("baseline".to_string(), entries, args);
    let timespan_hours = entries
        .first()
        .zip(entries.last())
        .map(|(first, last)| {
            last.entry
                .ready_ts_ms
                .saturating_sub(first.entry.ready_ts_ms) as f64
                / 3_600_000.0
        })
        .unwrap_or(0.0);
    let accept_rate_per_hour = if timespan_hours > 0.0 {
        entries.len() as f64 / timespan_hours
    } else {
        0.0
    };

    writeln!(writer, "# Teacher Impulse Fingerprint Report")?;
    writeln!(writer)?;
    writeln!(
        writer,
        "Generated from post-reset Rust receipts/inputs/capsules only."
    )?;
    writeln!(writer)?;
    writeln!(writer, "| Metric | Value |")?;
    writeln!(writer, "|---|---:|")?;
    writeln!(
        writer,
        "| Unique accepted shadow buys | {} |",
        entries.len()
    )?;
    writeln!(
        writer,
        "| Observed timespan hours | {:.2} |",
        timespan_hours
    )?;
    writeln!(
        writer,
        "| Accepted shadow buys/hour | {:.2} |",
        accept_rate_per_hour
    )?;
    writeln!(writer, "| Horizon ms | {} |", args.horizon_ms)?;
    writeln!(
        writer,
        "| TP / SL | +{:.2}% / {:.2}% |",
        args.tp_pct, args.sl_pct
    )?;
    writeln!(
        writer,
        "| Max forward MC mark | ${:.0} |",
        args.max_mark_mc_usd
    )?;
    writeln!(writer, "| Min cut sample | {} |", args.min_cut_sample)?;
    writeln!(writer)?;
    write_cut_table(writer, "Baseline First-Event Outcome", &[baseline])?;
    writeln!(writer)?;
    write_feature_summary(writer, entries)?;
    writeln!(writer)?;
    write_bucket_tables(writer, entries, args)?;
    writeln!(writer)?;
    write_candidate_cuts(writer, entries, args)?;
    writeln!(writer)?;
    write_examples(writer, entries)?;
    Ok(())
}

fn write_feature_summary<W: Write>(writer: &mut W, entries: &[LabeledEntry]) -> Result<()> {
    writeln!(writer, "## Winner Vs Loser Feature Medians")?;
    writeln!(writer)?;
    writeln!(writer, "| Feature | TP median | SL median | All median |")?;
    writeln!(writer, "|---|---:|---:|---:|")?;
    for feature in numeric_features() {
        let tp = median_feature(entries, FirstEvent::Tp, feature);
        let sl = median_feature(entries, FirstEvent::Sl, feature);
        let all = median_feature_all(entries, feature);
        writeln!(
            writer,
            "| {} | {} | {} | {} |",
            feature.name(),
            fmt_opt(tp),
            fmt_opt(sl),
            fmt_opt(all)
        )?;
    }
    Ok(())
}

fn write_bucket_tables<W: Write>(
    writer: &mut W,
    entries: &[LabeledEntry],
    args: &Args,
) -> Result<()> {
    let bucket_specs = vec![
        (
            "Entry MC Buckets",
            vec![
                Rule::max_mc(2500.0),
                Rule::range_mc(2500.0, 3500.0),
                Rule::range_mc(3500.0, 4500.0),
                Rule::range_mc(4500.0, 6000.0),
            ],
        ),
        (
            "Creation Age Buckets",
            vec![
                Rule::max_age(250),
                Rule::range_age(250, 500),
                Rule::range_age(500, 1000),
                Rule::range_age(1000, 2500),
            ],
        ),
        (
            "Sell Pressure Buckets",
            vec![
                Rule::max_sell_pressure(0.05),
                Rule::range_sell_pressure(0.05, 0.10),
                Rule::range_sell_pressure(0.10, 0.20),
                Rule::range_sell_pressure(0.20, 0.25),
            ],
        ),
        (
            "Dominance Buckets",
            vec![
                Rule::max_dominance(0.25),
                Rule::range_dominance(0.25, 0.40),
                Rule::range_dominance(0.40, 0.55),
                Rule::range_dominance(0.55, 0.65),
            ],
        ),
    ];
    for (title, rules) in bucket_specs {
        let results = rules
            .iter()
            .map(|rule| summarize_cut(rule.name.clone(), entries, args, &[rule]))
            .collect::<Vec<_>>();
        write_cut_table(writer, title, &results)?;
        writeln!(writer)?;
    }
    Ok(())
}

fn write_candidate_cuts<W: Write>(
    writer: &mut W,
    entries: &[LabeledEntry],
    args: &Args,
) -> Result<()> {
    let rules = candidate_rules();
    let mut cuts = Vec::new();
    for rule in &rules {
        push_cut(
            &mut cuts,
            summarize_cut(rule.name.clone(), entries, args, &[rule]),
            args,
        );
    }
    for i in 0..rules.len() {
        for j in (i + 1)..rules.len() {
            let name = format!("{} + {}", rules[i].name, rules[j].name);
            push_cut(
                &mut cuts,
                summarize_cut(name, entries, args, &[&rules[i], &rules[j]]),
                args,
            );
        }
    }
    cuts.sort_by(|a, b| {
        b.pf_proxy
            .partial_cmp(&a.pf_proxy)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| b.resolved.cmp(&a.resolved))
            .then_with(|| b.sample.cmp(&a.sample))
    });
    writeln!(writer, "## Candidate Child Filters")?;
    writeln!(writer)?;
    writeln!(
        writer,
        "These are hypotheses only. Promote only after prospective reset collection, not from this same mined sample."
    )?;
    writeln!(writer)?;
    write_cut_table(
        writer,
        "Top Cuts By First-Event PF Proxy",
        &cuts.into_iter().take(20).collect::<Vec<_>>(),
    )?;
    Ok(())
}

fn push_cut(cuts: &mut Vec<CutResult>, cut: CutResult, args: &Args) {
    if cut.sample >= args.min_cut_sample && cut.resolved >= 10 && cut.losses > 0 {
        cuts.push(cut);
    }
}

fn write_examples<W: Write>(writer: &mut W, entries: &[LabeledEntry]) -> Result<()> {
    let mut winners = entries
        .iter()
        .filter(|entry| entry.first_event == FirstEvent::Tp)
        .collect::<Vec<_>>();
    winners.sort_by(|a, b| {
        b.outcome
            .max_roi_pct
            .unwrap_or(0.0)
            .partial_cmp(&a.outcome.max_roi_pct.unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut losers = entries
        .iter()
        .filter(|entry| entry.first_event == FirstEvent::Sl)
        .collect::<Vec<_>>();
    losers.sort_by(|a, b| {
        a.outcome
            .min_roi_pct
            .unwrap_or(0.0)
            .partial_cmp(&b.outcome.min_roi_pct.unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    writeln!(writer, "## Example Mints")?;
    writeln!(writer)?;
    writeln!(writer, "### Top First-Event Winners")?;
    writeln!(writer)?;
    writeln!(
        writer,
        "| Mint | Entry MC | Max ROI | TP ms | Net60 | Buys1000 | SellPressure | DomShare |"
    )?;
    writeln!(writer, "|---|---:|---:|---:|---:|---:|---:|---:|")?;
    for entry in winners.into_iter().take(12) {
        write_example_row(writer, entry, true)?;
    }
    writeln!(writer)?;
    writeln!(writer, "### Worst First-Event Losers")?;
    writeln!(writer)?;
    writeln!(
        writer,
        "| Mint | Entry MC | Min ROI | SL ms | Net60 | Buys1000 | SellPressure | DomShare |"
    )?;
    writeln!(writer, "|---|---:|---:|---:|---:|---:|---:|---:|")?;
    for entry in losers.into_iter().take(12) {
        write_example_row(writer, entry, false)?;
    }
    Ok(())
}

fn write_example_row<W: Write>(writer: &mut W, entry: &LabeledEntry, winner: bool) -> Result<()> {
    let metric = if winner {
        entry.outcome.max_roi_pct
    } else {
        entry.outcome.min_roi_pct
    };
    let elapsed = if winner {
        entry.outcome.first_tp_ms
    } else {
        entry.outcome.first_sl_ms
    };
    writeln!(
        writer,
        "| `{}` | {:.0} | {} | {} | {} | {} | {} | {} |",
        entry.entry.mint,
        entry.entry.entry_mc_usd,
        fmt_opt(metric),
        elapsed
            .map(|value| value.to_string())
            .unwrap_or_else(|| "n/a".to_string()),
        fmt_opt(entry.entry.features.net_buy_sol_60),
        entry
            .entry
            .features
            .buy_count_1000ms
            .map(|value| value.to_string())
            .unwrap_or_else(|| "n/a".to_string()),
        fmt_opt(entry.entry.features.sell_pressure_60),
        fmt_opt(entry.entry.features.dominant_buy_share_60)
    )?;
    Ok(())
}

fn write_cut_table<W: Write>(writer: &mut W, title: &str, cuts: &[CutResult]) -> Result<()> {
    writeln!(writer, "## {title}")?;
    writeln!(writer)?;
    writeln!(writer, "| Cut | Sample | Resolved | W | L | Unresolved | No Marks | Win% Resolved | PF Proxy | Median TP ms | Median Entry MC |")?;
    writeln!(
        writer,
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    )?;
    for cut in cuts {
        writeln!(
            writer,
            "| {} | {} | {} | {} | {} | {} | {} | {:.2}% | {:.2} | {} | {} |",
            cut.name,
            cut.sample,
            cut.resolved,
            cut.wins,
            cut.losses,
            cut.unresolved,
            cut.no_marks,
            cut.win_rate_resolved,
            cut.pf_proxy,
            cut.median_tp_ms
                .map(|value| value.to_string())
                .unwrap_or_else(|| "n/a".to_string()),
            fmt_opt(cut.median_entry_mc)
        )?;
    }
    Ok(())
}

fn summarize(name: String, entries: &[LabeledEntry], args: &Args) -> CutResult {
    summarize_inner(name, entries.iter(), args)
}

fn summarize_cut(
    name: String,
    entries: &[LabeledEntry],
    args: &Args,
    rules: &[&Rule],
) -> CutResult {
    summarize_inner(
        name,
        entries
            .iter()
            .filter(|entry| rules.iter().all(|rule| rule.matches(entry))),
        args,
    )
}

fn summarize_inner<'a>(
    name: String,
    entries: impl Iterator<Item = &'a LabeledEntry>,
    args: &Args,
) -> CutResult {
    let mut sample = 0_usize;
    let mut wins = 0_usize;
    let mut losses = 0_usize;
    let mut unresolved = 0_usize;
    let mut no_marks = 0_usize;
    let mut tp_times = Vec::new();
    let mut entry_mcs = Vec::new();
    for entry in entries {
        sample += 1;
        entry_mcs.push(entry.entry.entry_mc_usd);
        match entry.first_event {
            FirstEvent::Tp => {
                wins += 1;
                if let Some(ms) = entry.outcome.first_tp_ms {
                    tp_times.push(ms);
                }
            }
            FirstEvent::Sl => losses += 1,
            FirstEvent::Unresolved => unresolved += 1,
            FirstEvent::NoMarks => no_marks += 1,
        }
    }
    let resolved = wins + losses;
    let pf_proxy = if losses == 0 {
        if wins > 0 {
            f64::INFINITY
        } else {
            0.0
        }
    } else {
        (wins as f64 * args.tp_pct) / (losses as f64 * args.sl_pct.abs())
    };
    CutResult {
        name,
        sample,
        resolved,
        wins,
        losses,
        unresolved,
        no_marks,
        pf_proxy,
        win_rate_resolved: pct(wins as f64, resolved as f64),
        median_tp_ms: median_u64(&mut tp_times),
        median_entry_mc: median_f64(&mut entry_mcs),
    }
}

fn candidate_rules() -> Vec<Rule> {
    let mut rules = Vec::new();
    for value in [2200.0, 2500.0, 3000.0, 3500.0, 4000.0, 5000.0] {
        rules.push(Rule::max_mc(value));
    }
    for value in [250_u64, 500, 750, 1000, 1500, 2000] {
        rules.push(Rule::max_age(value));
    }
    for value in [2.0, 3.0, 5.0, 8.0, 12.0] {
        rules.push(Rule::min_net(value));
    }
    for value in [2_u32, 3, 4, 5, 6, 8] {
        rules.push(Rule::min_buy1000(value));
    }
    for value in [1_u32, 2, 3] {
        rules.push(Rule::min_buy100(value));
        rules.push(Rule::min_buy250(value));
        rules.push(Rule::min_buy500(value));
    }
    for value in [0.05, 0.10, 0.15, 0.20] {
        rules.push(Rule::max_sell_pressure(value));
    }
    for value in [0.25, 0.35, 0.45, 0.55] {
        rules.push(Rule::max_dominance(value));
    }
    for value in [3_u32, 4, 5, 6, 8] {
        rules.push(Rule::min_unique(value));
    }
    for value in [2_000_u32, 3_000, 4_000, 5_000, 6_500] {
        rules.push(Rule::max_first_buyer_bps(value));
        rules.push(Rule::max_largest_wallet_bps(value));
    }
    rules
}

impl Rule {
    fn max_mc(value: f64) -> Self {
        Self {
            name: format!("entry_mc <= {:.0}", value),
            predicate: Predicate::MaxMc(value),
        }
    }

    fn range_mc(min: f64, max: f64) -> Self {
        Self {
            name: format!("{:.0} < entry_mc <= {:.0}", min, max),
            predicate: Predicate::RangeMc(min, max),
        }
    }

    fn max_age(value: u64) -> Self {
        Self {
            name: format!("age <= {}", value),
            predicate: Predicate::MaxAge(value),
        }
    }

    fn range_age(min: u64, max: u64) -> Self {
        Self {
            name: format!("{} < age <= {}", min, max),
            predicate: Predicate::RangeAge(min, max),
        }
    }

    fn min_net(value: f64) -> Self {
        Self {
            name: format!("net60 >= {:.0}", value),
            predicate: Predicate::MinNet(value),
        }
    }

    fn min_buy100(value: u32) -> Self {
        Self {
            name: format!("buys100 >= {}", value),
            predicate: Predicate::MinBuy100(value),
        }
    }

    fn min_buy250(value: u32) -> Self {
        Self {
            name: format!("buys250 >= {}", value),
            predicate: Predicate::MinBuy250(value),
        }
    }

    fn min_buy500(value: u32) -> Self {
        Self {
            name: format!("buys500 >= {}", value),
            predicate: Predicate::MinBuy500(value),
        }
    }

    fn min_buy1000(value: u32) -> Self {
        Self {
            name: format!("buys1000 >= {}", value),
            predicate: Predicate::MinBuy1000(value),
        }
    }

    fn max_sell_pressure(value: f64) -> Self {
        Self {
            name: format!("sell_pressure <= {:.2}", value),
            predicate: Predicate::MaxSellPressure(value),
        }
    }

    fn range_sell_pressure(min: f64, max: f64) -> Self {
        Self {
            name: format!("{:.2} < sell_pressure <= {:.2}", min, max),
            predicate: Predicate::RangeSellPressure(min, max),
        }
    }

    fn max_dominance(value: f64) -> Self {
        Self {
            name: format!("dom <= {:.2}", value),
            predicate: Predicate::MaxDominance(value),
        }
    }

    fn range_dominance(min: f64, max: f64) -> Self {
        Self {
            name: format!("{:.2} < dom <= {:.2}", min, max),
            predicate: Predicate::RangeDominance(min, max),
        }
    }

    fn min_unique(value: u32) -> Self {
        Self {
            name: format!("unique >= {}", value),
            predicate: Predicate::MinUnique(value),
        }
    }

    fn max_first_buyer_bps(value: u32) -> Self {
        Self {
            name: format!("first_buyer_bps <= {}", value),
            predicate: Predicate::MaxFirstBuyerBps(value),
        }
    }

    fn max_largest_wallet_bps(value: u32) -> Self {
        Self {
            name: format!("largest_wallet_bps <= {}", value),
            predicate: Predicate::MaxLargestWalletBps(value),
        }
    }

    fn matches(&self, entry: &LabeledEntry) -> bool {
        self.predicate.matches(entry)
    }
}

impl Predicate {
    fn matches(&self, entry: &LabeledEntry) -> bool {
        let f = &entry.entry.features;
        match self {
            Self::MaxMc(value) => entry.entry.entry_mc_usd <= *value,
            Self::RangeMc(min, max) => {
                entry.entry.entry_mc_usd > *min && entry.entry.entry_mc_usd <= *max
            }
            Self::MaxAge(value) => f.creation_age_ms.is_some_and(|actual| actual <= *value),
            Self::RangeAge(min, max) => f
                .creation_age_ms
                .is_some_and(|actual| actual > *min && actual <= *max),
            Self::MinNet(value) => f.net_buy_sol_60.is_some_and(|actual| actual >= *value),
            Self::MinBuy100(value) => f.buy_count_100ms.is_some_and(|actual| actual >= *value),
            Self::MinBuy250(value) => f.buy_count_250ms.is_some_and(|actual| actual >= *value),
            Self::MinBuy500(value) => f.buy_count_500ms.is_some_and(|actual| actual >= *value),
            Self::MinBuy1000(value) => f.buy_count_1000ms.is_some_and(|actual| actual >= *value),
            Self::MaxSellPressure(value) => {
                f.sell_pressure_60.is_some_and(|actual| actual <= *value)
            }
            Self::RangeSellPressure(min, max) => f
                .sell_pressure_60
                .is_some_and(|actual| actual > *min && actual <= *max),
            Self::MaxDominance(value) => f
                .dominant_buy_share_60
                .is_some_and(|actual| actual <= *value),
            Self::RangeDominance(min, max) => f
                .dominant_buy_share_60
                .is_some_and(|actual| actual > *min && actual <= *max),
            Self::MinUnique(value) => f.unique_wallets_60.is_some_and(|actual| actual >= *value),
            Self::MaxFirstBuyerBps(value) => f
                .first_buyer_quote_share_bps
                .is_some_and(|actual| actual <= *value),
            Self::MaxLargestWalletBps(value) => f
                .largest_wallet_quote_share_bps
                .is_some_and(|actual| actual <= *value),
        }
    }
}

#[derive(Debug, Clone, Copy)]
enum NumericFeature {
    EntryMc,
    Age,
    Net60,
    Buy100,
    Buy250,
    Buy500,
    Buy1000,
    Buy60,
    SellPressure,
    Dominance,
    Unique,
    UniquePerBuy,
    FirstBuyerBps,
    LargestWalletBps,
    MaxRoi,
    MinRoi,
    TpMs,
}

fn numeric_features() -> Vec<NumericFeature> {
    vec![
        NumericFeature::EntryMc,
        NumericFeature::Age,
        NumericFeature::Net60,
        NumericFeature::Buy100,
        NumericFeature::Buy250,
        NumericFeature::Buy500,
        NumericFeature::Buy1000,
        NumericFeature::Buy60,
        NumericFeature::SellPressure,
        NumericFeature::Dominance,
        NumericFeature::Unique,
        NumericFeature::UniquePerBuy,
        NumericFeature::FirstBuyerBps,
        NumericFeature::LargestWalletBps,
        NumericFeature::MaxRoi,
        NumericFeature::MinRoi,
        NumericFeature::TpMs,
    ]
}

impl NumericFeature {
    fn name(self) -> &'static str {
        match self {
            Self::EntryMc => "entry_mc",
            Self::Age => "creation_age_ms",
            Self::Net60 => "net_buy_sol_60",
            Self::Buy100 => "buy_count_100ms",
            Self::Buy250 => "buy_count_250ms",
            Self::Buy500 => "buy_count_500ms",
            Self::Buy1000 => "buy_count_1000ms",
            Self::Buy60 => "buy_count_60",
            Self::SellPressure => "sell_pressure_60",
            Self::Dominance => "dominant_buy_share_60",
            Self::Unique => "unique_wallets_60",
            Self::UniquePerBuy => "unique_per_buy_60",
            Self::FirstBuyerBps => "first_buyer_quote_share_bps",
            Self::LargestWalletBps => "largest_wallet_quote_share_bps",
            Self::MaxRoi => "max_forward_roi_pct",
            Self::MinRoi => "min_forward_roi_pct",
            Self::TpMs => "time_to_tp_ms",
        }
    }

    fn value(self, entry: &LabeledEntry) -> Option<f64> {
        let f = &entry.entry.features;
        match self {
            Self::EntryMc => Some(entry.entry.entry_mc_usd),
            Self::Age => f.creation_age_ms.map(|value| value as f64),
            Self::Net60 => f.net_buy_sol_60,
            Self::Buy100 => f.buy_count_100ms.map(|value| value as f64),
            Self::Buy250 => f.buy_count_250ms.map(|value| value as f64),
            Self::Buy500 => f.buy_count_500ms.map(|value| value as f64),
            Self::Buy1000 => f.buy_count_1000ms.map(|value| value as f64),
            Self::Buy60 => f.buy_count_60.map(|value| value as f64),
            Self::SellPressure => f.sell_pressure_60,
            Self::Dominance => f.dominant_buy_share_60,
            Self::Unique => f.unique_wallets_60.map(|value| value as f64),
            Self::UniquePerBuy => f.unique_per_buy_60,
            Self::FirstBuyerBps => f.first_buyer_quote_share_bps.map(|value| value as f64),
            Self::LargestWalletBps => f.largest_wallet_quote_share_bps.map(|value| value as f64),
            Self::MaxRoi => entry.outcome.max_roi_pct,
            Self::MinRoi => entry.outcome.min_roi_pct,
            Self::TpMs => entry.outcome.first_tp_ms.map(|value| value as f64),
        }
    }
}

fn median_feature(
    entries: &[LabeledEntry],
    event: FirstEvent,
    feature: NumericFeature,
) -> Option<f64> {
    let mut values = entries
        .iter()
        .filter(|entry| entry.first_event == event)
        .filter_map(|entry| feature.value(entry))
        .collect::<Vec<_>>();
    median_f64(&mut values)
}

fn median_feature_all(entries: &[LabeledEntry], feature: NumericFeature) -> Option<f64> {
    let mut values = entries
        .iter()
        .filter_map(|entry| feature.value(entry))
        .collect::<Vec<_>>();
    median_f64(&mut values)
}

fn median_f64(values: &mut [f64]) -> Option<f64> {
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    values.get(values.len().checked_sub(1)? / 2).copied()
}

fn median_u64(values: &mut [u64]) -> Option<u64> {
    values.sort_unstable();
    values.get(values.len().checked_sub(1)? / 2).copied()
}

fn fmt_opt(value: Option<f64>) -> String {
    match value {
        Some(value) if value.is_finite() => format!("{value:.2}"),
        Some(_) => "inf".to_string(),
        None => "n/a".to_string(),
    }
}

fn pct(numerator: f64, denominator: f64) -> f64 {
    if denominator <= 0.0 {
        0.0
    } else {
        numerator * 100.0 / denominator
    }
}
