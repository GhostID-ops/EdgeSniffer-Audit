//! Bounded offline candidate-specific context extraction for the frozen kEFi sample.
//!
//! This reads closed source-spool partitions, verifies frame checksums, decodes
//! retained Yellowstone protobufs, and emits only pre-entry evidence related to
//! the selected mint of each frozen BUY. It is not a collector or finalizer.

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use prost::Message;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::{self, File};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use yellowstone_grpc_proto::geyser::{subscribe_update::UpdateOneof, SubscribeUpdate};
use zstd::stream::read::Decoder;

const MAGIC: &[u8; 8] = b"PEL1FRM\0";
const PUMP_PROGRAM: &str = "6EF8rrecthR5Dkzon8Nwu78nWWeC3qWZid9M2n6P";
const PUMPSWAP_PROGRAM: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const ROUTER_PROGRAM: &str = "6Vo3245eszAb5wuqEMw8mGdbfRUdKbHhDHP5LcaGuTAB";
const TRACKED_WALLET: &str = "kEFiAX3jo5NmemysQov342TZ9mGh6yp92GDRjhA8XDf";

#[derive(Debug, Deserialize)]
struct Structural {
    trades: Vec<Trade>,
}

#[derive(Debug, Deserialize)]
struct Trade {
    ordinal: usize,
    direction: String,
    #[serde(rename = "tokenDeltas")]
    token_deltas: HashMap<String, i64>,
}

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
}

#[derive(Debug, Default)]
struct State {
    selected_mint: String,
    selected_signature: String,
    continuity_epoch: Option<u64>,
    related_frames: u64,
    pump_frames: u64,
    pumpswap_frames: u64,
    router_frames: u64,
    create_like_frames: u64,
    buy_like_frames: u64,
    sell_like_frames: u64,
    program_data_frames: u64,
    wallet_token_pre_seen: u64,
    wallet_token_post_seen: u64,
    first_related_ns: Option<u64>,
    last_related_ns: Option<u64>,
    min_related_slot: Option<u64>,
    max_related_slot: Option<u64>,
    log_kinds: BTreeMap<String, u64>,
    exact_events: u64,
    exact_buys: u64,
    exact_sells: u64,
    distinct_pools: HashSet<String>,
    last_exact_side: Option<String>,
    last_exact_timestamp: Option<i64>,
    last_exact_slot: Option<u64>,
    last_exact_pre_pool_base: Option<u64>,
    last_exact_pre_pool_quote: Option<u64>,
    last_exact_base_amount: Option<u64>,
    last_exact_pool_quote_delta: Option<u64>,
    exact_event_decode_failures: u64,
    entry_exact_events: u64,
    entry_exact_side: Option<String>,
    entry_exact_timestamp: Option<i64>,
    entry_exact_slot: Option<u64>,
    entry_exact_pre_pool_base: Option<u64>,
    entry_exact_pre_pool_quote: Option<u64>,
    entry_exact_base_amount: Option<u64>,
    entry_exact_pool_quote_delta: Option<u64>,
}

#[derive(Debug, Serialize)]
struct ContextRow {
    ordinal: usize,
    selected_mint: String,
    continuity_epoch: Option<u64>,
    related_frames: u64,
    pump_frames: u64,
    pumpswap_frames: u64,
    router_frames: u64,
    create_like_frames: u64,
    buy_like_frames: u64,
    sell_like_frames: u64,
    program_data_frames: u64,
    wallet_token_pre_seen: u64,
    wallet_token_post_seen: u64,
    first_related_ns: Option<u64>,
    last_related_ns: Option<u64>,
    min_related_slot: Option<u64>,
    max_related_slot: Option<u64>,
    log_kinds: BTreeMap<String, u64>,
    exact_pump_swap_events: u64,
    exact_pump_swap_buys: u64,
    exact_pump_swap_sells: u64,
    distinct_pools: usize,
    last_exact_event_side: Option<String>,
    last_exact_event_timestamp: Option<i64>,
    last_exact_event_slot: Option<u64>,
    last_exact_pre_pool_base: Option<u64>,
    last_exact_pre_pool_quote: Option<u64>,
    last_exact_base_amount: Option<u64>,
    last_exact_pool_quote_delta: Option<u64>,
    exact_event_decode_failures: u64,
    entry_exact_events: u64,
    entry_exact_side: Option<String>,
    entry_exact_timestamp: Option<i64>,
    entry_exact_slot: Option<u64>,
    entry_exact_pre_pool_base: Option<u64>,
    entry_exact_pre_pool_quote: Option<u64>,
    entry_exact_base_amount: Option<u64>,
    entry_exact_pool_quote_delta: Option<u64>,
}

