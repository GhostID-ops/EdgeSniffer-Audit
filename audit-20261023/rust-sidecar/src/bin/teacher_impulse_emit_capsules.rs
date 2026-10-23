use std::{
    env,
    fs::{File, OpenOptions},
    io::{self, BufRead, BufReader, Seek, SeekFrom, Write},
    thread::sleep,
    time::Duration,
};

use anyhow::{Context, Result};
use pumpfun_sidecar::teacher_impulse_emitter::{
    parse_observation_line, TeacherImpulseEmitter, TeacherImpulseEmitterConfig,
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
    truncate_output: bool,
    poll_ms: u64,
    max_capsules: Option<u64>,
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
            max_capsules: None,
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--input" => parsed.input = Some(next_arg(&mut args, "--input")?),
                "--output" => parsed.output = Some(next_arg(&mut args, "--output")?),
                "--follow" => parsed.follow = true,
                "--start-at-end" => parsed.start_at_end = true,
                "--truncate-output" => parsed.truncate_output = true,
                "--poll-ms" => parsed.poll_ms = next_arg(&mut args, "--poll-ms")?.parse()?,
                "--max-capsules" => {
                    parsed.max_capsules = Some(next_arg(&mut args, "--max-capsules")?.parse()?)
                }
                "--help" | "-h" => {
                    println!(
                        "usage: teacher_impulse_emit_capsules [--input path|-] [--output path] [--follow] [--start-at-end] [--poll-ms ms] [--truncate-output] [--max-capsules n]"
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
            File::open(path).with_context(|| format!("open normalized observation file {path}"))?;
        let writer = output_writer(args.output.as_deref(), args.truncate_output)?;
        emit_capsules(BufReader::new(file), writer, args.max_capsules).map(|_| ())
    } else {
        let stdin = io::stdin();
        let writer = output_writer(args.output.as_deref(), args.truncate_output)?;
        emit_capsules(stdin.lock(), writer, args.max_capsules).map(|_| ())
    }
}

fn follow_file(args: Args) -> Result<()> {
    let input = args.input.as_ref().expect("follow input checked");
    let mut file = OpenOptions::new()
        .read(true)
        .open(input)
        .with_context(|| format!("open normalized observation file {input}"))?;
    if args.start_at_end {
        file.seek(SeekFrom::End(0))?;
    }
    let mut reader = BufReader::new(file);
    let mut writer = output_writer(args.output.as_deref(), args.truncate_output)?;
    let mut emitter = TeacherImpulseEmitter::new(emitter_config_from_env());
    let mut emitted = 0_u64;
    let mut carry = String::new();

    loop {
        carry.clear();
        let line_start = reader.stream_position()?;
        let bytes = reader.read_line(&mut carry)?;
        if bytes == 0 {
            if args.max_capsules.is_some_and(|max| emitted >= max) {
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
        match emit_one(&carry, &mut writer, &mut emitter, emitted + 1) {
            Ok(()) => emitted += 1,
            Err(err) => {
                eprintln!("skip malformed observation line {}: {err:#}", emitted + 1);
                continue;
            }
        }
        if args.max_capsules.is_some_and(|max| emitted >= max) {
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
            .with_context(|| format!("open capsule output file {path}"))?;
        Ok(Box::new(file))
    } else {
        Ok(Box::new(io::stdout()))
    }
}

fn emit_capsules<R: BufRead, W: Write>(
    reader: R,
    mut writer: W,
    max_capsules: Option<u64>,
) -> Result<u64> {
    let mut emitter = TeacherImpulseEmitter::new(emitter_config_from_env());
    let mut emitted = 0_u64;
    for (line_no, line) in reader.lines().enumerate() {
        let line = line.with_context(|| format!("read line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        emit_one(&line, &mut writer, &mut emitter, (line_no + 1) as u64)?;
        emitted += 1;
        if max_capsules.is_some_and(|max| emitted >= max) {
            break;
        }
    }
    Ok(emitted)
}

fn emitter_config_from_env() -> TeacherImpulseEmitterConfig {
    let mut config = TeacherImpulseEmitterConfig::default();
    if let Some(sol_price) = env_f64("TEACHER_IMPULSE_SOL_PRICE_USD")
        .or_else(|| env_f64("SOL_PRICE_USD"))
        .or_else(|| env_f64("LOCAL_PUMPFUN_SOL_PRICE_USD"))
        .or_else(|| env_f64("SHRED_MC_SOL_PRICE_USD"))
    {
        config.sol_price_usd = sol_price;
    }
    if let Some(position_size_lamports) = env_u64("TEACHER_IMPULSE_POSITION_SIZE_LAMPORTS") {
        config.default_position_size_lamports = position_size_lamports.max(1);
    }
    if let Some(slippage_bps) = env_u64("TEACHER_IMPULSE_SLIPPAGE_BPS") {
        config.default_slippage_bps = slippage_bps.min(9_500);
    }
    config
}

fn env_f64(name: &str) -> Option<f64> {
    env::var(name)
        .ok()
        .and_then(|value| value.trim().parse::<f64>().ok())
        .filter(|value| value.is_finite() && *value > 0.0)
}

fn env_u64(name: &str) -> Option<u64> {
    env::var(name)
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
}

fn emit_one<W: Write>(
    line: &str,
    writer: &mut W,
    emitter: &mut TeacherImpulseEmitter,
    line_no: u64,
) -> Result<()> {
    let observation = parse_observation_line(line, line_no)?;
    let capsule = emitter.ingest(observation)?;
    let mut out = serde_json::to_string(&capsule)?;
    out.push('\n');
    writer.write_all(out.as_bytes())?;
    writer.flush()?;
    Ok(())
}
