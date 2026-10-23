use std::{
    collections::{HashMap, HashSet},
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    thread,
    time::Duration,
};

use anyhow::{Context, Result};
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use bs58;
use chrono::Utc;
use prost::Message;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use yellowstone_grpc_proto::geyser::{subscribe_update::UpdateOneof, SubscribeUpdate};

const FRAME_MAGIC: &[u8; 8] = b"PEL1FRM\0";
const PUMP_PROGRAM: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const TRADE_EVENT: [u8; 8] = [189, 219, 127, 211, 78, 230, 97, 238];
const CREATE: [u8; 8] = [24, 30, 200, 40, 5, 28, 7, 119];
const CREATE_V2: [u8; 8] = [214, 144, 76, 236, 95, 139, 49, 180];
const BUY: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];
const BUY_EXACT_SOL_IN: [u8; 8] = [56, 252, 116, 8, 158, 223, 205, 95];
const BUY_V2: [u8; 8] = [184, 23, 238, 97, 103, 197, 211, 61];
const BUY_EXACT_QUOTE_IN_V2: [u8; 8] = [194, 171, 28, 70, 104, 77, 91, 47];
const SELL: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];
const SELL_V2: [u8; 8] = [93, 246, 130, 60, 231, 233, 64, 178];

#[derive(Clone, Debug)]
struct Target {
    ordinal: u64,
    signature: String,
    mint: String,
    slot: u64,
    receive_ns: i64,
}