#[derive(Debug, Serialize)]
struct Summary {
    schema: &'static str,
    source_partitions_scanned: Vec<String>,
    open_partitions_excluded: Vec<String>,
    frozen_buy_windows: usize,
    source_frames_scanned: u64,
    protobuf_transactions_decoded: u64,
    candidate_related_frames: u64,
    candidate_windows_with_related_frames: usize,
    candidate_windows_with_create_like_frames: usize,
    candidate_windows_with_wallet_token_state: usize,
    candidate_windows_with_exact_events: usize,
    candidate_windows_with_exact_buys: usize,
    candidate_windows_with_exact_sells: usize,
    exact_event_decode_failures: u64,
}

struct DecodedTransaction {
    signature: String,
    slot: u64,
    keys: HashSet<String>,
    pumpswap: bool,
    pump: bool,
    router: bool,
    wallet_pre_mints: HashSet<String>,
    wallet_post_mints: HashSet<String>,
    log_kinds: BTreeMap<String, u64>,
    program_data: bool,
    pump_swap_events: Vec<PumpSwapEvent>,
    exact_event_decode_failures: u64,
}

#[derive(Debug)]
struct PumpSwapEvent {
    side: String,
    timestamp: i64,
    base_amount: u64,
    pool_base: u64,
    pool_quote: u64,
    pool_quote_delta: u64,
    pool: String,
    user: String,
    base_mint: String,
}

#[derive(Debug)]
struct PumpSwapInstruction {
    side: String,
    pool: String,
    user: String,
    base_mint: String,
}

struct Reader<'a> {
    bytes: &'a [u8],
    offset: usize,
}

impl<'a> Reader<'a> {
    fn take(&mut self, count: usize) -> Result<&'a [u8]> {
        let end = self.offset.checked_add(count).context("borsh offset overflow")?;
        if end > self.bytes.len() { bail!("borsh payload truncated"); }
        let value = &self.bytes[self.offset..end];
        self.offset = end;
        Ok(value)
    }
    fn u64(&mut self) -> Result<u64> { Ok(u64::from_le_bytes(self.take(8)?.try_into()?)) }
    fn i64(&mut self) -> Result<i64> { Ok(i64::from_le_bytes(self.take(8)?.try_into()?)) }
    fn i128(&mut self) -> Result<i128> { Ok(i128::from_le_bytes(self.take(16)?.try_into()?)) }
    fn bool(&mut self) -> Result<bool> { Ok(self.take(1)?[0] != 0) }
    fn string(&mut self) -> Result<()> { let len = u32::from_le_bytes(self.take(4)?.try_into()?) as usize; self.take(len)?; Ok(()) }
    fn pubkey(&mut self) -> Result<String> { Ok(bs58::encode(self.take(32)?).into_string()) }
}

fn read_json<T: for<'a> Deserialize<'a>>(path: &Path) -> Result<T> {
    Ok(serde_json::from_slice(&fs::read(path)?)?)
}

fn instruction_text(log: &str) -> Option<&'static str> {
    if log.contains("Instruction: Create") || log.contains("Instruction: CreateV2") {
        Some("CREATE")
    } else if log.contains("Instruction: Buy") {
        Some("BUY")
    } else if log.contains("Instruction: Sell") {
        Some("SELL")
    } else {
        None
    }
}

fn account_keys(
    message: &yellowstone_grpc_proto::solana::storage::confirmed_block::Message,
    meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta,
) -> HashSet<String> {
    let mut keys = message
        .account_keys
        .iter()
        .map(|key| bs58::encode(key).into_string())
        .collect::<HashSet<_>>();
    keys.extend(
        meta.loaded_writable_addresses
            .iter()
            .chain(meta.loaded_readonly_addresses.iter())
            .map(|key| bs58::encode(key).into_string()),
    );
    keys
}

