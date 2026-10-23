use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use zstd::stream::read::Decoder;

const MAGIC: &[u8; 8] = b"PEL1FRM\0";

#[derive(Debug, Deserialize)]
struct Freeze {
    items: Vec<FreezeItem>,
}

#[derive(Debug, Deserialize)]
struct FreezeItem {
    ordinal: usize,
    signature: String,
    #[serde(rename = "continuityEpoch")]
    continuity_epoch: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct WindowManifest {
    #[serde(rename = "requestedCaptureStartNs")]
    start_ns: u64,
    #[serde(rename = "requestedCaptureEndNs")]
    end_ns: u64,
}

#[derive(Debug, Deserialize, Default)]
struct FrameMetadata {
    #[serde(alias = "receivedAtUnixNs")]
    received_at_unix_ns: Option<u64>,
    #[serde(alias = "discoveredMints")]
    discovered_mints: Vec<String>,
    #[serde(alias = "pumpInstructionKinds")]
    pump_instruction_kinds: Vec<String>,
    #[serde(alias = "continuityEpoch")]
    continuity_epoch: Option<u64>,
    #[serde(alias = "hasTradeEvent")]
    has_trade_event: bool,
    successful: Option<bool>,
}

#[derive(Debug, Serialize, Default)]
struct ContextRow {
    ordinal: usize,
    signature: String,
    #[serde(rename = "continuityEpoch")]
    continuity_epoch: Option<u64>,
    #[serde(rename = "windowStartNs")]
    window_start_ns: u64,
    #[serde(rename = "windowEndNs")]
    window_end_ns: u64,
    #[serde(rename = "framesInPreEntryWindow")]
    frames: u64,
    #[serde(rename = "uniqueDiscoveredMints")]
    unique_discovered_mints: usize,
    #[serde(rename = "discoveredMints")]
    discovered_mints: Vec<String>,
    #[serde(rename = "pumpInstructionKinds")]
    pump_instruction_kinds: BTreeMap<String, u64>,
    #[serde(rename = "sourceEpochCounts")]
    source_epoch_counts: BTreeMap<String, u64>,
    #[serde(rename = "tradeEventFrames")]
    trade_event_frames: u64,
    #[serde(rename = "successfulFrames")]
    successful_frames: u64,
}

#[derive(Default)]
struct ContextState {
    frames: u64,
    mints: HashSet<String>,
    kinds: BTreeMap<String, u64>,
    epochs: BTreeMap<String, u64>,
    trade_frames: u64,
    successful_frames: u64,
}

#[derive(Debug, Serialize)]
struct Summary {
    schema: &'static str,
    freeze_manifest_sha256: String,
    closed_partitions_scanned: Vec<String>,
    open_partitions_excluded: Vec<String>,
    source_frames_scanned: u64,
    source_frames_matched_to_windows: u64,
    trades_indexed: usize,
    frames_min: u64,
    frames_median: u64,
    frames_p90: u64,
    frames_max: u64,
}

fn sha256(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buf = [0u8; 1024 * 1024];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        digest.update(&buf[..n]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn read_json<T: for<'a> Deserialize<'a>>(path: &Path) -> Result<T> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let root = PathBuf::from(args.next().context("missing --root")?);
    let freeze_path = PathBuf::from(args.next().context("missing freeze manifest")?);
    let output_jsonl = PathBuf::from(args.next().context("missing output jsonl")?);
    let summary_path = PathBuf::from(args.next().context("missing summary path")?);
    let freeze: Freeze = read_json(&freeze_path)?;

    let mut intervals = Vec::with_capacity(freeze.items.len());
    for item in &freeze.items {
        let manifest = root
            .join("data/source-spool-materialized")
            .join(&item.signature)
            .join("manifest.json");
        let window: WindowManifest =
            read_json(&manifest).with_context(|| format!("reading {}", manifest.display()))?;
        intervals.push((window.start_ns, window.end_ns, item.ordinal));
    }
    intervals.sort_by_key(|item| item.0);
    let mut states: BTreeMap<usize, ContextState> = intervals
        .iter()
        .map(|(_, _, ordinal)| (*ordinal, ContextState::default()))
        .collect();
    let mut next_interval = 0usize;
    let mut active = BTreeSet::new();
    let mut scanned = 0u64;
    let mut matched = 0u64;
    let mut closed = Vec::new();

    for key in [
        "2026/10/04/23",
        "2026/10/05/00",
        "2026/10/05/01",
        "2026/10/05/02",
    ] {
        let path = root
            .join("data/source-spool/partitions")
            .join(key)
            .join("evidence.frames.zst");
        if !path.exists() {
            continue;
        }
        closed.push(key.to_string());
        let mut decoder = Decoder::new(BufReader::new(File::open(&path)?))?;
        let mut magic = [0u8; 8];
        decoder.read_exact(&mut magic)?;
        if &magic != MAGIC {
            bail!("frame magic mismatch in {}", path.display());
        }
        loop {
            let mut prefix = [0u8; 8];
            match decoder.read_exact(&mut prefix) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => break,
                Err(error) => return Err(error.into()),
            }
            let metadata_len = u32::from_le_bytes(prefix[..4].try_into()?) as usize;
            let raw_len = u32::from_le_bytes(prefix[4..].try_into()?) as usize;
            let mut body = vec![0u8; metadata_len + raw_len];
            decoder.read_exact(&mut body)?;
            let mut expected = [0u8; 32];
            decoder.read_exact(&mut expected)?;
            let actual = Sha256::digest(&body);
            if actual.as_slice() != expected {
                bail!("frame checksum mismatch in {}", path.display());
            }
            scanned += 1;
            let metadata: FrameMetadata = serde_json::from_slice(&body[..metadata_len])?;
            let Some(received_ns) = metadata.received_at_unix_ns else {
                continue;
            };
            while next_interval < intervals.len() && intervals[next_interval].0 <= received_ns {
                active.insert(intervals[next_interval].2);
                next_interval += 1;
            }
            let expired: Vec<usize> = active
                .iter()
                .copied()
                .filter(|ordinal| {
                    intervals
                        .iter()
                        .find(|(_, _, value)| value == ordinal)
                        .map(|(_, end, _)| *end < received_ns)
                        .unwrap_or(false)
                })
                .collect();
            for ordinal in expired {
                active.remove(&ordinal);
            }
            if active.is_empty() {
                continue;
            }
            matched += 1;
            for ordinal in &active {
                let state = states.get_mut(ordinal).unwrap();
                state.frames += 1;
                state
                    .mints
                    .extend(metadata.discovered_mints.iter().cloned());
                for kind in &metadata.pump_instruction_kinds {
                    *state.kinds.entry(kind.clone()).or_default() += 1;
                }
                let epoch = metadata.continuity_epoch.unwrap_or_default().to_string();
                *state.epochs.entry(epoch).or_default() += 1;
                if metadata.has_trade_event {
                    state.trade_frames += 1;
                }
                if metadata.successful == Some(true) {
                    state.successful_frames += 1;
                }
            }
        }
        eprintln!("scanned {}: {} frames", key, scanned);
    }

    let mut rows = Vec::with_capacity(freeze.items.len());
    for item in &freeze.items {
        let manifest: WindowManifest = read_json(
            &root
                .join("data/source-spool-materialized")
                .join(&item.signature)
                .join("manifest.json"),
        )?;
        let state = states.remove(&item.ordinal).unwrap_or_default();
        rows.push(ContextRow {
            ordinal: item.ordinal,
            signature: item.signature.clone(),
            continuity_epoch: item.continuity_epoch,
            window_start_ns: manifest.start_ns,
            window_end_ns: manifest.end_ns,
            frames: state.frames,
            unique_discovered_mints: state.mints.len(),
            discovered_mints: state.mints.into_iter().collect(),
            pump_instruction_kinds: state.kinds,
            source_epoch_counts: state.epochs,
            trade_event_frames: state.trade_frames,
            successful_frames: state.successful_frames,
        });
    }
    rows.sort_by_key(|row| row.ordinal);
    let frame_counts: Vec<u64> = rows.iter().map(|row| row.frames).collect();
    let percentile =
        |fraction: f64| frame_counts[((frame_counts.len() - 1) as f64 * fraction) as usize];
    let mut output = BufWriter::new(File::create(&output_jsonl)?);
    for row in &rows {
        serde_json::to_writer(&mut output, row)?;
        output.write_all(b"\n")?;
    }
    output.flush()?;
    let summary = Summary {
        schema: "KEFI_PREENTRY_CONTEXT_SUMMARY_V1",
        freeze_manifest_sha256: sha256(&freeze_path)?,
        closed_partitions_scanned: closed,
        open_partitions_excluded: vec!["2026/10/05/03".into()],
        source_frames_scanned: scanned,
        source_frames_matched_to_windows: matched,
        trades_indexed: rows.len(),
        frames_min: *frame_counts.iter().min().unwrap_or(&0),
        frames_median: percentile(0.5),
        frames_p90: percentile(0.9),
        frames_max: *frame_counts.iter().max().unwrap_or(&0),
    };
    let mut summary_file = BufWriter::new(File::create(summary_path)?);
    serde_json::to_writer_pretty(&mut summary_file, &summary)?;
    summary_file.write_all(b"\n")?;
    summary_file.flush()?;
    println!("{}", serde_json::to_string(&summary)?);
    Ok(())
}