#[derive(Default)]
struct Runtime {
    targets: HashMap<String, Target>,
    seen: HashSet<String>,
    output: Option<File>,
    frames_scanned: u64,
    matching_frames: u64,
    events_written: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct TapeEvent {
    schema: &'static str,
    schema_version: &'static str,
    dedupe_key: String,
    canonical_ordinal: u64,
    launch_signature: String,
    mint: String,
    evidence_kind: String,
    transaction_signature: String,
    slot: u64,
    transaction_index: u64,
    instruction_coordinate: Option<String>,
    pump_instruction_kind: Option<String>,
    side: Option<String>,
    wallet: Option<String>,
    quote_lamports: Option<u64>,
    token_amount_raw: Option<u64>,
    event_timestamp_s: Option<i64>,
    virtual_sol_reserves_after: Option<u64>,
    virtual_token_reserves_after: Option<u64>,
    real_sol_reserves_after: Option<u64>,
    real_token_reserves_after: Option<u64>,
    source_received_ns: i64,
    source_evidence_id: String,
    source_path: String,
    raw_sha256: String,
    raw_base64: String,
    exact_ordering: String,
    created_at_utc: String,
}

fn as_u64(v: Option<&Value>) -> u64 { v.and_then(Value::as_u64).unwrap_or(0) }
fn as_i64(v: Option<&Value>) -> i64 { v.and_then(Value::as_i64).unwrap_or(0) }
fn strv(v: Option<&Value>) -> String { v.and_then(Value::as_str).unwrap_or_default().to_string() }

fn load_targets(path: &Path) -> Result<HashMap<String, Target>> {
    let mut out = HashMap::new();
    let text = fs::read_to_string(path)?;
    for line in text.lines() {
        let row: Value = match serde_json::from_str(line) { Ok(v) => v, Err(_) => continue };
        let mint = strv(row.get("mint"));
        let signature = strv(row.get("signature"));
        if mint.is_empty() || signature.is_empty() { continue; }
        let target = Target {
            ordinal: as_u64(row.get("canonical_ordinal")),
            signature: signature.clone(),
            mint: mint.clone(),
            slot: as_u64(row.get("slot")),
            receive_ns: as_i64(row.get("source_receive_ns")),
        };
        out.insert(mint, target.clone());
        out.insert(format!("sig:{signature}"), target);
    }
    Ok(out)
}

fn sha256(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

fn read_u64(data: &[u8], offset: usize) -> Option<u64> {
    let end = offset.checked_add(8)?;
    Some(u64::from_le_bytes(data.get(offset..end)?.try_into().ok()?))
}

fn trade_event(data: &[u8]) -> Option<(String, u64, u64, bool, String, i64, u64, u64, u64, u64)> {
    if data.len() < 121 || data[..8] != TRADE_EVENT { return None; }
    let body = &data[8..];
    let mint = bs58::encode(&body[0..32]).into_string();
    let sol = read_u64(body, 32)?;
    let token = read_u64(body, 40)?;
    let is_buy = body[48] != 0;
    let user = bs58::encode(&body[49..81]).into_string();
    let timestamp = i64::from_le_bytes(body[81..89].try_into().ok()?);
    Some((mint, sol, token, is_buy, user, timestamp, read_u64(body, 89)?, read_u64(body, 97)?, read_u64(body, 105)?, read_u64(body, 113)?))
}

fn ix_kind(data: &[u8]) -> Option<&'static str> {
    if data.len() < 8 { return None; }
    let d: [u8; 8] = data[..8].try_into().ok()?;
    Some(match d {
        CREATE => "CREATE",
        CREATE_V2 => "CREATE_V2",
        BUY => "BUY",
        BUY_EXACT_SOL_IN => "BUY_EXACT_SOL_IN",
        BUY_V2 => "BUY_V2",
        BUY_EXACT_QUOTE_IN_V2 => "BUY_EXACT_QUOTE_IN_V2",
        SELL => "SELL",
        SELL_V2 => "SELL_V2",
        _ => return None,
    })
}

fn collect_pump_ixs(info: &yellowstone_grpc_proto::prelude::SubscribeUpdateTransactionInfo) -> Vec<(String, String, Option<String>)> {
    let Some(tx) = info.transaction.as_ref() else { return Vec::new() };
    let Some(message) = tx.message.as_ref() else { return Vec::new() };
    let mut keys: Vec<String> = message.account_keys.iter().map(|k| bs58::encode(k).into_string()).collect();
    if let Some(meta) = info.meta.as_ref() {
        keys.extend(meta.loaded_writable_addresses.iter().map(|k| bs58::encode(k).into_string()));
        keys.extend(meta.loaded_readonly_addresses.iter().map(|k| bs58::encode(k).into_string()));
    }
    let mut out = Vec::new();
    for (i, ix) in message.instructions.iter().enumerate() {
        if keys.get(ix.program_id_index as usize).map(String::as_str) != Some(PUMP_PROGRAM) { continue; }
        let Some(kind) = ix_kind(&ix.data) else { continue };
        let mint = if kind == "CREATE" || kind == "CREATE_V2" { ix.accounts.first().and_then(|i| keys.get(*i as usize)).cloned() } else { None };
        out.push((format!("outer:{i}"), kind.to_string(), mint));
    }
    if let Some(meta) = info.meta.as_ref() {
        for group in &meta.inner_instructions {
            for (i, ix) in group.instructions.iter().enumerate() {
                if keys.get(ix.program_id_index as usize).map(String::as_str) != Some(PUMP_PROGRAM) { continue; }
                let Some(kind) = ix_kind(&ix.data) else { continue };
                let mint = if kind == "CREATE" || kind == "CREATE_V2" { ix.accounts.first().and_then(|i| keys.get(*i as usize)).cloned() } else { None };
                out.push((format!("outer:{}:inner:{}:stack:{}", group.index, i, ix.stack_height.map(|v| v.to_string()).unwrap_or_else(|| "unknown".to_string())), kind.to_string(), mint));
            }
        }
    }
    out
}

fn target_for(targets: &HashMap<String, Target>, signature: &str, mint: Option<&str>, slot: u64, received_ns: i64) -> Option<Target> {
    let target = targets.get(&format!("sig:{signature}")).or_else(|| mint.and_then(|m| targets.get(m)))?.clone();
    let in_window = if target.receive_ns > 0 && received_ns > 0 {
        received_ns >= target.receive_ns.saturating_sub(5_000_000) && received_ns <= target.receive_ns.saturating_add(120_000_000_000)
    } else {
        slot >= target.slot && slot <= target.slot.saturating_add(1_000)
    };
    in_window.then_some(target)
}

fn emit(runtime: &mut Runtime, event: TapeEvent) -> Result<()> {
    if runtime.seen.contains(&event.dedupe_key) { return Ok(()); }
    let line = serde_json::to_string(&event)?;
    if let Some(file) = runtime.output.as_mut() {
        writeln!(file, "{line}")?;
        file.flush()?;
    }
    runtime.seen.insert(event.dedupe_key);
    runtime.events_written += 1;
    Ok(())
}

fn handle_frame(runtime: &mut Runtime, metadata: &Value, raw: &[u8], source_path: &str) -> Result<()> {
    runtime.frames_scanned += 1;
    let received_ns = as_i64(metadata.get("receivedAtUnixNs"));
    let source_evidence_id = strv(metadata.get("evidenceId"));
    let raw_sha = strv(metadata.get("rawSha256"));
    let update = match SubscribeUpdate::decode(raw) { Ok(v) => v, Err(_) => return Ok(()) };
    let Some(UpdateOneof::Transaction(tx)) = update.update_oneof else { return Ok(()) };
    let Some(info) = tx.transaction.as_ref() else { return Ok(()) };
    let signature = bs58::encode(&info.signature).into_string();
    let slot = tx.slot;
    let tx_index = info.index as u64;
    let pump_ixs = collect_pump_ixs(info);
    let creates: Vec<_> = pump_ixs.iter().filter(|(_, kind, _)| kind == "CREATE" || kind == "CREATE_V2").collect();
    let mut matched: Option<Target> = None;
    if let Some(t) = runtime.targets.get(&format!("sig:{signature}")) { matched = Some(t.clone()); }
    for (_, _, mint) in &creates {
        if let Some(mint) = mint { if let Some(t) = target_for(&runtime.targets, &signature, Some(mint), slot, received_ns) { matched = Some(t); } }
    }
    let mut events = Vec::new();
    if let Some(meta) = info.meta.as_ref() {
        for line in &meta.log_messages {
            if let Some(encoded) = line.strip_prefix("Program data: ") {
                if let Ok(bytes) = B64.decode(encoded) { if let Some(event) = trade_event(&bytes) { events.push(event); } }
            }
        }
    }
    for (event_index, event) in events.into_iter().enumerate() {
        let (mint, sol, token, is_buy, wallet, timestamp, vsol, vtoken, rsol, rtoken) = event;
        let Some(target) = target_for(&runtime.targets, &signature, Some(&mint), slot, received_ns).or(matched.clone()) else { continue };
        if target.mint != mint { continue; }
        runtime.matching_frames += 1;
        let side = if is_buy { "buy" } else { "sell" };
        let ix = pump_ixs.iter().find(|(_, kind, _)| if is_buy { kind.starts_with("BUY") } else { kind.starts_with("SELL") }).cloned();
        emit(runtime, TapeEvent {
            schema: "8ycb-canonical-pump-evidence",
            schema_version: "v1",
            dedupe_key: format!("{}|{}|{}", target.ordinal, signature, event_index),
            canonical_ordinal: target.ordinal,
            launch_signature: target.signature,
            mint,
            evidence_kind: "TRADE_EVENT".to_string(),
            transaction_signature: signature.clone(),
            slot,
            transaction_index: tx_index,
            instruction_coordinate: ix.as_ref().map(|v| v.0.clone()),
            pump_instruction_kind: ix.as_ref().map(|v| v.1.clone()),
            side: Some(side.to_string()),
            wallet: Some(wallet),
            quote_lamports: Some(sol),
            token_amount_raw: Some(token),
            event_timestamp_s: Some(timestamp),
            virtual_sol_reserves_after: Some(vsol),
            virtual_token_reserves_after: Some(vtoken),
            real_sol_reserves_after: Some(rsol),
            real_token_reserves_after: Some(rtoken),
            source_received_ns: received_ns,
            source_evidence_id: source_evidence_id.clone(),
            source_path: source_path.to_string(),
            raw_sha256: raw_sha.clone(),
            raw_base64: B64.encode(raw),
            exact_ordering: format!("slot:{slot}|tx:{tx_index}|event:{event_index}"),
            created_at_utc: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
        })?;
    }
    for (_, kind, mint) in creates {
        let Some(mint) = mint else { continue };
        let Some(target) = target_for(&runtime.targets, &signature, Some(&mint), slot, received_ns) else { continue };
        emit(runtime, TapeEvent {
            schema: "8ycb-canonical-pump-evidence", schema_version: "v1",
            dedupe_key: format!("{}|{}|CREATE", target.ordinal, signature), canonical_ordinal: target.ordinal,
            launch_signature: target.signature, mint, evidence_kind: "CREATE".to_string(), transaction_signature: signature.clone(),
            slot, transaction_index: tx_index, instruction_coordinate: None, pump_instruction_kind: Some(kind), side: None,
            wallet: None, quote_lamports: None, token_amount_raw: None, event_timestamp_s: None,
            virtual_sol_reserves_after: None, virtual_token_reserves_after: None, real_sol_reserves_after: None, real_token_reserves_after: None,
            source_received_ns: received_ns, source_evidence_id: source_evidence_id.clone(), source_path: source_path.to_string(), raw_sha256: raw_sha.clone(), raw_base64: B64.encode(raw), exact_ordering: format!("slot:{slot}|tx:{tx_index}|create"), created_at_utc: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true),
        })?;
    }
    Ok(())
}