const BUY_DISC: &[u8] = &[102, 6, 61, 18, 1, 218, 235, 234];
const BUY_EXACT_DISC: &[u8] = &[198, 46, 21, 82, 180, 217, 232, 112];
const SELL_DISC: &[u8] = &[51, 230, 133, 164, 1, 127, 131, 173];
const BUY_EVENT_DISC: &[u8] = &[103, 244, 82, 31, 44, 245, 119, 119];
const SELL_EVENT_DISC: &[u8] = &[62, 47, 55, 10, 165, 3, 220, 42];

fn decode_pumpswap_instructions(
    message: &yellowstone_grpc_proto::solana::storage::confirmed_block::Message,
    meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta,
) -> Vec<PumpSwapInstruction> {
    let mut keys = message
        .account_keys
        .iter()
        .map(|key| bs58::encode(key).into_string())
        .collect::<Vec<_>>();
    keys.extend(
        meta.loaded_writable_addresses
            .iter()
            .chain(meta.loaded_readonly_addresses.iter())
            .map(|key| bs58::encode(key).into_string()),
    );
    let mut output = Vec::new();
    let mut visit = |program_id_index: u32, accounts: &[u8], data: &[u8]| {
        let Some(program) = keys.get(program_id_index as usize) else { return };
        if program != PUMPSWAP_PROGRAM || data.len() < 24 { return; }
        let decoded = data;
        if decoded.len() < 24 { return; }
        let side = if decoded.starts_with(BUY_DISC) || decoded.starts_with(BUY_EXACT_DISC) {
            "BUY"
        } else if decoded.starts_with(SELL_DISC) {
            "SELL"
        } else { return };
        let account_keys = accounts.iter().filter_map(|index| keys.get(*index as usize)).collect::<Vec<_>>();
        if account_keys.len() < 5 { return; }
        output.push(PumpSwapInstruction {
            side: side.to_string(),
            pool: account_keys[0].to_string(),
            user: account_keys[1].to_string(),
            base_mint: account_keys[3].to_string(),
        });
    };
    for instruction in &message.instructions {
        visit(instruction.program_id_index, &instruction.accounts, &instruction.data);
    }
    for group in &meta.inner_instructions {
        for instruction in &group.instructions {
            visit(instruction.program_id_index, &instruction.accounts, &instruction.data);
        }
    }
    output
}

fn decode_pumpswap_event(payload: &[u8]) -> Result<Option<PumpSwapEvent>> {
    let mut reader = Reader { bytes: payload, offset: 0 };
    let discriminator = reader.take(8)?;
    let side = if discriminator == BUY_EVENT_DISC {
        "BUY"
    } else if discriminator == SELL_EVENT_DISC {
        "SELL"
    } else {
        return Ok(None);
    };
    let timestamp = reader.i64()?;
    let base_amount = reader.u64()?;
    let _limit_quote_amount = reader.u64()?;
    let _user_base_reserves = reader.u64()?;
    let _user_quote_reserves = reader.u64()?;
    let pool_base = reader.u64()?;
    let pool_quote = reader.u64()?;
    let _curve_quote_amount = reader.u64()?;
    let _lp_fee_bps = reader.u64()?;
    let _lp_fee = reader.u64()?;
    let _protocol_fee_bps = reader.u64()?;
    let _protocol_fee = reader.u64()?;
    let pool_quote_delta = reader.u64()?;
    let _user_quote_amount = reader.u64()?;
    let pool = reader.pubkey()?;
    let user = reader.pubkey()?;
    let _user_base_account = reader.pubkey()?;
    let _user_quote_account = reader.pubkey()?;
    let _protocol_recipient = reader.pubkey()?;
    let _protocol_recipient_account = reader.pubkey()?;
    let _coin_creator = reader.pubkey()?;
    let _coin_creator_bps = reader.u64()?;
    let _coin_creator_fee = reader.u64()?;
    if side == "BUY" {
        let _track_volume = reader.bool()?;
        let _total_unclaimed = reader.u64()?;
        let _total_claimed = reader.u64()?;
        let _current_sol_volume = reader.u64()?;
        let _last_update_timestamp = reader.i64()?;
        let _min_base_amount = reader.u64()?;
        reader.string()?;
    }
    let _cashback_bps = reader.u64()?;
    let _cashback = reader.u64()?;
    let _buyback_bps = reader.u64()?;
    let _buyback_fee = reader.u64()?;
    let _virtual_quote_reserves = reader.i128()?;
    let _can_boost = reader.bool()?;
    let _base_supply = reader.u64()?;
    let remaining = payload.len().saturating_sub(reader.offset);
    if remaining == 16 {
        let _holder_rewards_bps = reader.u64()?;
        let _holder_rewards = reader.u64()?;
    } else if remaining == 24 {
        // Current recorded PumpSwap variant carries a 24-byte trailing
        // extension after the reserve fields. Preserve and consume it
        // exactly; the state-bearing reserve transition is already decoded.
        let _extension_0 = reader.u64()?;
        let _extension_1 = reader.u64()?;
        let _extension_2 = reader.u64()?;
    }
    if reader.offset != payload.len() {
        let trailing_hex = payload[reader.offset..]
            .iter()
            .map(|byte| format!("{:02x}", byte))
            .collect::<String>();
        bail!(
            "pumpswap event trailing bytes: decoded_offset={} payload_len={} trailing={} trailing_hex={}",
            reader.offset,
            payload.len(),
            payload.len().saturating_sub(reader.offset),
            trailing_hex,
        );
    }
    Ok(Some(PumpSwapEvent { side: side.to_string(), timestamp, base_amount, pool_base, pool_quote, pool_quote_delta, pool, user, base_mint: String::new() }))
}

