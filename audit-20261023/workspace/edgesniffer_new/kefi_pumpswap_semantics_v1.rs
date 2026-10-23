//! Shared PumpSwap semantic decoder and authoritative Pool identity resolver.
//!
//! This is intentionally an isolated gRPC subscriber.  It neither receives a
//! tracked-wallet value nor opens promotion, PRE_H3, outcome, or PnL artifacts.
//! Its only output is a compact canonical public-event stream and a manifest.

#![allow(dead_code, unused_imports)]

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use futures::{stream, StreamExt};
use prost::Message;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque},
    env,
    fs::{self, File, OpenOptions},
    io::{BufWriter, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tonic::{
    metadata::AsciiMetadataValue,
    transport::{Channel, ClientTlsConfig},
    Request, Status,
};
use yellowstone_grpc_proto::geyser::{
    geyser_client::GeyserClient, subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest,
    SubscribeRequestFilterTransactions,
};

pub const PUMPSWAP_PROGRAM: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const WRAPPED_SOL_MINT: &str = "So11111111111111111111111111111111111111112";
const USDC_MINT: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
pub const BUY_DISC: &[u8] = &[102, 6, 61, 18, 1, 218, 235, 234];
pub const BUY_EXACT_DISC: &[u8] = &[198, 46, 21, 82, 180, 217, 232, 112];
pub const SELL_DISC: &[u8] = &[51, 230, 133, 164, 1, 127, 131, 173];
const BUY_EVENT_DISC: &[u8] = &[103, 244, 82, 31, 44, 245, 119, 119];
const SELL_EVENT_DISC: &[u8] = &[62, 47, 55, 10, 165, 3, 220, 42];
const SCHEMA: &str = "KEFI_TEACHER_INDEPENDENT_POOL_STATE_V1";

#[derive(Clone)]
struct Instruction {
    side: &'static str,
    variant: &'static str,
    coordinate: String,
    amount_mode: &'static str,
    amount_raw: u64,
    limit_raw: u64,
    accounts: Vec<String>,
}

/// One exact instruction/event pairing from a single full-meta Yellowstone
/// transaction.  This is source-order evidence only; callers must not label
/// `source_transaction_index` as finalized block order.
#[derive(Clone, Debug)]
pub struct ExactTransition {
    pub log_index: usize,
    pub instruction_coordinate: String,
    pub amount_mode: &'static str,
    pub instruction_amount_raw: u64,
    pub instruction_limit_raw: u64,
    pub event: Event,
}

const PUMPSWAP_POOL_INDEX: usize = 0;
const PUMPSWAP_USER_INDEX: usize = 1;
const PUMPSWAP_BUY_BASE_MINT_INDEX: usize = 3;
const PUMPSWAP_BUY_QUOTE_MINT_INDEX: usize = 4;
const PUMPSWAP_SELL_QUOTE_MINT_INDEX: usize = 3;
const PUMPSWAP_SELL_BASE_MINT_INDEX: usize = 4;
const PUMPSWAP_MIN_BUY_ACCOUNTS: usize = 23;
const PUMPSWAP_MIN_SELL_ACCOUNTS: usize = 21;
const CHOICESTATE_WARMUP_SECONDS: u64 = 60;

#[derive(Clone, Debug)]
pub struct Event {
    pub side: &'static str,
    pub base_amount: u64,
    pub instruction_limit_quote_raw: u64,
    pub user_base_raw: u64,
    pub user_quote_raw: u64,
    pub pool_base: u64,
    pub pool_quote: u64,
    pub curve_quote_raw: u64,
    pub pool_quote_delta: u64,
    pub lp_fee_bps: u64,
    pub lp_fee_raw: u64,
    pub protocol_fee_bps: u64,
    pub protocol_fee_raw: u64,
    pub user_quote_amount_raw: u64,
    pub coin_creator_bps: u64,
    pub coin_creator_fee_raw: u64,
    pub cashback_bps: u64,
    pub cashback_raw: u64,
    pub buyback_bps: u64,
    pub buyback_fee_raw: u64,
    pub virtual_quote_reserves_raw: i128,
    pub pool: String,
    pub user: String,
    pub mint: String,
}

/// A wallet transaction may contain several PumpSwap event records.  A
/// scientific selection is nevertheless one economic decision, identified by
/// the earliest BUY event for one authoritative selection mint.  More than
/// one teacher-action mint alongside a BUY is deliberately not guessable.
fn canonical_teacher_buy_event(
    decoded: &[(usize, Event)],
    identities: &BTreeMap<String, PoolIdentity>,
    wallet: Option<&str>,
) -> Result<Option<usize>> {
    let Some(wallet) = wallet else {
        return Ok(None);
    };
    let teacher_events = decoded
        .iter()
        .filter_map(|(index, event)| {
            (event.user == wallet).then(|| {
                identities
                    .get(&event.pool)
                    .map(|identity| (*index, event.side, identity.selection_mint.clone()))
                    .context("teacher event lacks authoritative Pool identity")
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let buy_mints = teacher_events
        .iter()
        .filter(|(_, side, _)| *side == "BUY")
        .map(|(_, _, mint)| mint.clone())
        .collect::<BTreeSet<_>>();
    if buy_mints.is_empty() {
        return Ok(None);
    }
    let all_action_mints = teacher_events
        .iter()
        .map(|(_, _, mint)| mint.clone())
        .collect::<BTreeSet<_>>();
    if all_action_mints.len() != 1 {
        bail!("AMBIGUOUS_MULTI_MINT_TEACHER_SELECTION: one transaction contains a teacher BUY across {} authoritative mints", all_action_mints.len());
    }
    Ok(teacher_events
        .iter()
        .filter(|(_, side, _)| *side == "BUY")
        .map(|(index, _, _)| *index)
        .min())
}

struct PendingEvent {
    event_index: usize,
    event: Event,
    received_ns: u128,
    sequence: u64,
    raw_sha: String,
    slot: u64,
    signature: String,
}

/// Immutable identity metadata.  This deliberately excludes reserves and all
/// post-event state: account lookup is used only to map a canonical Pool key
/// to its base/quote token identities.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PoolIdentity {
    pub pool: String,
    pub base_mint: String,
    pub quote_mint: String,
    pub selection_mint: String,
    pub pool_base_token_account: String,
    pub pool_quote_token_account: String,
    pub account_owner: String,
    pub layout: String,
    pub resolution_slot: u64,
}

#[derive(Deserialize)]
struct RpcResponse {
    result: Option<RpcResult>,
    error: Option<serde_json::Value>,
}
#[derive(Deserialize)]
struct RpcResult {
    context: RpcContext,
    value: Option<RpcAccount>,
}
#[derive(Deserialize)]
struct RpcContext {
    slot: u64,
}
#[derive(Deserialize)]
struct RpcAccount {
    owner: String,
    data: (String, String),
}
#[derive(Deserialize)]
struct RpcMultiResponse {
    result: Option<RpcMultiResult>,
    error: Option<serde_json::Value>,
}
#[derive(Deserialize)]
struct RpcMultiResult {
    context: RpcContext,
    value: Vec<Option<RpcAccount>>,
}

#[derive(Clone)]
struct Flow {
    at_ns: u128,
    side: &'static str,
    quote: u64,
    user: String,
}
#[derive(Clone)]
struct PoolState {
    first_ns: u128,
    first_id: String,
    last_ns: u128,
    last_sequence: u64,
    pool_base: u64,
    pool_quote: u64,
    fees: (u64, u64, u64, u64, u64),
    prior_pre_pool_quote: Option<u64>,
    last_buy_ns: Option<u128>,
    flows: VecDeque<Flow>,
}
#[derive(Serialize)]
struct ChoiceVector<'a> {
    pool: &'a str,
    mint: &'a str,
    quote_reserve_raw: u64,
    base_reserve_raw: u64,
    fee_tuple: (u64, u64, u64, u64, u64),
    observed_pool_age_seconds: u64,
    seconds_since_prior_observed_buy: Option<u64>,
    prior_pre_pool_quote_raw: Option<u64>,
    buy_count_5s: u64,
    buy_count_20s: u64,
    buy_count_60s: u64,
    buy_quote_flow_5s: u64,
    buy_quote_flow_20s: u64,
    buy_quote_flow_60s: u64,
    no_prior_buy: bool,
    sell_count_60s: u64,
    sell_quote_flow_60s: u64,
    first_public_observation_unix_ns: u128,
    last_event_unix_ns: u128,
    last_durable_output_sequence: u64,
}
#[derive(Serialize)]
struct ChoiceSnapshot<'a> {
    schema: &'a str,
    source_instance_id: &'a str,
    continuity_epoch: u64,
    teacher_signature: &'a str,
    teacher_slot: u64,
    teacher_event_index: usize,
    teacher_received_at_unix_ns: u128,
    teacher_durable_output_sequence: u64,
    selection_kind: &'a str,
    chosen_pool: &'a str,
    chosen_mint: &'a str,
    chosen_admitted: bool,
    wallet_ledger_boundary_unix_ns: u128,
    wallet_ledger_sequence_before: u64,
    wallet_ledger_tail_before: &'a str,
    wallet_ledger_record_sha256: &'a str,
    candidates: Vec<ChoiceVector<'a>>,
}

/// Compact, append-only wallet history shared by every future observer epoch.
/// It is intentionally separate from pool state: it only answers whether a
/// mint has already been observed in a direct kEfi wallet action since the
/// fixed analysis-history boundary.
#[derive(Clone, Serialize, Deserialize)]
struct ObservedWalletLedgerRecord {
    schema: String,
    sequence: u64,
    ledger_boundary_unix_ns: u128,
    mint: String,
    first_action_identity: String,
    signature: String,
    slot: u64,
    observed_at_unix_ns: u128,
    source_provenance_sha256: String,
    previous_record_sha256: String,
    source_kind: String,
    record_sha256: String,
}

struct ObservedWalletLedger {
    lock_path: PathBuf,
    file: File,
    boundary: u128,
    sequence: u64,
    tail: String,
    seen_mints: HashSet<String>,
}

fn vector<'a>(pool: &'a str, mint: &'a str, state: &'a PoolState, at_ns: u128) -> ChoiceVector<'a> {
    let mut counts = [0_u64; 3];
    let mut flow = [0_u64; 3];
    let mut sells = 0_u64;
    let mut sell_flow = 0_u64;
    for event in &state.flows {
        let age = at_ns.saturating_sub(event.at_ns);
        for (index, window) in [5_u128, 20, 60].iter().enumerate() {
            if age <= *window * 1_000_000_000 && event.side == "BUY" {
                counts[index] += 1;
                flow[index] = flow[index].saturating_add(event.quote);
            }
        }
        if age <= 60_000_000_000 && event.side == "SELL" {
            sells += 1;
            sell_flow = sell_flow.saturating_add(event.quote);
        }
    }
    ChoiceVector {
        pool,
        mint,
        quote_reserve_raw: state.pool_quote,
        base_reserve_raw: state.pool_base,
        fee_tuple: state.fees,
        observed_pool_age_seconds: at_ns.saturating_sub(state.first_ns) as u64 / 1_000_000_000,
        seconds_since_prior_observed_buy: state
            .last_buy_ns
            .map(|n| at_ns.saturating_sub(n) as u64 / 1_000_000_000),
        prior_pre_pool_quote_raw: state.prior_pre_pool_quote,
        buy_count_5s: counts[0],
        buy_count_20s: counts[1],
        buy_count_60s: counts[2],
        buy_quote_flow_5s: flow[0],
        buy_quote_flow_20s: flow[1],
        buy_quote_flow_60s: flow[2],
        no_prior_buy: state.last_buy_ns.is_none(),
        sell_count_60s: sells,
        sell_quote_flow_60s: sell_flow,
        first_public_observation_unix_ns: state.first_ns,
        last_event_unix_ns: state.last_ns,
        last_durable_output_sequence: state.last_sequence,
    }
}