fn scan_reader<R: Read>(runtime: &mut Runtime, mut decoder: R, path: &Path) -> Result<()> {
    let mut magic = [0u8; 8];
    if decoder.read_exact(&mut magic).is_err() || &magic != FRAME_MAGIC { return Ok(()); }
    loop {
        let mut prefix = [0u8; 8];
        if decoder.read_exact(&mut prefix).is_err() { break; }
        let metadata_len = u32::from_le_bytes(prefix[..4].try_into().unwrap()) as usize;
        let raw_len = u32::from_le_bytes(prefix[4..].try_into().unwrap()) as usize;
        if metadata_len > 16 * 1024 * 1024 || raw_len > 128 * 1024 * 1024 { break; }
        let mut metadata_bytes = vec![0u8; metadata_len];
        if decoder.read_exact(&mut metadata_bytes).is_err() { break; }
        let mut raw = vec![0u8; raw_len];
        if decoder.read_exact(&mut raw).is_err() { break; }
        let mut digest = [0u8; 32];
        if decoder.read_exact(&mut digest).is_err() { break; }
        let metadata: Value = match serde_json::from_slice(&metadata_bytes) { Ok(v) => v, Err(_) => continue };
        handle_frame(runtime, &metadata, &raw, &path.to_string_lossy())?;
    }
    Ok(())
}