fn decode_pumpswap_events(
    meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta,
    instructions: &[PumpSwapInstruction],
) -> Result<(Vec<PumpSwapEvent>, u64)> {
    let mut events = Vec::new();
    for log in &meta.log_messages {
        let Some(encoded) = log.strip_prefix("Program data: ") else { continue };
        let Ok(payload) = BASE64_STANDARD.decode(encoded.trim()) else { continue };
        let recognized = payload.starts_with(BUY_EVENT_DISC) || payload.starts_with(SELL_EVENT_DISC);
        if !recognized {
            continue;
        }
        const EVENT_POOL_OFFSET: usize = 120;
        const EVENT_USER_OFFSET: usize = 152;
        if payload.len() < EVENT_USER_OFFSET + 32 {
            bail!("recognized PumpSwap event too short: {} bytes", payload.len());
        }
        let event_side = if payload.starts_with(BUY_EVENT_DISC) { "BUY" } else { "SELL" };
        let event_pool = bs58::encode(&payload[EVENT_POOL_OFFSET..EVENT_POOL_OFFSET + 32]).into_string();
        let event_user = bs58::encode(&payload[EVENT_USER_OFFSET..EVENT_USER_OFFSET + 32]).into_string();
        let matching = instructions.iter().filter(|instruction| {
            instruction.side == event_side && instruction.pool == event_pool && instruction.user == event_user
        }).collect::<Vec<_>>();
        if matching.len() != 1 {
            continue;
        }
        let mut event = decode_pumpswap_event(&payload)
            .with_context(|| format!("decoding matched PumpSwap {} event", event_side))?
            .context("matched PumpSwap event had no decoded value")?;
        event.base_mint = matching[0].base_mint.clone();
        events.push(event);
    }
    Ok((events, 0))
}

fn decode_transaction(raw: &[u8]) -> Result<Option<DecodedTransaction>> {
    let update = SubscribeUpdate::decode(raw)?;
    let Some(UpdateOneof::Transaction(update_tx)) = update.update_oneof else {
        return Ok(None);
    };
    let Some(info) = update_tx.transaction else {
        return Ok(None);
    };
    let Some(transaction) = info.transaction else {
        return Ok(None);
    };
    let Some(message) = transaction.message else {
        return Ok(None);
    };
    let Some(meta) = info.meta else {
        return Ok(None);
    };
    let keys = account_keys(&message, &meta);
    let wallet_pre_mints = meta
        .pre_token_balances
        .iter()
        .filter(|balance| balance.owner == TRACKED_WALLET)
        .map(|balance| balance.mint.clone())
        .collect::<HashSet<_>>();
    let wallet_post_mints = meta
        .post_token_balances
        .iter()
        .filter(|balance| balance.owner == TRACKED_WALLET)
        .map(|balance| balance.mint.clone())
        .collect::<HashSet<_>>();
    let mut log_kinds = BTreeMap::new();
    let mut program_data = false;
    for log in &meta.log_messages {
        if log.contains("Program data: ") {
            program_data = true;
        }
        if let Some(kind) = instruction_text(&log) {
            *log_kinds.entry(kind.to_string()).or_default() += 1;
        }
    }
    let has_trade_logs = log_kinds.contains_key("BUY") || log_kinds.contains_key("SELL");
    let (pump_swap_events, exact_event_decode_failures) = if keys.contains(PUMPSWAP_PROGRAM)
        && program_data
        && has_trade_logs
    {
        let instructions = decode_pumpswap_instructions(&message, &meta);
        decode_pumpswap_events(&meta, &instructions)?
    } else {
        (Vec::new(), 0)
    };
    Ok(Some(DecodedTransaction {
        signature: bs58::encode(&info.signature).into_string(),
        slot: update_tx.slot,
        pumpswap: keys.contains(PUMPSWAP_PROGRAM),
        pump: keys.contains(PUMP_PROGRAM),
        router: keys.contains(ROUTER_PROGRAM),
        keys,
        wallet_pre_mints,
        wallet_post_mints,
        log_kinds,
        program_data,
        pump_swap_events,
        exact_event_decode_failures,
    }))
}