fn apply_event(state: &mut PoolState, event: &Event, at_ns: u128, sequence: u64) {
    state.prior_pre_pool_quote = Some(state.pool_quote);
    state.pool_base = event.pool_base;
    state.pool_quote = event.pool_quote;
    state.fees = (
        event.lp_fee_bps,
        event.protocol_fee_bps,
        event.coin_creator_bps,
        event.cashback_bps,
        event.buyback_bps,
    );
    if event.side == "BUY" {
        state.last_buy_ns = Some(at_ns);
    }
    state.flows.push_back(Flow {
        at_ns,
        side: event.side,
        quote: event.pool_quote_delta,
        user: event.user.clone(),
    });
    while state
        .flows
        .front()
        .map(|f| at_ns.saturating_sub(f.at_ns) > 60_000_000_000)
        .unwrap_or(false)
    {
        state.flows.pop_front();
    }
    state.last_ns = at_ns;
    state.last_sequence = sequence;
}

struct Reader<'a> {
    bytes: &'a [u8],
    offset: usize,
}
impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8]> {
        let end = self
            .offset
            .checked_add(n)
            .context("event offset overflow")?;
        if end > self.bytes.len() {
            bail!("truncated PumpSwap event")
        }
        let value = &self.bytes[self.offset..end];
        self.offset = end;
        Ok(value)
    }
    fn u64(&mut self) -> Result<u64> {
        Ok(u64::from_le_bytes(self.take(8)?.try_into()?))
    }
    fn i64(&mut self) -> Result<i64> {
        Ok(i64::from_le_bytes(self.take(8)?.try_into()?))
    }
    fn i128(&mut self) -> Result<i128> {
        Ok(i128::from_le_bytes(self.take(16)?.try_into()?))
    }
    fn bool(&mut self) -> Result<bool> {
        Ok(self.take(1)?[0] != 0)
    }
    fn string(&mut self) -> Result<()> {
        let n = u32::from_le_bytes(self.take(4)?.try_into()?) as usize;
        self.take(n)?;
        Ok(())
    }
    fn pubkey(&mut self) -> Result<String> {
        Ok(bs58::encode(self.take(32)?).into_string())
    }
}

