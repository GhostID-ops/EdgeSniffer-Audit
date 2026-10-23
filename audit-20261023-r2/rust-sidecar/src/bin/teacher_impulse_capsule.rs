use std::{
    env,
    fs::{File, OpenOptions},
    io::{self, BufRead, BufReader, Seek, SeekFrom, Write},
    thread::sleep,
    time::Duration,
};

use anyhow::{Context, Result};
use pumpfun_sidecar::parity_capsule::{decision_input_from_capsule, parse_capsule_line};

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
    truncate_output: bool,
    poll_ms: u64,
    max_inputs: Option<u64>,
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
            max_inputs: None,
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--input" => parsed.input = Some(next_arg(&mut args, "--input")?),
                "--output" => parsed.output = Some(next_arg(&mut args, "--output")?),
                "--follow" => parsed.follow = true,
                "--start-at-end" => parsed.start_at_end = true,
                "--truncate-output" => parsed.truncate_output = true,
                "--poll-ms" => parsed.poll_ms = next_arg(&mut args, "--poll-ms")?.parse()?,
                "--max-inputs" => {
                    parsed.max_inputs = Some(next_arg(&mut args, "--max-inputs")?.parse()?)
                }
                "--help" | "-h" => {
                    println!(
                        "usage: teacher_impulse_capsule [--input path|-] [--output path] [--follow] [--start-at-end] [--poll-ms ms] [--truncate-output] [--max-inputs n]"
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
        let file = File::open(path).with_context(|| format!("open capsule input file {path}"))?;
        let writer = output_writer(args.output.as_deref(), args.truncate_output)?;
        emit_decision_inputs(BufReader::new(file), writer, args.max_inputs).map(|_| ())
    } else {
        let stdin = io::stdin();
        let writer = output_writer(args.output.as_deref(), args.truncate_output)?;
        emit_decision_inputs(stdin.lock(), writer, args.max_inputs).map(|_| ())
    }
}

fn follow_file(args: Args) -> Result<()> {
    let input = args.input.as_ref().expect("follow input checked");
    let mut file = OpenOptions::new()
        .read(true)
        .open(input)
        .with_context(|| format!("open capsule input file {input}"))?;
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
            if args.max_inputs.is_some_and(|max| emitted >= max) {
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
        match parse_capsule_line(&carry, emitted + 1).and_then(decision_input_from_capsule) {
            Ok(input) => {
                let mut out = serde_json::to_string(&input)?;
                out.push('\n');
                writer.write_all(out.as_bytes())?;
                writer.flush()?;
                emitted += 1;
            }
            Err(err) => {
                eprintln!("skip malformed capsule line {}: {err:#}", emitted + 1);
                continue;
            }
        }
        if args.max_inputs.is_some_and(|max| emitted >= max) {
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
            .with_context(|| format!("open decision input output file {path}"))?;
        Ok(Box::new(file))
    } else {
        Ok(Box::new(io::stdout()))
    }
}

fn emit_decision_inputs<R: BufRead, W: Write>(
    reader: R,
    mut writer: W,
    max_inputs: Option<u64>,
) -> Result<u64> {
    let mut emitted = 0_u64;
    for (line_no, line) in reader.lines().enumerate() {
        let line = line.with_context(|| format!("read line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let capsule = parse_capsule_line(&line, (line_no + 1) as u64)?;
        let input = decision_input_from_capsule(capsule)?;
        let mut out = serde_json::to_string(&input)?;
        out.push('\n');
        writer.write_all(out.as_bytes())?;
        writer.flush()?;
        emitted += 1;
        if max_inputs.is_some_and(|max| emitted >= max) {
            break;
        }
    }
    Ok(emitted)
}
