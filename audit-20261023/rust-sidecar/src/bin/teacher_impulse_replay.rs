use std::{
    env,
    fs::{File, OpenOptions},
    io::{self, BufRead, BufReader, Write},
};

use anyhow::{Context, Result};
use pumpfun_sidecar::{
    parity_capsule::parse_decision_input_line,
    parity_core::{evaluate_decision, StrategyConfig},
};

fn main() -> Result<()> {
    let args = Args::parse()?;
    let config = StrategyConfig::default();
    run(args, &config)
}

#[derive(Debug, Clone)]
struct Args {
    input: Option<String>,
    output: Option<String>,
    truncate_output: bool,
    max_receipts: Option<u64>,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut args = env::args().skip(1);
        let mut parsed = Self {
            input: None,
            output: None,
            truncate_output: false,
            max_receipts: None,
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--input" => parsed.input = Some(next_arg(&mut args, "--input")?),
                "--output" => parsed.output = Some(next_arg(&mut args, "--output")?),
                "--truncate-output" => parsed.truncate_output = true,
                "--max-receipts" => {
                    parsed.max_receipts = Some(next_arg(&mut args, "--max-receipts")?.parse()?)
                }
                "--help" | "-h" => {
                    println!(
                        "usage: teacher_impulse_replay [--input path|-] [--output path] [--truncate-output] [--max-receipts n]"
                    );
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

fn run(args: Args, config: &StrategyConfig) -> Result<()> {
    if let Some(path) = args.input.as_deref().filter(|path| *path != "-") {
        let file = File::open(path).with_context(|| format!("open replay input file {path}"))?;
        let writer = output_writer(args.output.as_deref(), args.truncate_output)?;
        emit_receipts(BufReader::new(file), writer, config, args.max_receipts).map(|_| ())
    } else {
        let stdin = io::stdin();
        let writer = output_writer(args.output.as_deref(), args.truncate_output)?;
        emit_receipts(stdin.lock(), writer, config, args.max_receipts).map(|_| ())
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
            .with_context(|| format!("open replay receipt output file {path}"))?;
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
        let input = parse_decision_input_line(&line, (line_no + 1) as u64)?;
        let receipt = evaluate_decision(&input, config);
        writeln!(writer, "{}", serde_json::to_string(&receipt)?)?;
        writer.flush()?;
        emitted += 1;
        if max_receipts.is_some_and(|max| emitted >= max) {
            break;
        }
    }
    Ok(emitted)
}