fn request(from_slot: Option<u64>, tracked_wallet: Option<&str>) -> SubscribeRequest {
    let mut transactions = HashMap::new();
    transactions.insert(
        "all_pumpswap".to_owned(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: Some(false),
            signature: None,
            account_include: vec![PUMPSWAP_PROGRAM.to_owned()],
            account_exclude: vec![],
            account_required: vec![],
            token_accounts: None,
        },
    );
    // The second OR-filter is ledger-only. It collects direct wallet actions
    // outside PumpSwap without putting them into public pool-state features.
    if let Some(wallet) = tracked_wallet {
        transactions.insert(
            "tracked_wallet_history".to_owned(),
            SubscribeRequestFilterTransactions {
                vote: Some(false),
                failed: Some(false),
                signature: None,
                account_include: vec![],
                account_exclude: vec![],
                account_required: vec![wallet.to_owned()],
                token_accounts: None,
            },
        );
    }
    SubscribeRequest {
        accounts: Default::default(),
        slots: Default::default(),
        transactions,
        transactions_status: Default::default(),
        blocks: Default::default(),
        blocks_meta: Default::default(),
        entry: Default::default(),
        accounts_data_slice: vec![],
        commitment: Some(CommitmentLevel::Confirmed as i32),
        ping: None,
        from_slot,
    }
}

