use std::{
    collections::HashSet,
    env,
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader, Seek, SeekFrom, Write},
    thread::sleep,
    time::Duration,
};

use anyhow::{Context, Result};
use pumpfun_sidecar::source_binding::{normalize_source_line, SourceBindingConfig};

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
    max_emits: Option<u64>,
    create_output_dir: bool,
    dedupe_keys: bool,
    config: SourceBindingConfig,
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
            max_emits: None,
            create_output_dir: false,
            dedupe_keys: true,
            config: SourceBindingConfig::with_default_slippage(),
        };
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--input" => parsed.input = Some(next_arg(&mut args, "--input")?),
                "--output" => parsed.output = Some(next_arg(&mut args, "--output")?),
                "--follow" => parsed.follow = true,
                "--start-at-end" => parsed.start_at_end = true,
                "--poll-ms" => parsed.poll_ms = next_arg(&mut args, "--poll-ms")?.parse()?,
                "--max-emits" => {
                    parsed.max_emits = Some(next_arg(&mut args, "--max-emits")?.parse()?)
                }
                "--create-output-dir" => parsed.create_output_dir = true,
                "--required-lane" => {
                    parsed.config.required_lane = Some(next_arg(&mut args, "--required-lane")?)
                }
                "--allowed-lane" => parsed
                    .config
                    .allowed_lanes
                    .push(next_arg(&mut args, "--allowed-lane")?),
                "--required-policy" => {
                    parsed.config.required_policy = Some(next_arg(&mut args, "--required-policy")?)
                }
                "--slippage-bps" => {
                    parsed.config.slippage_bps = next_arg(&mut args, "--slippage-bps")?.parse()?
                }
                "--no-dedupe" => parsed.dedupe_keys = false,
                "--help" | "-h" => {
                    println!("usage: teacher_impulse_source_emitter [--input path|-] [--output path] [--follow] [--start-at-end] [--poll-ms ms] [--max-emits n] [--create-output-dir] [--required-lane lane] [--allowed-lane lane] [--required-policy policy] [--slippage-bps n] [--no-dedupe]");
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
        let file = File::open(path).with_context(|| format!("open source event file {path}"))?;
        let writer = output_writer(args.output.as_deref(), args.create_output_dir)?;
        emit_sources(
            BufReader::new(file),
            writer,
            &args.config,
            args.max_emits,
            args.dedupe_keys,
        )
        .map(|_| ())
    } else {
        let stdin = io::stdin();
        let writer = output_writer(args.output.as_deref(), args.create_output_dir)?;
        emit_sources(
            stdin.lock(),
            writer,
            &args.config,
            args.max_emits,
            args.dedupe_keys,
        )
        .map(|_| ())
    }
}

fn follow_file(args: Args) -> Result<()> {
    let input = args.input.as_ref().expect("follow input checked");
    let mut file = OpenOptions::new()
        .read(true)
        .open(input)
        .with_context(|| format!("open source event file {input}"))?;
    if args.start_at_end {
        file.seek(SeekFrom::End(0))?;
    }
    let mut reader = BufReader::new(file);
    let mut writer = output_writer(args.output.as_deref(), args.create_output_dir)?;
    let mut emitted = 0_u64;
    let mut seen = HashSet::new();
    let mut carry = String::new();
    loop {
        carry.clear();
        let bytes = reader.read_line(&mut carry)?;
        if bytes == 0 {
            if args.max_emits.is_some_and(|max| emitted >= max) {
                return Ok(());
            }
            sleep(Duration::from_millis(args.poll_ms));
            continue;
        }
        if carry.trim().is_empty() {
            continue;
        }
        emitted += emit_one(
            &carry,
            &mut writer,
            &args.config,
            args.dedupe_keys,
            &mut seen,
        )?;
        if args.max_emits.is_some_and(|max| emitted >= max) {
            return Ok(());
        }
    }
}

fn output_writer(path: Option<&str>, create_output_dir: bool) -> Result<Box<dyn Write>> {
    if let Some(path) = path {
        if create_output_dir {
            if let Some(parent) = std::path::Path::new(path).parent() {
                fs::create_dir_all(parent).with_context(|| {
                    format!("create candidate source directory {}", parent.display())
                })?;
            }
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
            .with_context(|| format!("open candidate source output file {path}"))?;
        Ok(Box::new(file))
    } else {
        Ok(Box::new(io::stdout()))
    }
}

fn emit_sources<R: BufRead, W: Write>(
    reader: R,
    mut writer: W,
    config: &SourceBindingConfig,
    max_emits: Option<u64>,
    dedupe_keys: bool,
) -> Result<u64> {
    let mut emitted = 0_u64;
    let mut seen = HashSet::new();
    for (line_no, line) in reader.lines().enumerate() {
        let line = line.with_context(|| format!("read line {}", line_no + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        emitted += emit_one(&line, &mut writer, config, dedupe_keys, &mut seen)?;
        if max_emits.is_some_and(|max| emitted >= max) {
            break;
        }
    }
    Ok(emitted)
}

fn emit_one<W: Write>(
    line: &str,
    writer: &mut W,
    config: &SourceBindingConfig,
    dedupe_keys: bool,
    seen: &mut HashSet<String>,
) -> Result<u64> {
    let Some(source) = normalize_source_line(line, config).map_err(anyhow::Error::msg)? else {
        return Ok(0);
    };
    let dedupe_key = source
        .get("rawRecordIdentity")
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_string();
    if dedupe_keys && !seen.insert(dedupe_key) {
        return Ok(0);
    }
    writeln!(writer, "{}", serde_json::to_string(&source)?)?;
    writer.flush()?;
    Ok(1)
}
