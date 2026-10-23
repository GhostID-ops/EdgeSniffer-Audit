use std::{
    fs::{File, OpenOptions},
    io::{self, BufRead, BufReader, Write},
    path::PathBuf,
    thread::sleep,
    time::Duration,
};

use anyhow::{Context, Result};
use pumpfun_sidecar::provider_observation_adapter::{
    observation_from_detected_swap, parse_detected_swap_provider_line,
};

#[derive(Debug, Default)]
struct Args {
    input: Option<PathBuf>,
    output: Option<PathBuf>,
    follow: bool,
    start_at_end: bool,
    poll_ms: u64,
    max_observations: Option<u64>,
    truncate_output: bool,
}

fn main() -> Result<()> {
    let args = parse_args()?;
    let mut writer: Box<dyn Write> = if let Some(output) = args.output.as_ref() {
        let mut options = OpenOptions::new();
        options.create(true).write(true);
        if args.truncate_output {
            options.truncate(true);
        } else {
            options.append(true);
        }
        Box::new(
            options
                .open(output)
                .with_context(|| format!("open output {}", output.display()))?,
        )
    } else {
        Box::new(io::stdout())
    };

    if args.follow {
        let input = args.input.as_ref().context("--follow requires --input")?;
        follow_file(input, &mut writer, &args)?;
    } else if let Some(input) = args.input.as_ref() {
        let file = File::open(input).with_context(|| format!("open input {}", input.display()))?;
        process_reader(BufReader::new(file), &mut writer, args.max_observations)?;
    } else {
        let stdin = io::stdin();
        process_reader(stdin.lock(), &mut writer, args.max_observations)?;
    }
    Ok(())
}

fn follow_file(input: &PathBuf, writer: &mut dyn Write, args: &Args) -> Result<()> {
    let mut emitted = 0_u64;
    let mut line_no = 0_u64;
    let mut offset = if args.start_at_end {
        std::fs::metadata(input)
            .map(|metadata| metadata.len())
            .unwrap_or(0)
    } else {
        0
    };

    loop {
        let mut file =
            File::open(input).with_context(|| format!("open input {}", input.display()))?;
        use std::io::{Seek, SeekFrom};
        file.seek(SeekFrom::Start(offset))
            .with_context(|| format!("seek input {}", input.display()))?;
        let mut reader = BufReader::new(file);
        let mut line = String::new();
        let mut progressed = false;
        loop {
            line.clear();
            let read = reader
                .read_line(&mut line)
                .with_context(|| format!("read input {}", input.display()))?;
            if read == 0 {
                break;
            }
            if !line.ends_with('\n') {
                sleep(Duration::from_millis(args.poll_ms.max(1)));
                break;
            }
            progressed = true;
            offset = offset.saturating_add(read as u64);
            line_no = line_no.saturating_add(1);
            match emit_line(line.trim_end(), line_no, writer) {
                Ok(true) => {
                    emitted = emitted.saturating_add(1);
                    if args.max_observations.is_some_and(|max| emitted >= max) {
                        return Ok(());
                    }
                }
                Ok(false) => {}
                Err(err) => {
                    eprintln!("skip malformed detected swap line {line_no}: {err:#}");
                    continue;
                }
            }
        }
        if !progressed {
            sleep(Duration::from_millis(args.poll_ms.max(1)));
        }
    }
}

fn process_reader<R: BufRead>(
    reader: R,
    writer: &mut dyn Write,
    max_observations: Option<u64>,
) -> Result<()> {
    let mut emitted = 0_u64;
    for (index, line) in reader.lines().enumerate() {
        let line_no = (index + 1) as u64;
        let line = line.with_context(|| format!("read line {line_no}"))?;
        if emit_line(&line, line_no, writer)? {
            emitted = emitted.saturating_add(1);
            if max_observations.is_some_and(|max| emitted >= max) {
                break;
            }
        }
    }
    Ok(())
}

fn emit_line(line: &str, line_no: u64, writer: &mut dyn Write) -> Result<bool> {
    if line.trim().is_empty() {
        return Ok(false);
    }
    let provider_line = parse_detected_swap_provider_line(line, line_no)?;
    let Some(observation) = observation_from_detected_swap(provider_line)? else {
        return Ok(false);
    };
    let mut out = serde_json::to_string(&observation)
        .with_context(|| format!("serialize observation line {line_no}"))?;
    out.push('\n');
    writer.write_all(out.as_bytes())?;
    writer.flush()?;
    Ok(true)
}

fn parse_args() -> Result<Args> {
    let mut args = Args {
        poll_ms: 25,
        ..Args::default()
    };
    let mut iter = std::env::args().skip(1);
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--input" => {
                args.input = Some(PathBuf::from(
                    iter.next().context("--input requires a value")?,
                ));
            }
            "--output" => {
                args.output = Some(PathBuf::from(
                    iter.next().context("--output requires a value")?,
                ));
            }
            "--follow" => args.follow = true,
            "--start-at-end" => args.start_at_end = true,
            "--truncate-output" => args.truncate_output = true,
            "--poll-ms" => {
                args.poll_ms = iter
                    .next()
                    .context("--poll-ms requires a value")?
                    .parse()
                    .context("--poll-ms must be an integer")?;
            }
            "--max-observations" => {
                args.max_observations = Some(
                    iter.next()
                        .context("--max-observations requires a value")?
                        .parse()
                        .context("--max-observations must be an integer")?,
                );
            }
            _ => anyhow::bail!("unknown argument: {arg}"),
        }
    }
    Ok(args)
}