fn direct_wallet_token_mints(
    meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta,
    wallet: &str,
) -> BTreeSet<String> {
    let mut before: BTreeMap<String, String> = BTreeMap::new();
    let mut after: BTreeMap<String, String> = BTreeMap::new();
    for balance in &meta.pre_token_balances {
        if balance.owner == wallet {
            before.insert(
                balance.mint.clone(),
                balance
                    .ui_token_amount
                    .as_ref()
                    .map(|amount| amount.amount.clone())
                    .unwrap_or_default(),
            );
        }
    }
    for balance in &meta.post_token_balances {
        if balance.owner == wallet {
            after.insert(
                balance.mint.clone(),
                balance
                    .ui_token_amount
                    .as_ref()
                    .map(|amount| amount.amount.clone())
                    .unwrap_or_default(),
            );
        }
    }
    before
        .keys()
        .chain(after.keys())
        .filter(|mint| before.get(*mint) != after.get(*mint))
        .cloned()
        .collect()
}

fn decode_instructions(
    message: &yellowstone_grpc_proto::solana::storage::confirmed_block::Message,
    meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta,
) -> Vec<Instruction> {
    let mut keys = message
        .account_keys
        .iter()
        .map(|value| bs58::encode(value).into_string())
        .collect::<Vec<_>>();
    keys.extend(
        meta.loaded_writable_addresses
            .iter()
            .chain(meta.loaded_readonly_addresses.iter())
            .map(|value| bs58::encode(value).into_string()),
    );
    let mut output = Vec::new();
    let mut visit = |coordinate: String, program_index: u32, accounts: &[u8], data: &[u8]| {
        if keys.get(program_index as usize).map(String::as_str) != Some(PUMPSWAP_PROGRAM)
            || data.len() < 24
        {
            return;
        }
        let (side, variant, amount_mode, minimum) = if data.starts_with(BUY_DISC) {
            ("BUY", "buy", "EXACT_BASE", PUMPSWAP_MIN_BUY_ACCOUNTS)
        } else if data.starts_with(BUY_EXACT_DISC) {
            ("BUY", "buy_exact_quote_in", "EXACT_QUOTE_IN", PUMPSWAP_MIN_BUY_ACCOUNTS)
        } else if data.starts_with(SELL_DISC) {
            ("SELL", "sell", "EXACT_BASE", PUMPSWAP_MIN_SELL_ACCOUNTS)
        } else {
            return;
        };
        let accounts = accounts
            .iter()
            .filter_map(|index| keys.get(*index as usize))
            .collect::<Vec<_>>();
        if accounts.len() >= minimum {
            output.push(Instruction {
                side,
                variant,
                coordinate,
                amount_mode,
                amount_raw: u64::from_le_bytes(data[8..16].try_into().expect("fixed PumpSwap amount")),
                limit_raw: u64::from_le_bytes(data[16..24].try_into().expect("fixed PumpSwap limit")),
                accounts: accounts.into_iter().cloned().collect(),
            });
        }
    };
    for (outer_index, instruction) in message.instructions.iter().enumerate() {
        visit(
            format!("outer:{outer_index:05}"),
            instruction.program_id_index,
            &instruction.accounts,
            &instruction.data,
        );
    }
    for group in &meta.inner_instructions {
        for (inner_index, instruction) in group.instructions.iter().enumerate() {
            let Some(stack_height) = instruction.stack_height else { continue };
            visit(
                format!("outer:{:05}/inner:{inner_index:05}/stack:{stack_height:03}", group.index),
                instruction.program_id_index,
                &instruction.accounts,
                &instruction.data,
            );
        }
    }
    output
}