fn update_state(state: &mut State, tx: &DecodedTransaction, received_ns: u64) -> Result<()> {
    state.related_frames += 1;
    state.first_related_ns.get_or_insert(received_ns);
    state.last_related_ns = Some(received_ns);
    state.min_related_slot = Some(state.min_related_slot.map_or(tx.slot, |v| v.min(tx.slot)));
    state.max_related_slot = Some(state.max_related_slot.map_or(tx.slot, |v| v.max(tx.slot)));
    state.pump_frames += u64::from(tx.pump);
    state.pumpswap_frames += u64::from(tx.pumpswap);
    state.router_frames += u64::from(tx.router);
    state.wallet_token_pre_seen += u64::from(tx.wallet_pre_mints.contains(&state.selected_mint));
    state.wallet_token_post_seen += u64::from(tx.wallet_post_mints.contains(&state.selected_mint));
    state.program_data_frames += u64::from(tx.program_data);
    state.exact_event_decode_failures += tx.exact_event_decode_failures;
    for (kind, count) in &tx.log_kinds {
        *state.log_kinds.entry(kind.clone()).or_default() += count;
        match kind.as_str() {
            "CREATE" => state.create_like_frames += count,
            "BUY" => state.buy_like_frames += count,
            "SELL" => state.sell_like_frames += count,
            _ => {}
        }
    }
    for event in &tx.pump_swap_events {
        if event.base_mint != state.selected_mint {
            continue;
        }
        state.exact_events += 1;
        match event.side.as_str() {
            "BUY" => state.exact_buys += 1,
            "SELL" => state.exact_sells += 1,
            _ => bail!("unknown exact PumpSwap side {}", event.side),
        }
        state.distinct_pools.insert(event.pool.clone());
        state.last_exact_side = Some(event.side.clone());
        state.last_exact_timestamp = Some(event.timestamp);
        state.last_exact_slot = Some(tx.slot);
        state.last_exact_base_amount = Some(event.base_amount);
        state.last_exact_pool_quote_delta = Some(event.pool_quote_delta);
        state.last_exact_pre_pool_base = Some(match event.side.as_str() {
            "BUY" => event.pool_base.checked_add(event.base_amount),
            "SELL" => event.pool_base.checked_sub(event.base_amount),
            _ => None,
        }.context("exact PumpSwap base reserve arithmetic overflow")?);
        state.last_exact_pre_pool_quote = Some(match event.side.as_str() {
            "BUY" => event.pool_quote.checked_sub(event.pool_quote_delta),
            "SELL" => event.pool_quote.checked_add(event.pool_quote_delta),
            _ => None,
        }.context("exact PumpSwap quote reserve arithmetic overflow")?);
        if received_ns == 0 && tx.signature == state.selected_signature {
            state.entry_exact_events += 1;
            state.entry_exact_side = Some(event.side.clone());
            state.entry_exact_timestamp = Some(event.timestamp);
            state.entry_exact_slot = Some(tx.slot);
            state.entry_exact_pre_pool_base = state.last_exact_pre_pool_base;
            state.entry_exact_pre_pool_quote = state.last_exact_pre_pool_quote;
            state.entry_exact_base_amount = Some(event.base_amount);
            state.entry_exact_pool_quote_delta = Some(event.pool_quote_delta);
        }
    }
    Ok(())
}