fn scan_file(runtime: &mut Runtime, path: &Path) -> Result<()> {
    let mut file = File::open(path)?;
    let mut prefix = [0u8; 8];
    file.read_exact(&mut prefix)?;
    file.seek(SeekFrom::Start(0))?;
    if &prefix == FRAME_MAGIC {
        scan_reader(runtime, file, path)
    } else {
        let decoder = zstd::Decoder::new(file)?;
        scan_reader(runtime, decoder, path)
    }
}

fn source_files(root: &Path, out: &mut Vec<PathBuf>) -> Result<()> {
    if !root.exists() { return Ok(()); }
    for entry in fs::read_dir(root)? {
        let path = entry?.path();
        if path.is_dir() { source_files(&path, out)?; }
        else if path.file_name().and_then(|v| v.to_str()).is_some_and(|v| v == "evidence.frames.zst" || v == "evidence.frames.partial" || v == "evidence.frames") { out.push(path); }
    }
    Ok(())
}

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let source = PathBuf::from(args.get(1).cloned().unwrap_or_else(|| "/mnt/HC_Volume_106775198/edgesniffer-research/2cq-yellowstone-full-meta-prospective-v1".to_string()));
    let root = PathBuf::from(args.get(2).cloned().unwrap_or_else(|| "/var/lib/8ycb-canonical/pump-tape".to_string()));
    let ledger = PathBuf::from(args.get(3).cloned().unwrap_or_else(|| "/var/lib/8ycb-canonical/launch-identity-ledger.jsonl".to_string()));
    let once = args.iter().any(|v| v == "--once");
    fs::create_dir_all(&root)?;
    let targets = load_targets(&ledger)?;
    let output_path = root.join("evidence.jsonl");
    let mut runtime = Runtime { targets, output: Some(OpenOptions::new().create(true).append(true).open(&output_path)?), ..Runtime::default() };
    if output_path.exists() {
        for line in fs::read_to_string(&output_path).unwrap_or_default().lines() {
            if let Ok(row) = serde_json::from_str::<Value>(line) { if let Some(k) = row.get("dedupeKey").and_then(Value::as_str) { runtime.seen.insert(k.to_string()); } }
        }
    }
    loop {
        let mut files = Vec::new(); source_files(&source, &mut files)?; files.sort();
        for path in files { let _ = scan_file(&mut runtime, &path); }
        let health = serde_json::json!({"schema":"8ycb-canonical-pump-tape-health-v1","updatedAtUtc":Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos,true),"sourceRoot":source,"canonicalTargets":runtime.targets.len()/2,"framesScanned":runtime.frames_scanned,"matchingFrames":runtime.matching_frames,"eventsWritten":runtime.events_written,"dedupeEntries":runtime.seen.len(),"realMarketSubmissionCount":0,"realMarketSubmissionEnabled":false});
        let tmp = root.join("health.json.tmp"); fs::write(&tmp, serde_json::to_vec_pretty(&health)?)?; fs::rename(tmp, root.join("health.json"))?;
        if once { break; }
        thread::sleep(Duration::from_secs(10));
    }
    Ok(())
}