fn decode_event(payload: &[u8]) -> Result<Option<Event>> {
    let mut reader = Reader {
        bytes: payload,
        offset: 0,
    };
    let discriminator = reader.take(8)?;
    let side = if discriminator == BUY_EVENT_DISC {
        "BUY"
    } else if discriminator == SELL_EVENT_DISC {
        "SELL"
    } else {
        return Ok(None);
    };
    let _timestamp = reader.i64()?;
    let base_amount = reader.u64()?;
    let instruction_limit_quote_raw = reader.u64()?;
    let user_base_raw = reader.u64()?;
    let user_quote_raw = reader.u64()?;
    let pool_base = reader.u64()?;
    let pool_quote = reader.u64()?;
    let curve_quote_raw = reader.u64()?;
    let lp_fee_bps = reader.u64()?;
    let lp_fee_raw = reader.u64()?;
    let protocol_fee_bps = reader.u64()?;
    let protocol_fee_raw = reader.u64()?;
    let pool_quote_delta = reader.u64()?;
    let user_quote_amount_raw = reader.u64()?;
    let pool = reader.pubkey()?;
    let user = reader.pubkey()?;
    for _ in 0..5 {
        let _ = reader.pubkey()?;
    }
    let coin_creator_bps = reader.u64()?;
    let coin_creator_fee_raw = reader.u64()?;
    if side == "BUY" {
        let _ = reader.bool()?;
        for _ in 0..3 {
            let _ = reader.u64()?;
        }
        let _ = reader.i64()?;
        let _ = reader.u64()?;
        reader.string()?;
    }
    let cashback_bps = reader.u64()?;
    let cashback_raw = reader.u64()?;
    let buyback_bps = reader.u64()?;
    let buyback_fee_raw = reader.u64()?;
    let virtual_quote_reserves_raw = reader.i128()?;
    let _ = reader.bool()?;
    let _ = reader.u64()?;
    match payload.len().saturating_sub(reader.offset) {
        0 => {}
        16 => {
            let _ = reader.u64()?;
            let _ = reader.u64()?;
        }
        24 => {
            let _ = reader.u64()?;
            let _ = reader.u64()?;
            let _ = reader.u64()?;
        }
        _ => bail!("unsupported PumpSwap event layout"),
    }
    if reader.offset != payload.len() {
        bail!("unconsumed PumpSwap event bytes")
    }
    Ok(Some(Event {
        side,
        base_amount,
        instruction_limit_quote_raw,
        user_base_raw,
        user_quote_raw,
        pool_base,
        pool_quote,
        curve_quote_raw,
        pool_quote_delta,
        lp_fee_bps,
        lp_fee_raw,
        protocol_fee_bps,
        protocol_fee_raw,
        user_quote_amount_raw,
        coin_creator_bps,
        coin_creator_fee_raw,
        cashback_bps,
        cashback_raw,
        buyback_bps,
        buyback_fee_raw,
        virtual_quote_reserves_raw,
        pool,
        user,
        mint: String::new(),
    }))
}

pub fn decode_events(
    meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta,
) -> Result<Vec<(usize, Event)>> {
    let mut output = Vec::new();
    for (log_index, log) in meta.log_messages.iter().enumerate() {
        let Some(encoded) = log.strip_prefix("Program data: ") else {
            continue;
        };
        let Ok(payload) = BASE64_STANDARD.decode(encoded.trim()) else {
            continue;
        };
        let side = if payload.starts_with(BUY_EVENT_DISC) {
            "BUY"
        } else if payload.starts_with(SELL_EVENT_DISC) {
            "SELL"
        } else {
            continue;
        };
        if payload.len() < 184 {
            bail!("recognized PumpSwap event too short")
        }
        let pool = bs58::encode(&payload[120..152]).into_string();
        let user = bs58::encode(&payload[152..184]).into_string();
        if let Some(event) = decode_event(&payload)? {
            if event.pool != pool || event.user != user || event.side != side {
                bail!("event header and decoded payload disagree")
            }
            output.push((log_index, event));
        }
    }
    Ok(output)
}

/// Join each observed PumpSwap event to exactly one compiled invocation from
/// the same full-meta transaction.  A missing or non-unique match is rejected
/// rather than choosing the first routed invocation.
pub fn decode_exact_transitions(
    info: &yellowstone_grpc_proto::geyser::SubscribeUpdateTransactionInfo,
) -> Result<Vec<ExactTransition>> {
    let transaction = info.transaction.as_ref().context("PUMPSWAP_TRANSACTION_MISSING")?;
    let message = transaction.message.as_ref().context("PUMPSWAP_MESSAGE_MISSING")?;
    let meta = info.meta.as_ref().context("PUMPSWAP_FULL_META_MISSING")?;
    if meta.err.is_some() {
        bail!("FAILED_PUMPSWAP_TRANSACTION")
    }
    let instructions = decode_instructions(message, meta);
    let events = decode_events(meta)?;
    let mut output = Vec::with_capacity(events.len());
    for (log_index, event) in events {
        let matches = instructions
            .iter()
            .filter(|instruction| {
                instruction.side == event.side
                    && instruction.accounts.get(PUMPSWAP_POOL_INDEX).map(String::as_str) == Some(event.pool.as_str())
                    && instruction.accounts.get(PUMPSWAP_USER_INDEX).map(String::as_str) == Some(event.user.as_str())
            })
            .collect::<Vec<_>>();
        if matches.len() != 1 {
            bail!("PUMPSWAP_EVENT_INSTRUCTION_MAPPING_AMBIGUOUS")
        }
        let instruction = matches[0];
        if instruction.amount_mode == "EXACT_BASE" && instruction.amount_raw != event.base_amount {
            bail!("PUMPSWAP_EVENT_INSTRUCTION_BASE_AMOUNT_MISMATCH")
        }
        if instruction.amount_mode == "EXACT_QUOTE_IN"
            && (instruction.amount_raw != event.curve_quote_raw
                || instruction.limit_raw != event.instruction_limit_quote_raw)
        {
            bail!("PUMPSWAP_EVENT_INSTRUCTION_EXACT_QUOTE_MISMATCH")
        }
        output.push(ExactTransition {
            log_index,
            instruction_coordinate: instruction.coordinate.clone(),
            amount_mode: instruction.amount_mode,
            instruction_amount_raw: instruction.amount_raw,
            instruction_limit_raw: instruction.limit_raw,
            event,
        });
    }
    Ok(output)
}