fn row_from_state(ordinal: usize, state: State) -> ContextRow {
    ContextRow {
        ordinal,
        selected_mint: state.selected_mint,
        continuity_epoch: state.continuity_epoch,
        related_frames: state.related_frames,
        pump_frames: state.pump_frames,
        pumpswap_frames: state.pumpswap_frames,
        router_frames: state.router_frames,
        create_like_frames: state.create_like_frames,
        buy_like_frames: state.buy_like_frames,
        sell_like_frames: state.sell_like_frames,
        program_data_frames: state.program_data_frames,
        wallet_token_pre_seen: state.wallet_token_pre_seen,
        wallet_token_post_seen: state.wallet_token_post_seen,
        first_related_ns: state.first_related_ns,
        last_related_ns: state.last_related_ns,
        min_related_slot: state.min_related_slot,
        max_related_slot: state.max_related_slot,
        log_kinds: state.log_kinds,
        exact_pump_swap_events: state.exact_events,
        exact_pump_swap_buys: state.exact_buys,
        exact_pump_swap_sells: state.exact_sells,
        distinct_pools: state.distinct_pools.len(),
        last_exact_event_side: state.last_exact_side,
        last_exact_event_timestamp: state.last_exact_timestamp,
        last_exact_event_slot: state.last_exact_slot,
        last_exact_pre_pool_base: state.last_exact_pre_pool_base,
        last_exact_pre_pool_quote: state.last_exact_pre_pool_quote,
        last_exact_base_amount: state.last_exact_base_amount,
        last_exact_pool_quote_delta: state.last_exact_pool_quote_delta,
        exact_event_decode_failures: state.exact_event_decode_failures,
        entry_exact_events: state.entry_exact_events,
        entry_exact_side: state.entry_exact_side,
        entry_exact_timestamp: state.entry_exact_timestamp,
        entry_exact_slot: state.entry_exact_slot,
        entry_exact_pre_pool_base: state.entry_exact_pre_pool_base,
        entry_exact_pre_pool_quote: state.entry_exact_pre_pool_quote,
        entry_exact_base_amount: state.entry_exact_base_amount,
        entry_exact_pool_quote_delta: state.entry_exact_pool_quote_delta,
    }
}

