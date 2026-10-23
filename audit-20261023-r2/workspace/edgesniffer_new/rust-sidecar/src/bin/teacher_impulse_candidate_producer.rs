use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader, Seek, SeekFrom, Write},
    thread::sleep,
    time::Duration,
};

use anyhow::{Context, Result};
use pumpfun_sidecar::decision_input_producer::{
    decision_input_from_candidate_line, ProducerConfig,
};

fn main() -> Result<()> {
    let args = Args::parse()?;
    run(args)
}

#[derive(Debug, Clone)]
struct Args {
    input: Option<String>,
    output: Option<String>,
    follow: bool,
    start_at_end: bool,
    poll_ms: u64,
    max_inputs: Option<u64>,
    create_output_dir: bool,
    config: ProducerConfig,
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
            max_inputs: None,
            create_output_dir: false,
            config: ProducerConfig::default(),
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--input" => parsed.input = Some(next_arg(&mut args, "--input")?),
                "--output" => parsed.output = Some(next_arg(&mut args, "--output")?),
                "--follow" => parsed.follow = true,
                "--start-at-end" => parsed.start_at_end = true,
                "--poll-ms" => parsed.poll_ms = next_arg(&mut args, "--poll-ms")?.parse()?,
                "--max-inputs" => {
                    parsed.max_inputs = Some(next_arg(&mut args, "--max-inputs")?.parse()?)
                }
                "--create-output-dir" => parsed.create_output_dir = true,
                "--position-size-lamports" => {
                    parsed.config.position_size_lamports =
                        next_arg(&mut args, "--position-size-lamports")?.parse()?
                }
                "--priority-fee-lamports" => {
                    parsed.config.priority_fee_lamports =
                        next_arg(&mut args, "--priority-fee-lamports")?.parse()?
                }
                "--jito-tip-lamports" => {
                    parsed.config.jito_tip_lamports =
                        next_arg(&mut args, "--jito-tip-lamports")?.parse()?
                }
                "--other-tip-lamports" => {
                    parsed.config.other_tip_lamports =
                        next_arg(&mut args, "--other-tip-lamports")?.parse()?
                }
                "--slippage-bps" => {
                    parsed.config.slippage_bps = next_arg(&mut args, "--slippage-bps")?.parse()?
                }
                "--help" | "-h" => {
                    println!(
                        "usage: teacher_impulse_candidate_producer [--input path|-] [--output path] [--follow] [--start-at-end] [--poll-ms ms] [--max-inputs n] [--create-output-dir] [--position-size-lamports n] [--priority-fee-lamports n] [--jito-tip-lamports n] [--other-tip-lamports n] [--slippage-bps n]"
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

fn run(args: Args) -> Result<()> {
    if args.follow {
        follow_file(args)
    } else if let Some(path) = args.input.as_deref().filter(|path| *path != "-") {
        let file =
            File::open(path).with_context(|| format!("open candidate source file {path}"))?;
        let writer = output_writer(args.output.as_deref(), args.create_output_dir)?;
        emit_inputs(BufReader::new(file), writer, &args.config, args.max_inputs).map(|_| ())
    } else {
        let stdin = io::stdin();
        let writer = output_writer(args.output.as_deref(), args.create_output_dir)?;
        emit_inputs(stdin.lock(), writer, &args.config, args.max_inputs).map(|_| ())
    }
}

fn follow_file(args: Args) -> Result<()> {
    let input = args.input.as_ref().expect("follow input checked");
    let mut file = OpenOptions::new()
        .read(true)
        .open(input)
        .with_context(|| format!("open candidate source file {input}"))?;
    if args.start_at_end {
        file.seek(SeekFrom::End(0))?;
    }
    let mut reader = BufReader::new(file);
    let mut writer = output_writer(args.output.as_deref(), args.create_output_dir)?;
    let mut emitted = 0_u64;
    let mut carry = String::new();
    loop {
        carry.clear();
        let bytes = reader.read_line(&mut carry)?;
        if bytes == 0 {
            if args.max_inputs.is_some_and(|max| emitted >= max) {
                return Ok(());
            }
            sleep(Duration::from_millis(args.poll_ms));
            continue;
        }
        if carry.trim().is_empty() {
            continue;
        }
        emit_one(&carry, &mut writer, &args.config, emitted + 1)?;
        emitted += 1;
        if args.max_inputs.is_some_and(|max| emitted >= max) {
            return Ok(());
        }
    }
}

fn output_writer(path: Option<&str>, create_output_dir: bool) -> Result<Box<dyn Write>> {
    if let Some(path) = path {
        if create_output_dir {
            if let Some(parent) = std::path::Path::new(path).parent() {
                fs::create_dir_all(parent).with_context(|| {
                    format!("create decision input directory {}", parent.display())
                })?;
            }
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .with_context(|| format!("open decision input output file {path}"))?;
        Ok(Box::new(file))
    } else {
        Ok(Box::new(io::stdout()))
    }
}

fn emit_inputs<R: BufRead, W: Write>(
    reader: R,
    mut writer: W,
    config: &ProducerConfig,
    max_inputs: Option<u64>,
) -> Result<u64> {
    let mut emitted = 0_u64;
    for (line_no, line) in reader.lines().enumerate() {
        let line = line.with_context(|| format!("read line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        emit_one(&line, &mut writer, config, (line_no + 1) as u64)?;
        emitted += 1;
        if max_inputs.is_some_and(|max| emitted >= max) {
            break;
        }
    }
    Ok(emitted)
}

fn emit_one<W: Write>(
    line: &str,
    writer: &mut W,
    config: &ProducerConfig,
    line_no: u64,
) -> Result<()> {
    let input = decision_input_from_candidate_line(line, config)
        .map_err(anyhow::Error::msg)
        .with_context(|| format!("convert candidate line {line_no}"))?;
    writeln!(writer, "{}", serde_json::to_string(&input)?)?;
    writer.flush()?;
    Ok(())
}