fn pool_account_discriminator() -> [u8; 8] {
    let hash = Sha256::digest(b"account:Pool");
    hash[..8].try_into().expect("eight-byte discriminator")
}

pub fn decode_pool_identity(
    pool: &str,
    owner: &str,
    bytes: &[u8],
    resolution_slot: u64,
) -> Result<PoolIdentity> {
    if owner != PUMPSWAP_PROGRAM {
        bail!("Pool account owner is not canonical PumpSwap program")
    }
    // Anchor Pool: discriminator(8), bump(u8), index(u16), creator(pubkey),
    // base_mint, quote_mint, lp_mint, pool_base_token_account,
    // pool_quote_token_account.  Trailing fields are intentionally ignored.
    if bytes.len() < 203 {
        bail!("unsupported PumpSwap Pool layout: {} bytes", bytes.len())
    }
    if bytes[..8] != pool_account_discriminator() {
        bail!("unsupported PumpSwap Pool discriminator")
    }
    let base_mint = bs58::encode(&bytes[43..75]).into_string();
    let quote_mint = bs58::encode(&bytes[75..107]).into_string();
    let pool_base_token_account = bs58::encode(&bytes[139..171]).into_string();
    let pool_quote_token_account = bs58::encode(&bytes[171..203]).into_string();
    // PumpSwap Pool accounts can orient the SOL/USDC quote asset as either
    // canonical `base_mint` or `quote_mint`.  Preserve that account truth and
    // derive the tradeable asset only when exactly one side is configured as a
    // supported quote.  No transaction-wide mint inference is involved.
    let base_is_quote = base_mint == WRAPPED_SOL_MINT || base_mint == USDC_MINT;
    let quote_is_quote = quote_mint == WRAPPED_SOL_MINT || quote_mint == USDC_MINT;
    let selection_mint = match (base_is_quote, quote_is_quote) {
        (true, false) => quote_mint.clone(),
        (false, true) => base_mint.clone(),
        (true, true) => bail!("PumpSwap Pool has two quote mints"),
        (false, false) => bail!("PumpSwap Pool has no supported quote mint"),
    };
    Ok(PoolIdentity {
        pool: pool.to_owned(),
        base_mint,
        quote_mint,
        selection_mint,
        pool_base_token_account,
        pool_quote_token_account,
        account_owner: owner.to_owned(),
        layout: "PUMPSWAP_POOL_ANCHOR_V1".to_owned(),
        resolution_slot,
    })
}

async fn resolve_pool_identity(
    http: &reqwest::Client,
    rpc_url: &str,
    pool: &str,
) -> Result<PoolIdentity> {
    let request = serde_json::json!({"jsonrpc":"2.0","id":"kefi-pool-identity-v1","method":"getAccountInfo","params":[pool,{"encoding":"base64","commitment":"confirmed"}]});
    let response: RpcResponse = http
        .post(rpc_url)
        .json(&request)
        .send()
        .await
        .context("Pool identity RPC request failed")?
        .error_for_status()
        .context("Pool identity RPC HTTP status")?
        .json()
        .await
        .context("Pool identity RPC response")?;
    if let Some(error) = response.error {
        bail!("Pool identity RPC error: {error}")
    }
    let result = response
        .result
        .context("Pool identity RPC missing result")?;
    let account = result.value.context("PumpSwap Pool account is absent")?;
    if account.data.1 != "base64" {
        bail!("PumpSwap Pool account was not base64 encoded")
    }
    let bytes = BASE64_STANDARD
        .decode(account.data.0)
        .context("PumpSwap Pool account invalid base64")?;
    decode_pool_identity(pool, &account.owner, &bytes, result.context.slot)
}