fn scan_partition(
    path: &Path,
    intervals: &[(u64, u64, usize, String)],
    next_interval: &mut usize,
    active: &mut Vec<(u64, usize, String)>,
    entry_targets: &BTreeMap<u64, Vec<(usize, String)>>,
    states: &mut BTreeMap<usize, State>,
    scanned: &mut u64,
    decoded: &mut u64,
    related: &mut u64,
) -> Result<()> {
    let mut decoder = Decoder::new(BufReader::new(File::open(path)?))?;
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
        if Sha256::digest(&body).as_slice() != expected {
            bail!("frame checksum mismatch in {}", path.display());
        }
        *scanned += 1;
        let metadata: FrameMetadata = serde_json::from_slice(&body[..metadata_len])?;
        let Some(received_ns) = metadata.received_at_unix_ns else { continue };
        while *next_interval < intervals.len() && intervals[*next_interval].0 <= received_ns {
            let (_, end_ns, ordinal, mint) = &intervals[*next_interval];
            active.push((*end_ns, *ordinal, mint.clone()));
            *next_interval += 1;
        }
        active.retain(|(end_ns, _, _)| *end_ns > received_ns);
        if active.is_empty() && !entry_targets.contains_key(&received_ns) {
            continue;
        }
        let Some(transaction) = decode_transaction(&body[metadata_len..])? else { continue };
        *decoded += 1;
        for (_, ordinal, mint) in active.iter() {
            if transaction.keys.contains(mint) {
                if let Some(state) = states.get_mut(ordinal) {
                    update_state(state, &transaction, received_ns)?;
                    *related += 1;
                }
            }
        }
        if let Some(targets) = entry_targets.get(&received_ns) {
            for (ordinal, mint) in targets {
                if !transaction.keys.contains(mint) {
                    continue;
                }
                if let Some(state) = states.get_mut(ordinal) {
                    if transaction.signature == state.selected_signature {
                        update_state(state, &transaction, 0)?;
                    }
                }
            }
        }
    }
    Ok(())
}

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let root = PathBuf::from(args.next().context("missing root")?);
    let structural_path = PathBuf::from(args.next().context("missing structural json")?);
    let freeze_path = PathBuf::from(args.next().context("missing freeze json")?);
    let output_path = PathBuf::from(args.next().context("missing output jsonl")?);
    let summary_path = PathBuf::from(args.next().context("missing summary json")?);
    let structural: Structural = read_json(&structural_path)?;
    let freeze: Freeze = read_json(&freeze_path)?;
    let freeze_by_ordinal = freeze
        .items
        .into_iter()
        .map(|item| (item.ordinal, item))
        .collect::<HashMap<_, _>>();

    let mut intervals = Vec::new();
    let mut entry_targets: BTreeMap<u64, Vec<(usize, String)>> = BTreeMap::new();
    let mut states = BTreeMap::new();
    for trade in structural.trades {
        if trade.direction != "BUY" {
            continue;
        }
        let Some((mint, _)) = trade.token_deltas.iter().find(|(_, delta)| **delta > 0) else {
            continue;
        };
        let item = freeze_by_ordinal
            .get(&trade.ordinal)
            .context("BUY ordinal missing from freeze")?;
        let manifest = root
            .join("data/source-spool-materialized")
            .join(&item.signature)
            .join("manifest.json");
        let window: WindowManifest = read_json(&manifest)
            .with_context(|| format!("reading {}", manifest.display()))?;
        intervals.push((window.start_ns, window.end_ns, trade.ordinal, mint.clone()));
        entry_targets.entry(window.end_ns).or_default().push((trade.ordinal, mint.clone()));
        states.insert(
            trade.ordinal,
            State {
                selected_mint: mint.clone(),
                selected_signature: item.signature.clone(),
                continuity_epoch: item.continuity_epoch,
                ..State::default()
            },
        );
    }
    intervals.sort_by_key(|item| item.0);
    let mut next_interval = 0usize;
    let mut active = Vec::new();
    let mut scanned = 0u64;
    let mut decoded = 0u64;
    let mut related = 0u64;
    let mut partitions = Vec::new();
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
        scan_partition(
            &path,
            &intervals,
            &mut next_interval,
            &mut active,
            &entry_targets,
            &mut states,
            &mut scanned,
            &mut decoded,
            &mut related,
        )?;
        partitions.push(key.to_string());
        eprintln!("scanned {}: {} frames", key, scanned);
    }
    let rows = states
        .into_iter()
        .map(|(ordinal, state)| row_from_state(ordinal, state))
        .collect::<Vec<_>>();
    let summary = Summary {
        schema: "KEFI_CANDIDATE_SPECIFIC_CONTEXT_SUMMARY_V1",
        source_partitions_scanned: partitions,
        open_partitions_excluded: vec!["2026/10/05/03".to_string()],
        frozen_buy_windows: rows.len(),
        source_frames_scanned: scanned,
        protobuf_transactions_decoded: decoded,
        candidate_related_frames: related,
        candidate_windows_with_related_frames: rows.iter().filter(|row| row.related_frames > 0).count(),
        candidate_windows_with_create_like_frames: rows.iter().filter(|row| row.create_like_frames > 0).count(),
        candidate_windows_with_wallet_token_state: rows
            .iter()
            .filter(|row| row.wallet_token_pre_seen + row.wallet_token_post_seen > 0)
            .count(),
        candidate_windows_with_exact_events: rows.iter().filter(|row| row.exact_pump_swap_events > 0).count(),
        candidate_windows_with_exact_buys: rows.iter().filter(|row| row.exact_pump_swap_buys > 0).count(),
        candidate_windows_with_exact_sells: rows.iter().filter(|row| row.exact_pump_swap_sells > 0).count(),
        exact_event_decode_failures: rows.iter().map(|row| row.exact_event_decode_failures).sum(),
    };
    let mut output = BufWriter::new(File::create(output_path)?);
    for row in rows {
        serde_json::to_writer(&mut output, &row)?;
        output.write_all(b"\n")?;
    }
    output.flush()?;
    let mut summary_file = BufWriter::new(File::create(summary_path)?);
    serde_json::to_writer_pretty(&mut summary_file, &summary)?;
    summary_file.write_all(b"\n")?;
    summary_file.flush()?;
    println!("{}", serde_json::to_string(&summary)?);
    Ok(())
}