pub async fn resolve_pool_identity_batch(
    http: &reqwest::Client,
    rpc_url: &str,
    pools: &[String],
) -> Result<Vec<(String, Result<PoolIdentity, String>)>> {
    if pools.is_empty() {
        return Ok(Vec::new());
    }
    if pools.len() > 100 {
        bail!("Pool identity batch exceeds RPC limit")
    }
    let request = serde_json::json!({"jsonrpc":"2.0","id":"kefi-pool-identity-v1","method":"getMultipleAccounts","params":[pools,{"encoding":"base64","commitment":"confirmed"}]});
    let response: RpcMultiResponse = http
        .post(rpc_url)
        .json(&request)
        .send()
        .await
        .context("Pool identity batch RPC request failed")?
        .error_for_status()
        .context("Pool identity batch RPC HTTP status")?
        .json()
        .await
        .context("Pool identity batch RPC response")?;
    if let Some(error) = response.error {
        bail!("Pool identity batch RPC error: {error}")
    }
    let result = response
        .result
        .context("Pool identity batch RPC missing result")?;
    if result.value.len() != pools.len() {
        bail!("Pool identity batch response cardinality mismatch")
    }
    Ok(pools
        .iter()
        .cloned()
        .zip(result.value.into_iter())
        .map(|(pool, account)| {
            let resolved = (|| -> Result<PoolIdentity> {
                let account = account.context("PumpSwap Pool account is absent")?;
                if account.data.1 != "base64" {
                    bail!("PumpSwap Pool account was not base64 encoded")
                }
                let bytes = BASE64_STANDARD
                    .decode(account.data.0)
                    .context("PumpSwap Pool account invalid base64")?;
                decode_pool_identity(&pool, &account.owner, &bytes, result.context.slot)
            })()
            .map_err(|error| format!("{error:#}"));
            (pool, resolved)
        })
        .collect())
}

fn explicit_layout_mint(side: &str, pool: &str, instructions: &[Instruction]) -> Result<String> {
    let invocations = instructions
        .iter()
        .filter(|instruction| {
            instruction.side == side
                && instruction
                    .accounts
                    .get(PUMPSWAP_POOL_INDEX)
                    .map(String::as_str)
                    == Some(pool)
        })
        .collect::<Vec<_>>();
    let base_index = match side {
        "BUY" => PUMPSWAP_BUY_BASE_MINT_INDEX,
        "SELL" => PUMPSWAP_SELL_BASE_MINT_INDEX,
        _ => bail!("unknown PumpSwap side"),
    };
    let mints = invocations
        .iter()
        .filter_map(|instruction| instruction.accounts.get(base_index))
        .filter(|mint| mint.as_str() != WRAPPED_SOL_MINT)
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    if mints.len() != 1 {
        bail!("unsupported PumpSwap event-to-invocation mapping: side={side}, pool_matches={}, base_mints={}", invocations.len(), mints.len())
    }
    Ok(mints.into_iter().next().expect("one explicit layout mint"))
}

/// Direct instruction layouts are a *cross-check* only.  Unknown router
/// variants intentionally return None rather than participating in identity
/// selection; the authoritative Pool account remains the sole resolver.
fn instruction_mint_cross_check(
    side: &str,
    pool: &str,
    instructions: &[Instruction],
) -> Option<String> {
    explicit_layout_mint(side, pool, instructions).ok()
}

pub fn cache_identity(
    cache: &mut BTreeMap<String, PoolIdentity>,
    identity: PoolIdentity,
) -> Result<PoolIdentity> {
    if let Some(previous) = cache.get(&identity.pool) {
        if previous != &identity {
            bail!("authoritative Pool identity conflict for {}", identity.pool)
        }
        return Ok(previous.clone());
    }
    cache.insert(identity.pool.clone(), identity.clone());
    Ok(identity)
}

fn now_ns() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
}
fn sha256(bytes: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(bytes);
    format!("{:x}", digest.finalize())
}
fn sha256_file(path: &Path) -> Result<String> {
    Ok(sha256(&fs::read(path)?))
}
fn canonical_json<T: Serialize>(value: &T) -> Result<String> {
    Ok(serde_json::to_string(value)?)
}

fn observed_wallet_record_hash(record: &ObservedWalletLedgerRecord) -> String {
    sha256(
        format!(
            "{}|{}|{}|{}|{}|{}|{}|{}|{}",
            record.sequence,
            record.ledger_boundary_unix_ns,
            record.mint,
            record.first_action_identity,
            record.signature,
            record.slot,
            record.observed_at_unix_ns,
            record.source_provenance_sha256,
            record.previous_record_sha256
        )
        .as_bytes(),
    )
}

impl ObservedWalletLedger {
    fn open(path: PathBuf, boundary: u128) -> Result<Self> {
        let parent = path.parent().context("wallet ledger path has no parent")?;
        fs::create_dir_all(parent)?;
        let lock_path = path.with_extension("lock");
        // A stale lock fails closed rather than allowing two appenders to race.
        let mut lock = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&lock_path)
            .context("observed-wallet ledger lock already exists")?;
        writeln!(lock, "pid={} boundary={}", std::process::id(), boundary)?;
        lock.sync_all()?;
        let mut sequence = 0_u64;
        let mut tail = "GENESIS".to_owned();
        let mut seen_mints = HashSet::new();
        if path.exists() {
            for (line_number, line) in fs::read_to_string(&path)?.lines().enumerate() {
                if line.is_empty() {
                    continue;
                }
                let record: ObservedWalletLedgerRecord =
                    serde_json::from_str(line).with_context(|| {
                        format!("malformed observed-wallet ledger line {}", line_number + 1)
                    })?;
                if record.schema != "KEFI_OBSERVED_WALLET_MINT_LEDGER_V1"
                    || record.ledger_boundary_unix_ns != boundary
                    || record.sequence != sequence + 1
                    || record.previous_record_sha256 != tail
                    || record.record_sha256 != observed_wallet_record_hash(&record)
                    || !seen_mints.insert(record.mint.clone())
                {
                    let _ = fs::remove_file(&lock_path);
                    bail!("observed-wallet ledger integrity conflict")
                }
                sequence = record.sequence;
                tail = record.record_sha256;
            }
        }
        let file = OpenOptions::new().append(true).create(true).open(&path)?;
        Ok(Self {
            lock_path,
            file,
            boundary,
            sequence,
            tail,
            seen_mints,
        })
    }

    fn observe(
        &mut self,
        mint: &str,
        signature: &str,
        slot: u64,
        event_index: usize,
        side: &str,
        received_ns: u128,
        source_sha256: &str,
    ) -> Result<bool> {
        if received_ns < self.boundary {
            return Ok(false);
        }
        if self.seen_mints.contains(mint) {
            return Ok(false);
        }
        let mut record = ObservedWalletLedgerRecord {
            schema: "KEFI_OBSERVED_WALLET_MINT_LEDGER_V1".to_owned(),
            sequence: self.sequence + 1,
            ledger_boundary_unix_ns: self.boundary,
            mint: mint.to_owned(),
            first_action_identity: sha256(
                format!(
                    "kefi-observed-wallet-v1|{}|{}|{}|{}|{}",
                    signature, slot, event_index, mint, side
                )
                .as_bytes(),
            ),
            signature: signature.to_owned(),
            slot,
            observed_at_unix_ns: received_ns,
            source_provenance_sha256: source_sha256.to_owned(),
            previous_record_sha256: self.tail.clone(),
            source_kind: "LIVE_YELLOWSTONE_DIRECT_WALLET_ACTION".to_owned(),
            record_sha256: String::new(),
        };
        record.record_sha256 = observed_wallet_record_hash(&record);
        self.file.write_all(canonical_json(&record)?.as_bytes())?;
        self.file.write_all(b"\n")?;
        self.file.sync_all()?;
        self.sequence = record.sequence;
        self.tail = record.record_sha256;
        self.seen_mints.insert(mint.to_owned());
        Ok(true)
    }
}

impl Drop for ObservedWalletLedger {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.lock_path);
    }
}

#[derive(Serialize)]
struct Row<'a> {
    schema: &'a str,
    source_instance_id: &'a str,
    continuity_epoch: u64,
    durable_output_sequence: u64,
    received_at_unix_ns: u128,
    source_frame_sha256: &'a str,
    event_index: usize,
    intrinsic_event_id: String,
    slot: u64,
    signature: &'a str,
    pool: &'a str,
    mint: &'a str,
    side: &'a str,
    base_amount: u64,
    pool_base_raw: u64,
    pool_quote_raw: u64,
    pool_quote_delta: u64,
    fee_tuple: (u64, u64, u64, u64, u64),
    layout: &'a str,
    event_action_type: &'a str,
    first_public_observation_unix_ns: u128,
    first_public_observation_id: &'a str,
}

#[derive(Default)]
struct Stats {
    source_frames: u64,
    rows: u64,
    unique_pools: BTreeMap<String, (u128, String)>,
    rejected: u64,
    duplicates: u64,
    conflicts: u64,
    pool_resolutions: u64,
    pool_cache_hits: u64,
    unsupported_quote_events: u64,
    legacy_layout_disagreements: u64,
    first_ns: Option<u128>,
    last_ns: Option<u128>,
    latest_slot: Option<u64>,
}

pub fn pool_is_explicitly_unusable(error: &str) -> bool {
    error.starts_with("PumpSwap Pool has no supported quote mint")
        || error.starts_with("PumpSwap Pool has two quote mints")
        || error.starts_with("PumpSwap Pool account is absent")
}

#[derive(Serialize)]
struct Manifest<'a> {
    schema: &'a str,
    teacher_independent: bool,
    selected_centred: bool,
    all_supported_pools_covered: bool,
    uses_future_outcomes: bool,
    source_instance: &'a str,
    continuity_epoch: u64,
    source_interval_received_at_unix_ns: (Option<u128>, Option<u128>),
    row_count: u64,
    unique_pools: usize,
    source_frames: u64,
    rejected_rows: u64,
    duplicate_rows_discarded: u64,
    conflicting_identities: u64,
    events_path: String,
    events_sha256: String,
    compressed_bytes: u64,
    duration_seconds: u64,
    estimated_bytes_per_day: u128,
    writer_tool_sha256: String,
    collector_or_promotion_input_opened: bool,
    source_filter: &'a str,
    source_replay_from_slot: Option<u64>,
    validation_status: &'a str,
}

fn write_json_atomic(path: &Path, value: &impl Serialize) -> Result<()> {
    let temporary = path.with_extension("json.tmp");
    let mut output = File::create(&temporary)?;
    output.write_all(canonical_json(value)?.as_bytes())?;
    output.write_all(b"\n")?;
    output.sync_all()?;
    fs::rename(temporary, path)?;
    Ok(())
}
