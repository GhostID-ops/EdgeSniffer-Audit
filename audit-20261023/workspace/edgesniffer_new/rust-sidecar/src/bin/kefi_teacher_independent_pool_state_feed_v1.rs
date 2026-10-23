//! A bounded, teacher-independent PumpSwap pool-state feed probe.
//!
//! This is intentionally an isolated gRPC subscriber.  It neither receives a
//! tracked-wallet value nor opens promotion, PRE_H3, outcome, or PnL artifacts.
//! Its only output is a compact canonical public-event stream and a manifest.

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use futures::{stream, StreamExt};
use prost::Message;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque}, env, fs::{self, File, OpenOptions}, io::{BufWriter, Write}, path::{Path, PathBuf}, process::{Command, Stdio}, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};
use tonic::{metadata::AsciiMetadataValue, transport::{Channel, ClientTlsConfig}, Request, Status};
use yellowstone_grpc_proto::geyser::{geyser_client::GeyserClient, subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest, SubscribeRequestFilterTransactions};

const PUMPSWAP_PROGRAM: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const WRAPPED_SOL_MINT: &str = "So11111111111111111111111111111111111111112";
const USDC_MINT: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const BUY_DISC: &[u8] = &[102, 6, 61, 18, 1, 218, 235, 234];
const BUY_EXACT_DISC: &[u8] = &[198, 46, 21, 82, 180, 217, 232, 112];
const SELL_DISC: &[u8] = &[51, 230, 133, 164, 1, 127, 131, 173];
const BUY_EVENT_DISC: &[u8] = &[103, 244, 82, 31, 44, 245, 119, 119];
const SELL_EVENT_DISC: &[u8] = &[62, 47, 55, 10, 165, 3, 220, 42];
const SCHEMA: &str = "KEFI_TEACHER_INDEPENDENT_POOL_STATE_V1";

#[derive(Clone)]
struct Instruction { side: &'static str, variant: &'static str, accounts: Vec<String> }

const PUMPSWAP_POOL_INDEX: usize = 0;
const PUMPSWAP_USER_INDEX: usize = 1;
const PUMPSWAP_BUY_BASE_MINT_INDEX: usize = 3;
const PUMPSWAP_BUY_QUOTE_MINT_INDEX: usize = 4;
const PUMPSWAP_SELL_QUOTE_MINT_INDEX: usize = 3;
const PUMPSWAP_SELL_BASE_MINT_INDEX: usize = 4;
const PUMPSWAP_MIN_BUY_ACCOUNTS: usize = 23;
const PUMPSWAP_MIN_SELL_ACCOUNTS: usize = 21;
const CHOICESTATE_WARMUP_SECONDS: u64 = 60;

struct Event {
    side: &'static str, base_amount: u64, pool_base: u64, pool_quote: u64, pool_quote_delta: u64,
    lp_fee_bps: u64, protocol_fee_bps: u64, coin_creator_bps: u64, cashback_bps: u64, buyback_bps: u64,
    pool: String, user: String, mint: String,
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
    let Some(wallet) = wallet else { return Ok(None) };
    let teacher_events = decoded.iter().filter_map(|(index, event)| {
        (event.user == wallet).then(|| {
            identities.get(&event.pool)
                .map(|identity| (*index, event.side, identity.selection_mint.clone()))
                .context("teacher event lacks authoritative Pool identity")
        })
    }).collect::<Result<Vec<_>>>()?;
    let buy_mints = teacher_events.iter().filter(|(_, side, _)| *side == "BUY")
        .map(|(_, _, mint)| mint.clone()).collect::<BTreeSet<_>>();
    if buy_mints.is_empty() { return Ok(None) }
    let all_action_mints = teacher_events.iter().map(|(_, _, mint)| mint.clone()).collect::<BTreeSet<_>>();
    if all_action_mints.len() != 1 {
        bail!("AMBIGUOUS_MULTI_MINT_TEACHER_SELECTION: one transaction contains a teacher BUY across {} authoritative mints", all_action_mints.len());
    }
    Ok(teacher_events.iter().filter(|(_, side, _)| *side == "BUY").map(|(index, _, _)| *index).min())
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
struct PoolIdentity {
    pool: String,
    base_mint: String,
    quote_mint: String,
    selection_mint: String,
    pool_base_token_account: String,
    pool_quote_token_account: String,
    account_owner: String,
    layout: String,
    resolution_slot: u64,
}

#[derive(Deserialize)]
struct RpcResponse { result: Option<RpcResult>, error: Option<serde_json::Value> }
#[derive(Deserialize)]
struct RpcResult { context: RpcContext, value: Option<RpcAccount> }
#[derive(Deserialize)]
struct RpcContext { slot: u64 }
#[derive(Deserialize)]
struct RpcAccount { owner: String, data: (String, String) }
#[derive(Deserialize)]
struct RpcMultiResponse { result: Option<RpcMultiResult>, error: Option<serde_json::Value> }
#[derive(Deserialize)]
struct RpcMultiResult { context: RpcContext, value: Vec<Option<RpcAccount>> }

#[derive(Clone)]
struct Flow { at_ns: u128, side: &'static str, quote: u64, user: String }
#[derive(Clone)]
struct PoolState {
    first_ns: u128, first_id: String, last_ns: u128, last_sequence: u64,
    pool_base: u64, pool_quote: u64, fees: (u64, u64, u64, u64, u64),
    prior_pre_pool_quote: Option<u64>, last_buy_ns: Option<u128>, flows: VecDeque<Flow>,
}
#[derive(Serialize)]
struct ChoiceVector<'a> {
    pool: &'a str, mint: &'a str, quote_reserve_raw: u64, base_reserve_raw: u64,
    fee_tuple: (u64,u64,u64,u64,u64), observed_pool_age_seconds: u64,
    seconds_since_prior_observed_buy: Option<u64>, prior_pre_pool_quote_raw: Option<u64>,
    buy_count_5s: u64, buy_count_20s: u64, buy_count_60s: u64,
    buy_quote_flow_5s: u64, buy_quote_flow_20s: u64, buy_quote_flow_60s: u64,
    no_prior_buy: bool, sell_count_60s: u64, sell_quote_flow_60s: u64,
    first_public_observation_unix_ns: u128, last_event_unix_ns: u128, last_durable_output_sequence: u64,
}
#[derive(Serialize)]
struct ChoiceSnapshot<'a> {
    schema: &'a str, source_instance_id: &'a str, continuity_epoch: u64,
    teacher_signature: &'a str, teacher_slot: u64, teacher_event_index: usize,
    teacher_received_at_unix_ns: u128, teacher_durable_output_sequence: u64,
    selection_kind: &'a str, chosen_pool: &'a str, chosen_mint: &'a str,
    chosen_admitted: bool, wallet_ledger_boundary_unix_ns: u128,
    wallet_ledger_sequence_before: u64, wallet_ledger_tail_before: &'a str,
    wallet_ledger_record_sha256: &'a str, candidates: Vec<ChoiceVector<'a>>,
}

/// Compact, append-only wallet history shared by every future observer epoch.
/// It is intentionally separate from pool state: it only answers whether a
/// mint has already been observed in a direct kEfi wallet action since the
/// fixed analysis-history boundary.
#[derive(Clone, Serialize, Deserialize)]
struct ObservedWalletLedgerRecord {
    schema: String, sequence: u64, ledger_boundary_unix_ns: u128, mint: String,
    first_action_identity: String, signature: String, slot: u64, observed_at_unix_ns: u128,
    source_provenance_sha256: String, previous_record_sha256: String, source_kind: String,
    record_sha256: String,
}

struct ObservedWalletLedger {
    lock_path: PathBuf, file: File, boundary: u128, sequence: u64,
    tail: String, seen_mints: HashSet<String>,
}

fn vector<'a>(pool: &'a str, mint: &'a str, state: &'a PoolState, at_ns: u128) -> ChoiceVector<'a> {
    let mut counts = [0_u64; 3]; let mut flow = [0_u64; 3]; let mut sells = 0_u64; let mut sell_flow = 0_u64;
    for event in &state.flows {
        let age = at_ns.saturating_sub(event.at_ns);
        for (index, window) in [5_u128,20,60].iter().enumerate() { if age <= *window * 1_000_000_000 && event.side == "BUY" { counts[index]+=1; flow[index]=flow[index].saturating_add(event.quote); } }
        if age <= 60_000_000_000 && event.side == "SELL" { sells += 1; sell_flow= sell_flow.saturating_add(event.quote); }
    }
    ChoiceVector { pool, mint, quote_reserve_raw: state.pool_quote, base_reserve_raw: state.pool_base, fee_tuple: state.fees,
        observed_pool_age_seconds: at_ns.saturating_sub(state.first_ns) as u64 / 1_000_000_000,
        seconds_since_prior_observed_buy: state.last_buy_ns.map(|n| at_ns.saturating_sub(n) as u64 / 1_000_000_000),
        prior_pre_pool_quote_raw: state.prior_pre_pool_quote, buy_count_5s: counts[0], buy_count_20s: counts[1], buy_count_60s: counts[2],
        buy_quote_flow_5s: flow[0], buy_quote_flow_20s: flow[1], buy_quote_flow_60s: flow[2], no_prior_buy: state.last_buy_ns.is_none(),
        sell_count_60s: sells, sell_quote_flow_60s: sell_flow, first_public_observation_unix_ns: state.first_ns, last_event_unix_ns: state.last_ns, last_durable_output_sequence: state.last_sequence }
}

fn apply_event(state: &mut PoolState, event: &Event, at_ns: u128, sequence: u64) {
    state.prior_pre_pool_quote = Some(state.pool_quote); state.pool_base = event.pool_base; state.pool_quote = event.pool_quote;
    state.fees = (event.lp_fee_bps,event.protocol_fee_bps,event.coin_creator_bps,event.cashback_bps,event.buyback_bps);
    if event.side == "BUY" { state.last_buy_ns = Some(at_ns); }
    state.flows.push_back(Flow { at_ns, side:event.side, quote:event.pool_quote_delta, user:event.user.clone() });
    while state.flows.front().map(|f| at_ns.saturating_sub(f.at_ns) > 60_000_000_000).unwrap_or(false) { state.flows.pop_front(); }
    state.last_ns=at_ns; state.last_sequence=sequence;
}

struct Reader<'a> { bytes: &'a [u8], offset: usize }
impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8]> { let end = self.offset.checked_add(n).context("event offset overflow")?; if end > self.bytes.len() { bail!("truncated PumpSwap event") } let value = &self.bytes[self.offset..end]; self.offset = end; Ok(value) }
    fn u64(&mut self) -> Result<u64> { Ok(u64::from_le_bytes(self.take(8)?.try_into()?)) }
    fn i64(&mut self) -> Result<i64> { Ok(i64::from_le_bytes(self.take(8)?.try_into()?)) }
    fn i128(&mut self) -> Result<i128> { Ok(i128::from_le_bytes(self.take(16)?.try_into()?)) }
    fn bool(&mut self) -> Result<bool> { Ok(self.take(1)?[0] != 0) }
    fn string(&mut self) -> Result<()> { let n = u32::from_le_bytes(self.take(4)?.try_into()?) as usize; self.take(n)?; Ok(()) }
    fn pubkey(&mut self) -> Result<String> { Ok(bs58::encode(self.take(32)?).into_string()) }
}

fn request(from_slot: Option<u64>, tracked_wallet: Option<&str>) -> SubscribeRequest {
    let mut transactions = HashMap::new();
    transactions.insert("all_pumpswap".to_owned(), SubscribeRequestFilterTransactions {
        vote: Some(false), failed: Some(false), signature: None, account_include: vec![PUMPSWAP_PROGRAM.to_owned()], account_exclude: vec![], account_required: vec![], token_accounts: None,
    });
    // The second OR-filter is ledger-only. It collects direct wallet actions
    // outside PumpSwap without putting them into public pool-state features.
    if let Some(wallet) = tracked_wallet {
        transactions.insert("tracked_wallet_history".to_owned(), SubscribeRequestFilterTransactions {
            vote: Some(false), failed: Some(false), signature: None, account_include: vec![], account_exclude: vec![], account_required: vec![wallet.to_owned()], token_accounts: None,
        });
    }
    SubscribeRequest { accounts: Default::default(), slots: Default::default(), transactions, transactions_status: Default::default(), blocks: Default::default(), blocks_meta: Default::default(), entry: Default::default(), accounts_data_slice: vec![], commitment: Some(CommitmentLevel::Confirmed as i32), ping: None, from_slot }
}

fn direct_wallet_token_mints(meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta, wallet: &str) -> BTreeSet<String> {
    let mut before: BTreeMap<String, String> = BTreeMap::new();
    let mut after: BTreeMap<String, String> = BTreeMap::new();
    for balance in &meta.pre_token_balances {
        if balance.owner == wallet { before.insert(balance.mint.clone(), balance.ui_token_amount.as_ref().map(|amount| amount.amount.clone()).unwrap_or_default()); }
    }
    for balance in &meta.post_token_balances {
        if balance.owner == wallet { after.insert(balance.mint.clone(), balance.ui_token_amount.as_ref().map(|amount| amount.amount.clone()).unwrap_or_default()); }
    }
    before.keys().chain(after.keys()).filter(|mint| before.get(*mint) != after.get(*mint)).cloned().collect()
}

fn decode_instructions(message: &yellowstone_grpc_proto::solana::storage::confirmed_block::Message, meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta) -> Vec<Instruction> {
    let mut keys = message.account_keys.iter().map(|value| bs58::encode(value).into_string()).collect::<Vec<_>>();
    keys.extend(meta.loaded_writable_addresses.iter().chain(meta.loaded_readonly_addresses.iter()).map(|value| bs58::encode(value).into_string()));
    let mut output = Vec::new();
    let mut visit = |program_index: u32, accounts: &[u8], data: &[u8]| {
        if keys.get(program_index as usize).map(String::as_str) != Some(PUMPSWAP_PROGRAM) || data.len() < 8 { return; }
        let (side, variant, minimum) = if data.starts_with(BUY_DISC) { ("BUY", "buy", PUMPSWAP_MIN_BUY_ACCOUNTS) }
            else if data.starts_with(BUY_EXACT_DISC) { ("BUY", "buy_exact_quote_in", PUMPSWAP_MIN_BUY_ACCOUNTS) }
            else if data.starts_with(SELL_DISC) { ("SELL", "sell", PUMPSWAP_MIN_SELL_ACCOUNTS) } else { return; };
        let accounts = accounts.iter().filter_map(|index| keys.get(*index as usize)).collect::<Vec<_>>();
        if accounts.len() >= minimum { output.push(Instruction { side, variant, accounts: accounts.into_iter().cloned().collect() }); }
    };
    for instruction in &message.instructions { visit(instruction.program_id_index, &instruction.accounts, &instruction.data); }
    for group in &meta.inner_instructions { for instruction in &group.instructions { visit(instruction.program_id_index, &instruction.accounts, &instruction.data); } }
    output
}

fn decode_event(payload: &[u8]) -> Result<Option<Event>> {
    let mut reader = Reader { bytes: payload, offset: 0 };
    let discriminator = reader.take(8)?;
    let side = if discriminator == BUY_EVENT_DISC { "BUY" } else if discriminator == SELL_EVENT_DISC { "SELL" } else { return Ok(None); };
    let _timestamp = reader.i64()?; let base_amount = reader.u64()?; let _limit_quote = reader.u64()?; let _user_base = reader.u64()?; let _user_quote = reader.u64()?;
    let pool_base = reader.u64()?; let pool_quote = reader.u64()?; let _curve_quote = reader.u64()?; let lp_fee_bps = reader.u64()?; let _lp_fee = reader.u64()?; let protocol_fee_bps = reader.u64()?; let _protocol_fee = reader.u64()?; let pool_quote_delta = reader.u64()?; let _user_quote_amount = reader.u64()?;
    let pool = reader.pubkey()?; let user = reader.pubkey()?; for _ in 0..5 { let _ = reader.pubkey()?; }
    let coin_creator_bps = reader.u64()?; let _coin_creator_fee = reader.u64()?;
    if side == "BUY" { let _ = reader.bool()?; for _ in 0..3 { let _ = reader.u64()?; } let _ = reader.i64()?; let _ = reader.u64()?; reader.string()?; }
    let cashback_bps = reader.u64()?; let _cashback = reader.u64()?; let buyback_bps = reader.u64()?; let _buyback_fee = reader.u64()?; let _virtual_quote = reader.i128()?; let _ = reader.bool()?; let _ = reader.u64()?;
    match payload.len().saturating_sub(reader.offset) { 0 => {}, 16 => { let _ = reader.u64()?; let _ = reader.u64()?; }, 24 => { let _ = reader.u64()?; let _ = reader.u64()?; let _ = reader.u64()?; }, _ => bail!("unsupported PumpSwap event layout") }
    if reader.offset != payload.len() { bail!("unconsumed PumpSwap event bytes") }
    Ok(Some(Event { side, base_amount, pool_base, pool_quote, pool_quote_delta, lp_fee_bps, protocol_fee_bps, coin_creator_bps, cashback_bps, buyback_bps, pool, user, mint: String::new() }))
}

fn decode_events(meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta) -> Result<Vec<(usize, Event)>> {
    let mut output = Vec::new();
    for (log_index, log) in meta.log_messages.iter().enumerate() {
        let Some(encoded) = log.strip_prefix("Program data: ") else { continue; };
        let Ok(payload) = BASE64_STANDARD.decode(encoded.trim()) else { continue; };
        let side = if payload.starts_with(BUY_EVENT_DISC) { "BUY" } else if payload.starts_with(SELL_EVENT_DISC) { "SELL" } else { continue; };
        if payload.len() < 184 { bail!("recognized PumpSwap event too short") }
        let pool = bs58::encode(&payload[120..152]).into_string(); let user = bs58::encode(&payload[152..184]).into_string();
        if let Some(event) = decode_event(&payload)? {
            if event.pool != pool || event.user != user || event.side != side { bail!("event header and decoded payload disagree") }
            output.push((log_index, event));
        }
    }
    Ok(output)
}

fn pool_account_discriminator() -> [u8; 8] {
    let hash = Sha256::digest(b"account:Pool");
    hash[..8].try_into().expect("eight-byte discriminator")
}

fn decode_pool_identity(pool: &str, owner: &str, bytes: &[u8], resolution_slot: u64) -> Result<PoolIdentity> {
    if owner != PUMPSWAP_PROGRAM { bail!("Pool account owner is not canonical PumpSwap program") }
    // Anchor Pool: discriminator(8), bump(u8), index(u16), creator(pubkey),
    // base_mint, quote_mint, lp_mint, pool_base_token_account,
    // pool_quote_token_account.  Trailing fields are intentionally ignored.
    if bytes.len() < 203 { bail!("unsupported PumpSwap Pool layout: {} bytes", bytes.len()) }
    if bytes[..8] != pool_account_discriminator() { bail!("unsupported PumpSwap Pool discriminator") }
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
    Ok(PoolIdentity { pool: pool.to_owned(), base_mint, quote_mint, selection_mint, pool_base_token_account, pool_quote_token_account, account_owner: owner.to_owned(), layout: "PUMPSWAP_POOL_ANCHOR_V1".to_owned(), resolution_slot })
}

async fn resolve_pool_identity(http: &reqwest::Client, rpc_url: &str, pool: &str) -> Result<PoolIdentity> {
    let request = serde_json::json!({"jsonrpc":"2.0","id":"kefi-pool-identity-v1","method":"getAccountInfo","params":[pool,{"encoding":"base64","commitment":"confirmed"}]});
    let response: RpcResponse = http.post(rpc_url).json(&request).send().await.context("Pool identity RPC request failed")?.error_for_status().context("Pool identity RPC HTTP status")?.json().await.context("Pool identity RPC response")?;
    if let Some(error) = response.error { bail!("Pool identity RPC error: {error}") }
    let result = response.result.context("Pool identity RPC missing result")?;
    let account = result.value.context("PumpSwap Pool account is absent")?;
    if account.data.1 != "base64" { bail!("PumpSwap Pool account was not base64 encoded") }
    let bytes = BASE64_STANDARD.decode(account.data.0).context("PumpSwap Pool account invalid base64")?;
    decode_pool_identity(pool, &account.owner, &bytes, result.context.slot)
}

async fn resolve_pool_identity_batch(http: &reqwest::Client, rpc_url: &str, pools: &[String]) -> Result<Vec<(String, Result<PoolIdentity, String>)>> {
    if pools.is_empty() { return Ok(Vec::new()) }
    if pools.len() > 100 { bail!("Pool identity batch exceeds RPC limit") }
    let request = serde_json::json!({"jsonrpc":"2.0","id":"kefi-pool-identity-v1","method":"getMultipleAccounts","params":[pools,{"encoding":"base64","commitment":"confirmed"}]});
    let response: RpcMultiResponse = http.post(rpc_url).json(&request).send().await.context("Pool identity batch RPC request failed")?.error_for_status().context("Pool identity batch RPC HTTP status")?.json().await.context("Pool identity batch RPC response")?;
    if let Some(error) = response.error { bail!("Pool identity batch RPC error: {error}") }
    let result = response.result.context("Pool identity batch RPC missing result")?;
    if result.value.len() != pools.len() { bail!("Pool identity batch response cardinality mismatch") }
    Ok(pools.iter().cloned().zip(result.value.into_iter()).map(|(pool, account)| {
        let resolved = (|| -> Result<PoolIdentity> {
            let account = account.context("PumpSwap Pool account is absent")?;
            if account.data.1 != "base64" { bail!("PumpSwap Pool account was not base64 encoded") }
            let bytes = BASE64_STANDARD.decode(account.data.0).context("PumpSwap Pool account invalid base64")?;
            decode_pool_identity(&pool, &account.owner, &bytes, result.context.slot)
        })().map_err(|error| format!("{error:#}"));
        (pool, resolved)
    }).collect())
}

fn explicit_layout_mint(side: &str, pool: &str, instructions: &[Instruction]) -> Result<String> {
    let invocations = instructions.iter().filter(|instruction| instruction.side == side && instruction.accounts.get(PUMPSWAP_POOL_INDEX).map(String::as_str) == Some(pool)).collect::<Vec<_>>();
    let base_index = match side { "BUY" => PUMPSWAP_BUY_BASE_MINT_INDEX, "SELL" => PUMPSWAP_SELL_BASE_MINT_INDEX, _ => bail!("unknown PumpSwap side") };
    let mints = invocations.iter().filter_map(|instruction| instruction.accounts.get(base_index)).filter(|mint| mint.as_str() != WRAPPED_SOL_MINT).cloned().collect::<std::collections::BTreeSet<_>>();
    if mints.len() != 1 { bail!("unsupported PumpSwap event-to-invocation mapping: side={side}, pool_matches={}, base_mints={}", invocations.len(), mints.len()) }
    Ok(mints.into_iter().next().expect("one explicit layout mint"))
}

/// Direct instruction layouts are a *cross-check* only.  Unknown router
/// variants intentionally return None rather than participating in identity
/// selection; the authoritative Pool account remains the sole resolver.
fn instruction_mint_cross_check(side: &str, pool: &str, instructions: &[Instruction]) -> Option<String> {
    explicit_layout_mint(side, pool, instructions).ok()
}

fn cache_identity(cache: &mut BTreeMap<String, PoolIdentity>, identity: PoolIdentity) -> Result<PoolIdentity> {
    if let Some(previous) = cache.get(&identity.pool) {
        if previous != &identity { bail!("authoritative Pool identity conflict for {}", identity.pool) }
        return Ok(previous.clone());
    }
    cache.insert(identity.pool.clone(), identity.clone());
    Ok(identity)
}

fn now_ns() -> u128 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos() }
fn sha256(bytes: &[u8]) -> String { let mut digest = Sha256::new(); digest.update(bytes); format!("{:x}", digest.finalize()) }
fn sha256_file(path: &Path) -> Result<String> { Ok(sha256(&fs::read(path)?)) }
fn canonical_json<T: Serialize>(value: &T) -> Result<String> { Ok(serde_json::to_string(value)?) }

fn observed_wallet_record_hash(record: &ObservedWalletLedgerRecord) -> String {
    sha256(format!("{}|{}|{}|{}|{}|{}|{}|{}|{}",
        record.sequence, record.ledger_boundary_unix_ns, record.mint, record.first_action_identity,
        record.signature, record.slot, record.observed_at_unix_ns, record.source_provenance_sha256,
        record.previous_record_sha256).as_bytes())
}

impl ObservedWalletLedger {
    fn open(path: PathBuf, boundary: u128) -> Result<Self> {
        let parent = path.parent().context("wallet ledger path has no parent")?;
        fs::create_dir_all(parent)?;
        let lock_path = path.with_extension("lock");
        // A stale lock fails closed rather than allowing two appenders to race.
        let mut lock = OpenOptions::new().write(true).create_new(true).open(&lock_path)
            .context("observed-wallet ledger lock already exists")?;
        writeln!(lock, "pid={} boundary={}", std::process::id(), boundary)?; lock.sync_all()?;
        let mut sequence = 0_u64; let mut tail = "GENESIS".to_owned(); let mut seen_mints = HashSet::new();
        if path.exists() {
            for (line_number, line) in fs::read_to_string(&path)?.lines().enumerate() {
                if line.is_empty() { continue; }
                let record: ObservedWalletLedgerRecord = serde_json::from_str(line)
                    .with_context(|| format!("malformed observed-wallet ledger line {}", line_number + 1))?;
                if record.schema != "KEFI_OBSERVED_WALLET_MINT_LEDGER_V1" || record.ledger_boundary_unix_ns != boundary || record.sequence != sequence + 1 || record.previous_record_sha256 != tail || record.record_sha256 != observed_wallet_record_hash(&record) || !seen_mints.insert(record.mint.clone()) {
                    let _ = fs::remove_file(&lock_path);
                    bail!("observed-wallet ledger integrity conflict")
                }
                sequence = record.sequence; tail = record.record_sha256;
            }
        }
        let file = OpenOptions::new().append(true).create(true).open(&path)?;
        Ok(Self { lock_path, file, boundary, sequence, tail, seen_mints })
    }

    fn observe(&mut self, mint: &str, signature: &str, slot: u64, event_index: usize, side: &str, received_ns: u128, source_sha256: &str) -> Result<bool> {
        if received_ns < self.boundary { return Ok(false) }
        if self.seen_mints.contains(mint) { return Ok(false) }
        let mut record = ObservedWalletLedgerRecord {
            schema: "KEFI_OBSERVED_WALLET_MINT_LEDGER_V1".to_owned(), sequence: self.sequence + 1,
            ledger_boundary_unix_ns: self.boundary, mint: mint.to_owned(),
            first_action_identity: sha256(format!("kefi-observed-wallet-v1|{}|{}|{}|{}|{}", signature, slot, event_index, mint, side).as_bytes()),
            signature: signature.to_owned(), slot, observed_at_unix_ns: received_ns,
            source_provenance_sha256: source_sha256.to_owned(), previous_record_sha256: self.tail.clone(),
            source_kind: "LIVE_YELLOWSTONE_DIRECT_WALLET_ACTION".to_owned(), record_sha256: String::new(),
        };
        record.record_sha256 = observed_wallet_record_hash(&record);
        self.file.write_all(canonical_json(&record)?.as_bytes())?; self.file.write_all(b"\n")?; self.file.sync_all()?;
        self.sequence = record.sequence; self.tail = record.record_sha256; self.seen_mints.insert(mint.to_owned());
        Ok(true)
    }
}

impl Drop for ObservedWalletLedger { fn drop(&mut self) { let _ = fs::remove_file(&self.lock_path); } }

#[derive(Serialize)]
struct Row<'a> {
    schema: &'a str, source_instance_id: &'a str, continuity_epoch: u64, durable_output_sequence: u64, received_at_unix_ns: u128,
    source_frame_sha256: &'a str, event_index: usize, intrinsic_event_id: String, slot: u64, signature: &'a str,
    pool: &'a str, mint: &'a str, side: &'a str, base_amount: u64, pool_base_raw: u64, pool_quote_raw: u64, pool_quote_delta: u64,
    fee_tuple: (u64, u64, u64, u64, u64), layout: &'a str, event_action_type: &'a str,
    first_public_observation_unix_ns: u128, first_public_observation_id: &'a str,
}

#[derive(Default)]
struct Stats { source_frames: u64, rows: u64, unique_pools: BTreeMap<String, (u128, String)>, rejected: u64, duplicates: u64, conflicts: u64, pool_resolutions: u64, pool_cache_hits: u64, unsupported_quote_events: u64, legacy_layout_disagreements: u64, first_ns: Option<u128>, last_ns: Option<u128>, latest_slot: Option<u64> }

fn pool_is_explicitly_unusable(error: &str) -> bool {
    error.starts_with("PumpSwap Pool has no supported quote mint")
        || error.starts_with("PumpSwap Pool has two quote mints")
        || error.starts_with("PumpSwap Pool account is absent")
}

#[derive(Serialize)]
struct Manifest<'a> {
    schema: &'a str, teacher_independent: bool, selected_centred: bool, all_supported_pools_covered: bool, uses_future_outcomes: bool,
    source_instance: &'a str, continuity_epoch: u64, source_interval_received_at_unix_ns: (Option<u128>, Option<u128>),
    row_count: u64, unique_pools: usize, source_frames: u64, rejected_rows: u64, duplicate_rows_discarded: u64, conflicting_identities: u64,
    events_path: String, events_sha256: String, compressed_bytes: u64, duration_seconds: u64, estimated_bytes_per_day: u128,
    writer_tool_sha256: String, collector_or_promotion_input_opened: bool, source_filter: &'a str, source_replay_from_slot: Option<u64>, validation_status: &'a str,
}

fn write_json_atomic(path: &Path, value: &impl Serialize) -> Result<()> { let temporary = path.with_extension("json.tmp"); let mut output = File::create(&temporary)?; output.write_all(canonical_json(value)?.as_bytes())?; output.write_all(b"\n")?; output.sync_all()?; fs::rename(temporary, path)?; Ok(()) }

#[tokio::main]
async fn main() -> Result<()> {
    // The VPS dependency graph enables both Rustls providers transitively.
    // Select ring explicitly so the isolated probe cannot panic before it has
    // opened its source stream.
    rustls::crypto::ring::default_provider().install_default().map_err(|_| anyhow::anyhow!("unable to install rustls ring provider"))?;
    let endpoint = env::var("YELLOWSTONE_FULL_META_ENDPOINT").context("YELLOWSTONE_FULL_META_ENDPOINT missing")?;
    let token: AsciiMetadataValue = env::var("SHYFT_GRPC_X_TOKEN").context("SHYFT_GRPC_X_TOKEN missing")?.parse().context("invalid x-token")?;
    let observer_mode = env::var("KEFI_CHOICE_STATE_OBSERVER").ok().as_deref() == Some("1");
    let teacher_wallet = if observer_mode { Some(env::var("KEFI_TRACKED_WALLET").context("KEFI_TRACKED_WALLET required in observer mode")?) } else { None };
    let pool_identity_rpc_url = env::var("KEFI_POOL_IDENTITY_RPC_URL").context("KEFI_POOL_IDENTITY_RPC_URL missing; refusing non-authoritative mint identity")?;
    let root = PathBuf::from(env::var("KEFI_POOL_STATE_ROOT").context("KEFI_POOL_STATE_ROOT missing")?);
    let seconds = env::var("KEFI_POOL_STATE_VALIDATION_SECONDS").unwrap_or_else(|_| "180".to_owned()).parse::<u64>()?;
    let snapshot_target = env::var("KEFI_POOL_STATE_SNAPSHOT_TARGET").unwrap_or_else(|_| "60".to_owned()).parse::<u64>()?;
    let max_runtime_seconds = if observer_mode { 86_400 } else { 900 };
    if !(60..=max_runtime_seconds).contains(&seconds) { bail!("KEFI_POOL_STATE_VALIDATION_SECONDS must be 60..={max_runtime_seconds}") }
    if observer_mode && snapshot_target == 0 { bail!("KEFI_POOL_STATE_SNAPSHOT_TARGET must be positive") }
    let max_bytes = env::var("KEFI_POOL_STATE_MAX_BYTES").unwrap_or_else(|_| "104857600".to_owned()).parse::<u64>()?;
    let source_replay_from_slot = env::var("KEFI_SOURCE_REPLAY_FROM_SLOT").ok().map(|value| value.parse::<u64>()).transpose().context("invalid KEFI_SOURCE_REPLAY_FROM_SLOT")?;
    let started_ns = now_ns(); let source_instance = format!("{}-{}-pid{}", if observer_mode { "choice-state-v1" } else { "pool-state-v1" }, started_ns, std::process::id());
    let partial = root.join(format!(".partial-{source_instance}")); let sealed = root.join(format!("validation-{source_instance}"));
    if partial.exists() || sealed.exists() { bail!("refusing to reuse validation directory") }
    fs::create_dir_all(&partial)?;
    let durable_snapshot_dir = partial.join("durable-snapshots");
    let snapshot_receipt_dir = partial.join("snapshot-receipts");
    if observer_mode { fs::create_dir_all(&durable_snapshot_dir)?; fs::create_dir_all(&snapshot_receipt_dir)?; }
    let identity_cache_path = root.join("pool-identity-cache-v1.json");
    let events_path = partial.join(if observer_mode { "choice-snapshots.jsonl.zst" } else { "events.jsonl.zst" });
    // Stream directly through the already-installed zstd executable.  This
    // avoids a new Rust dependency/toolchain requirement and never leaves an
    // uncompressed on-disk event copy behind.
    let output_file = File::create(&events_path)?;
    let mut zstd = Command::new("zstd").args(["-q", "-3", "-c"]).stdin(Stdio::piped()).stdout(Stdio::from(output_file)).spawn().context("launching zstd stream encoder")?;
    let mut output = BufWriter::new(zstd.stdin.take().context("zstd stdin unavailable")?);
    let channel = Channel::from_shared(endpoint)?.tls_config(ClientTlsConfig::new().with_native_roots())?.connect_timeout(Duration::from_secs(10)).tcp_keepalive(Some(Duration::from_secs(15))).keep_alive_timeout(Duration::from_secs(5)).keep_alive_while_idle(true).http2_keep_alive_interval(Duration::from_secs(15)).connect().await?;
    let token_for_interceptor = token.clone();
    let mut client = GeyserClient::with_interceptor(channel, move |mut request: Request<()>| { request.metadata_mut().insert("x-token", token_for_interceptor.clone()); Ok::<_, Status>(request) }).max_decoding_message_size(128 * 1024 * 1024);
    let request_wallet = teacher_wallet.clone();
    let requests = stream::once(async move { request(source_replay_from_slot, request_wallet.as_deref()) }).chain(stream::pending());
    let mut updates = client.subscribe(Request::new(requests)).await?.into_inner();
    let mut observed_wallet_ledger = if observer_mode {
        let path = PathBuf::from(env::var("KEFI_OBSERVED_WALLET_LEDGER_PATH").context("KEFI_OBSERVED_WALLET_LEDGER_PATH required in observer mode")?);
        let boundary = env::var("KEFI_OBSERVED_WALLET_LEDGER_BOUNDARY_UNIX_NS").context("KEFI_OBSERVED_WALLET_LEDGER_BOUNDARY_UNIX_NS required in observer mode")?.parse::<u128>()?;
        Some(ObservedWalletLedger::open(path, boundary)?)
    } else { None };
    let started = Instant::now(); let mut last_health_write = Instant::now(); let mut sequence = 0_u64; let mut stats = Stats::default(); let mut seen: HashMap<String, String> = HashMap::new(); let mut validation_error: Option<String> = None; let mut storage_cap_reached = false; let mut snapshot_target_reached = false;
    let mut pools: BTreeMap<(String, String), PoolState> = BTreeMap::new(); let mut snapshots = 0_u64; let mut durable_snapshot_bytes = 0_u64; let mut warmup_ineligible_teacher_selections = 0_u64;
    let http = reqwest::Client::builder().timeout(Duration::from_secs(8)).build()?;
    let mut identities: BTreeMap<String, PoolIdentity> = if identity_cache_path.exists() {
        serde_json::from_slice(&fs::read(&identity_cache_path)?).context("Pool identity cache is malformed; refusing to continue")?
    } else { BTreeMap::new() };
    // Defend against a syntactically valid but internally corrupt persisted cache.
    if identities.iter().any(|(pool, identity)| pool != &identity.pool || identity.account_owner != PUMPSWAP_PROGRAM) { bail!("Pool identity cache integrity failure") }
    let mut unsupported_quote_pools: BTreeMap<String, String> = BTreeMap::new();
    while started.elapsed() < Duration::from_secs(seconds) {
        let received_ns = now_ns(); let update = match tokio::time::timeout(Duration::from_secs(20), updates.next()).await {
            Ok(Some(Ok(update))) => update, Ok(Some(Err(error))) => { validation_error = Some(format!("receive error: {error}")); break; }, Ok(None) => { validation_error = Some("stream ended".to_owned()); break; }, Err(_) => { validation_error = Some("source silence exceeded 20 seconds".to_owned()); break; },
        };
        sequence += 1; stats.source_frames += 1; stats.first_ns.get_or_insert(received_ns); stats.last_ns = Some(received_ns);
        let raw = update.encode_to_vec(); let raw_sha = sha256(&raw);
        let Some(UpdateOneof::Transaction(transaction_update)) = update.update_oneof else { continue; };
        stats.latest_slot = Some(transaction_update.slot);
        let Some(info) = transaction_update.transaction else { stats.rejected += 1; continue; };
        let Some(transaction) = info.transaction.as_ref() else { stats.rejected += 1; continue; };
        let Some(message) = transaction.message.as_ref() else { stats.rejected += 1; continue; };
        let Some(meta) = info.meta.as_ref() else { stats.rejected += 1; continue; };
        let signature = bs58::encode(&info.signature).into_string();
        let instructions = decode_instructions(message, meta);
        let decoded = match decode_events(meta) { Ok(rows) => rows, Err(error) => {
            stats.rejected += 1;
            // A single retained failure fixture is allowed: it is the exact
            // protobuf update needed to reproduce an unsupported layout, not a
            // general event archive.  It makes future decoder changes auditable.
            write_json_atomic(&partial.join("failed-layout-fixture.json"), &serde_json::json!({
                "schema":"KEFI_PUMPSWAP_MULTI_MINT_FIXTURE_V1", "slot":transaction_update.slot,
                "signature":signature, "durable_output_sequence":sequence,
                "source_frame_sha256":raw_sha, "decode_error":format!("{error:#}"),
                "pumpswap_invocations":instructions.iter().map(|ix| serde_json::json!({
                    "side":ix.side, "variant":ix.variant, "pool_index_0":ix.accounts.get(PUMPSWAP_POOL_INDEX),
                    "user_index_1":ix.accounts.get(PUMPSWAP_USER_INDEX), "buy_base_mint_index_3":ix.accounts.get(PUMPSWAP_BUY_BASE_MINT_INDEX),
                    "sell_base_mint_index_4":ix.accounts.get(PUMPSWAP_SELL_BASE_MINT_INDEX), "accounts":ix.accounts,
                })).collect::<Vec<_>>(),
                "pre_token_balances":meta.pre_token_balances.iter().map(|b| serde_json::json!({"account_index":b.account_index,"mint":b.mint,"owner":b.owner,"amount":b.ui_token_amount.as_ref().map(|a| &a.amount)})).collect::<Vec<_>>(),
                "post_token_balances":meta.post_token_balances.iter().map(|b| serde_json::json!({"account_index":b.account_index,"mint":b.mint,"owner":b.owner,"amount":b.ui_token_amount.as_ref().map(|a| &a.amount)})).collect::<Vec<_>>(),
                "protobuf_update_base64":BASE64_STANDARD.encode(&raw),
            }))?;
            validation_error = Some(format!("unsupported supported-PumpSwap layout: {error:#}")); break;
        } };
        // Wallet-only transactions are outside the scientific public pool
        // stream. Their changed token mints are nevertheless durable history,
        // so a later PumpSwap buy cannot be misclassified as first observed.
        if observer_mode && decoded.is_empty() {
            let ledger = observed_wallet_ledger.as_mut().context("observer ledger unavailable")?;
            let wallet = teacher_wallet.as_deref().context("observer wallet unavailable")?;
            for mint in direct_wallet_token_mints(meta, wallet) {
                ledger.observe(&mint, &signature, transaction_update.slot, 0, "DIRECT_WALLET_NON_PUMPSWAP_ACTION", received_ns, &raw_sha)?;
            }
            continue;
        }
        // Resolve every new Pool referenced by this transaction as one bounded
        // authoritative RPC batch.  This prevents an event burst from turning
        // into one HTTP request per event while retaining transaction order.
        let unknown_pools = decoded.iter().map(|(_, event)| event.pool.clone()).filter(|pool| !identities.contains_key(pool) && !unsupported_quote_pools.contains_key(pool)).collect::<BTreeSet<_>>().into_iter().collect::<Vec<_>>();
        if !unknown_pools.is_empty() {
            match resolve_pool_identity_batch(&http, &pool_identity_rpc_url, &unknown_pools).await {
                Ok(results) => for (pool, result) in results {
                    match result {
                        Ok(identity) => match cache_identity(&mut identities, identity) { Ok(_) => stats.pool_resolutions += 1, Err(error) => { stats.conflicts += 1; validation_error = Some(format!("Pool identity cache conflict: {error:#}")); break; } },
                        // The Pool account is canonical identity evidence.  A
                        // pool that cannot be resolved is excluded; it is
                        // never guessed from transaction-wide mint balances.
                        Err(error) if pool_is_explicitly_unusable(&error) => { unsupported_quote_pools.insert(pool, error); }
                        Err(error) => { stats.rejected += 1; validation_error = Some(format!("authoritative Pool identity batch resolution failed: {error}")); break; }
                    }
                },
                Err(error) => { stats.rejected += 1; validation_error = Some(format!("authoritative Pool identity batch request failed: {error:#}")); }
            }
            // Persist only immutable Pool identity metadata.  A failed later
            // source gate can safely reuse these independently validated
            // account decodes without retaining market transitions.
            write_json_atomic(&identity_cache_path, &identities)?;
            if validation_error.is_some() { break; }
        }
        // Resolve the transaction-level teacher decision before processing any
        // event.  This prevents duplicate event emission from becoming two
        // scientific selections and makes multi-mint teacher transactions a
        // hard wall rather than an arbitrary attribution choice.
        let has_unresolved_teacher_event = observer_mode && teacher_wallet.as_deref().map(|wallet| decoded.iter().any(|(_, event)| event.user == wallet && !identities.contains_key(&event.pool))).unwrap_or(false);
        let has_resolved_teacher_buy = observer_mode && teacher_wallet.as_deref().map(|wallet| decoded.iter().any(|(_, event)| event.user == wallet && event.side == "BUY" && identities.contains_key(&event.pool))).unwrap_or(false);
        // Unsupported Pool identity is not a scientific selection candidate.
        // It is recorded only as direct-wallet history.  Mixing it with a
        // separately supported teacher BUY in one transaction is ambiguous
        // and remains a hard wall.
        if has_unresolved_teacher_event && has_resolved_teacher_buy {
            stats.rejected += 1;
            validation_error = Some("AMBIGUOUS_MIXED_SUPPORTED_AND_UNSUPPORTED_TEACHER_ACTION".to_owned());
            break;
        }
        if has_unresolved_teacher_event {
            let ledger = observed_wallet_ledger.as_mut().context("observer ledger unavailable")?;
            let wallet = teacher_wallet.as_deref().context("observer wallet unavailable")?;
            for mint in direct_wallet_token_mints(meta, wallet) {
                ledger.observe(&mint, &signature, transaction_update.slot, 0, "DIRECT_WALLET_UNSUPPORTED_PUMPSWAP_ACTION", received_ns, &raw_sha)?;
            }
        }
        let canonical_teacher_buy = match if has_unresolved_teacher_event { Ok(None) } else { canonical_teacher_buy_event(&decoded, &identities, teacher_wallet.as_deref()) } {
            Ok(value) => value,
            Err(error) => {
                stats.rejected += 1;
                validation_error = Some(format!("{error:#}"));
                break;
            }
        };
        for (event_index, mut event) in decoded {
            if unsupported_quote_pools.contains_key(&event.pool) { stats.unsupported_quote_events += 1; continue; }
            let identity = match identities.get(&event.pool) {
                Some(identity) => { stats.pool_cache_hits += 1; identity.clone() },
                None => { stats.rejected += 1; validation_error = Some(format!("Pool identity was not present after authoritative batch for {}", event.pool)); break; },
            };
            if let Some(instruction_mint) = instruction_mint_cross_check(event.side, &event.pool, &instructions) {
                if instruction_mint != identity.selection_mint {
                    // A routed transaction can retain a legacy direct-layout
                    // discriminator while shifting account positions.  This
                    // is diagnostic only: it is not an authoritative identity
                    // conflict and cannot override event.pool -> Pool account.
                    stats.legacy_layout_disagreements += 1;
                }
            }
            event.mint = identity.selection_mint;
            let intrinsic_event_id = sha256(format!("pumpswap-v1|{}|{}|{}|{}|{}|{}", transaction_update.slot, signature, event_index, event.pool, event.mint, event.side).as_bytes());
            let first = stats.unique_pools.entry(format!("{}|{}", event.pool, event.mint)).or_insert_with(|| (received_ns, intrinsic_event_id.clone()));
            let pool_key = (event.pool.clone(), event.mint.clone());
            let teacher_action = observer_mode && teacher_wallet.as_deref() == Some(event.user.as_str());
            let teacher_buy = teacher_action && event.side == "BUY";
            // Persist first observed wallet action before deciding whether a
            // BUY can be a new-token selection. This state survives observer,
            // source-epoch, machine, and cohort restart boundaries.
            let mut wallet_newness: Option<(u64, String, String, u128)> = None;
            if teacher_action && (canonical_teacher_buy.is_none() || canonical_teacher_buy == Some(event_index)) {
                let ledger = observed_wallet_ledger.as_mut().context("observer ledger unavailable")?;
                let before_sequence = ledger.sequence; let before_tail = ledger.tail.clone(); let boundary = ledger.boundary;
                if ledger.observe(&event.mint, &signature, transaction_update.slot, event_index, event.side, received_ns, &raw_sha)? {
                    wallet_newness = Some((before_sequence, before_tail, ledger.tail.clone(), boundary));
                }
            }
            let first_teacher_selection = teacher_buy && wallet_newness.is_some();
            if first_teacher_selection && started.elapsed() >= Duration::from_secs(CHOICESTATE_WARMUP_SECONDS) {
                // Snapshot strictly before the teacher event updates its selected pool.
                let candidates = pools.iter().filter(|(_, state)| state.first_ns <= received_ns.saturating_sub(CHOICESTATE_WARMUP_SECONDS as u128 * 1_000_000_000) && state.pool_base > 0 && state.pool_quote > 0 && state.prior_pre_pool_quote.is_some()).map(|((pool, mint), state)| vector(pool, mint, state, received_ns)).collect::<Vec<_>>();
                let chosen_admitted = candidates.iter().any(|candidate| candidate.pool == event.pool && candidate.mint == event.mint);
                // A selection without fully warm public state is not a
                // scientific snapshot. Record the exclusion and continue;
                // never fill missing history with zero-valued features.
                if !chosen_admitted || candidates.len() <= 1 { warmup_ineligible_teacher_selections += 1; continue; }
                let (wallet_ledger_sequence_before, wallet_ledger_tail_before, wallet_ledger_record_sha256, wallet_ledger_boundary_unix_ns) = wallet_newness.as_ref().context("first selection missing durable wallet-newness proof")?;
                let snapshot = ChoiceSnapshot { schema: "KEFI_STREAMING_CHOICE_STATE_OBSERVER_V1", source_instance_id: &source_instance, continuity_epoch: 1, teacher_signature: &signature, teacher_slot: transaction_update.slot, teacher_event_index: event_index, teacher_received_at_unix_ns: received_ns, teacher_durable_output_sequence: sequence, selection_kind: "FIRST_OBSERVED_SELECTION", chosen_pool: &event.pool, chosen_mint: &event.mint, chosen_admitted, wallet_ledger_boundary_unix_ns: *wallet_ledger_boundary_unix_ns, wallet_ledger_sequence_before: *wallet_ledger_sequence_before, wallet_ledger_tail_before, wallet_ledger_record_sha256, candidates };
                let json = canonical_json(&snapshot)?;
                // The atomic writer appends one newline. Bind the receipt to
                // those exact persisted bytes, not merely the JSON payload.
                let snapshot_sha256 = sha256(format!("{json}\n").as_bytes());
                let receipt = serde_json::json!({"schema":"KEFI_CHOICESTATE_SNAPSHOT_RECEIPT_V1","snapshot_sequence":snapshots + 1,"snapshot_sha256":snapshot_sha256,"source_instance_id":source_instance,"continuity_epoch":1,"teacher_slot":transaction_update.slot,"teacher_durable_output_sequence":sequence,"candidate_count":snapshot.candidates.len(),"pre_action_only":true,"warmup_complete":true,"rolling_history_seconds":CHOICESTATE_WARMUP_SECONDS,"chosen_pool_previously_eligible":true,"teacher_event_excluded_from_state":true,"authoritative_pool_identity":true,"wallet_ledger_boundary_unix_ns":wallet_ledger_boundary_unix_ns,"wallet_ledger_sequence_before":wallet_ledger_sequence_before,"wallet_ledger_tail_before":wallet_ledger_tail_before,"wallet_ledger_record_sha256":wallet_ledger_record_sha256,"outcomes_or_pnl_accessed":false});
                let receipt_json = canonical_json(&receipt)?;
                let snapshot_bytes = json.len() as u64 + receipt_json.len() as u64 + 2;
                if durable_snapshot_bytes.saturating_add(snapshot_bytes) > max_bytes { storage_cap_reached = true; break; }
                write_json_atomic(&durable_snapshot_dir.join(format!("{:020}.json", snapshots + 1)), &snapshot)?;
                write_json_atomic(&snapshot_receipt_dir.join(format!("{:020}.json", snapshots + 1)), &receipt)?;
                if snapshots == 0 { write_json_atomic(&partial.join("first-snapshot-integrity.json"), &receipt)?; }
                durable_snapshot_bytes += snapshot_bytes;
                output.write_all(json.as_bytes())?; output.write_all(b"\n")?; output.flush()?; snapshots += 1;
                if observer_mode && snapshots >= snapshot_target { snapshot_target_reached = true; break; }
            }
            let state = pools.entry(pool_key).or_insert_with(|| PoolState { first_ns: received_ns, first_id: intrinsic_event_id.clone(), last_ns: received_ns, last_sequence: sequence, pool_base: event.pool_base, pool_quote: event.pool_quote, fees: (event.lp_fee_bps,event.protocol_fee_bps,event.coin_creator_bps,event.cashback_bps,event.buyback_bps), prior_pre_pool_quote: None, last_buy_ns: None, flows: VecDeque::new() });
            apply_event(state, &event, received_ns, sequence);
            if observer_mode { continue; }
            let row = Row { schema: SCHEMA, source_instance_id: &source_instance, continuity_epoch: 1, durable_output_sequence: sequence, received_at_unix_ns: received_ns, source_frame_sha256: &raw_sha, event_index, intrinsic_event_id: intrinsic_event_id.clone(), slot: transaction_update.slot, signature: &signature, pool: &event.pool, mint: &event.mint, side: event.side, base_amount: event.base_amount, pool_base_raw: event.pool_base, pool_quote_raw: event.pool_quote, pool_quote_delta: event.pool_quote_delta, fee_tuple: (event.lp_fee_bps, event.protocol_fee_bps, event.coin_creator_bps, event.cashback_bps, event.buyback_bps), layout: "PUMPSWAP_SUPPORTED", event_action_type: event.side, first_public_observation_unix_ns: first.0, first_public_observation_id: &first.1 };
            let row_json = canonical_json(&row)?;
            if let Some(previous) = seen.get(&intrinsic_event_id) { if previous == &row_json { stats.duplicates += 1; continue; } stats.conflicts += 1; validation_error = Some("conflicting intrinsic event identity".to_owned()); break; }
            seen.insert(intrinsic_event_id, row_json.clone()); output.write_all(row_json.as_bytes())?; output.write_all(b"\n")?; stats.rows += 1;
        }
        if validation_error.is_some() || storage_cap_reached || snapshot_target_reached { break; }
        if observer_mode && last_health_write.elapsed() >= Duration::from_secs(15) {
            write_json_atomic(&partial.join("health.json"), &serde_json::json!({
                "schema": "KEFI_STREAMING_CHOICE_STATE_OBSERVER_V1_HEALTH",
                "status": "RUNNING",
                "warmup_complete": started.elapsed() >= Duration::from_secs(CHOICESTATE_WARMUP_SECONDS),
                "required_warmup_seconds": CHOICESTATE_WARMUP_SECONDS,
                "snapshot_target": snapshot_target,
                "source_instance_id": source_instance,
                "continuity_epoch": 1,
                "source_replay_from_slot": source_replay_from_slot,
                "last_update_unix_ns": now_ns(),
                "source_frames": stats.source_frames,
                "latest_slot": stats.latest_slot,
                "pools_maintained": pools.len(),
                "snapshots": snapshots,
                "warmup_ineligible_teacher_selections": warmup_ineligible_teacher_selections,
                "durable_snapshot_bytes": durable_snapshot_bytes,
                "pool_identity_resolutions": stats.pool_resolutions,
                "pool_identity_cache_hits": stats.pool_cache_hits,
                "decoder_rejections": stats.rejected,
                "identity_conflicts": stats.conflicts,
                "unsupported_quote_events": stats.unsupported_quote_events,
                "legacy_layout_disagreements": stats.legacy_layout_disagreements,
                "durable_output_sequence": sequence,
                "observed_wallet_ledger_records": observed_wallet_ledger.as_ref().map(|ledger| ledger.sequence),
                "observed_wallet_ledger_tail_sha256": observed_wallet_ledger.as_ref().map(|ledger| ledger.tail.clone())
            }))?;
            last_health_write = Instant::now();
        }
    }
    output.flush()?; drop(output);
    if !zstd.wait()?.success() { bail!("zstd stream encoder failed") }
    let compressed_bytes = events_path.metadata()?.len(); let duration_seconds = started.elapsed().as_secs().max(1); let rate = (compressed_bytes as u128).saturating_mul(86_400) / duration_seconds as u128;
    if compressed_bytes > max_bytes { validation_error = Some(format!("compact stream exceeded byte cap: {compressed_bytes}>{max_bytes}")); }
    let validation_status = if snapshot_target_reached { "SNAPSHOT_TARGET_REACHED" } else if storage_cap_reached { "STORAGE_CAP_REACHED" } else if validation_error.is_none() && (if observer_mode { stats.source_frames > 0 } else { stats.rows > 0 }) && stats.rejected == 0 && stats.conflicts == 0 { "PASS" } else { "FAIL_CLOSED" };
    let manifest = Manifest { schema: if observer_mode { "KEFI_STREAMING_CHOICE_STATE_OBSERVER_V1" } else { "KEFI_CHOICESET_CANONICAL_INPUT_V1" }, teacher_independent: true, selected_centred: false, all_supported_pools_covered: validation_status == "PASS", uses_future_outcomes: false, source_instance: &source_instance, continuity_epoch: 1, source_interval_received_at_unix_ns: (stats.first_ns, stats.last_ns), row_count: if observer_mode { snapshots } else { stats.rows }, unique_pools: pools.len(), source_frames: stats.source_frames, rejected_rows: stats.rejected, duplicate_rows_discarded: stats.duplicates, conflicting_identities: stats.conflicts, events_path: sealed.join(if observer_mode { "choice-snapshots.jsonl.zst" } else { "events.jsonl.zst" }).display().to_string(), events_sha256: sha256_file(&events_path)?, compressed_bytes, duration_seconds, estimated_bytes_per_day: rate, writer_tool_sha256: sha256_file(&env::current_exe().context("locating deployed feed binary")?)?, collector_or_promotion_input_opened: false, source_filter: "all confirmed PumpSwap transactions; no tracked-wallet or promotion input", source_replay_from_slot, validation_status };
    write_json_atomic(&partial.join("manifest.json"), &manifest)?;
    write_json_atomic(&partial.join("unsupported-quote-pools.json"), &unsupported_quote_pools)?;
    write_json_atomic(&partial.join("validation.json"), &serde_json::json!({"status": manifest.validation_status, "failure": validation_error, "latest_slot": stats.latest_slot, "source_epoch": 1, "pre_h3_accessed": false, "choice_state_observer": observer_mode, "pools_maintained": pools.len(), "snapshots": snapshots, "pool_identity_resolutions":stats.pool_resolutions, "pool_identity_cache_hits":stats.pool_cache_hits, "pool_identity_conflicts":stats.conflicts, "unsupported_quote_events":stats.unsupported_quote_events, "unsupported_quote_pools":unsupported_quote_pools.len(), "legacy_layout_disagreements":stats.legacy_layout_disagreements}))?;
    fs::rename(&partial, &sealed)?;
    println!("{}", canonical_json(&serde_json::json!({"root": sealed, "status": manifest.validation_status, "rows": manifest.row_count, "compressed_bytes": manifest.compressed_bytes, "estimated_bytes_per_day": manifest.estimated_bytes_per_day}))?);
    if manifest.validation_status == "FAIL_CLOSED" { bail!("FAIL_CLOSED: teacher-independent feed validation did not pass") }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state() -> PoolState { PoolState { first_ns: 1, first_id: "first".into(), last_ns: 1, last_sequence: 1, pool_base: 100, pool_quote: 200, fees: (1,2,3,4,5), prior_pre_pool_quote: None, last_buy_ns: None, flows: VecDeque::new() } }
    fn buy(pool: &str, mint: &str, user: &str) -> Event { Event { side: "BUY", base_amount: 1, pool_base: 90, pool_quote: 220, pool_quote_delta: 20, lp_fee_bps:1, protocol_fee_bps:2, coin_creator_bps:3, cashback_bps:4, buyback_bps:5, pool:pool.into(), user:user.into(), mint:mint.into() } }

    #[test]
    fn state_is_deterministic_and_teacher_identity_is_not_state_input() {
        let event = buy("pool", "mint", "teacher");
        let mut left = state(); let mut right = state();
        apply_event(&mut left, &event, 20, 7);
        let mut same_public_event = event; same_public_event.user = "different-teacher".into();
        apply_event(&mut right, &same_public_event, 20, 7);
        assert_eq!(serde_json::to_string(&vector("pool", "mint", &left, 21)).unwrap(), serde_json::to_string(&vector("pool", "mint", &right, 21)).unwrap());
    }

    #[test]
    fn vector_before_event_cannot_include_the_event_or_future_events() {
        let mut state = state();
        let before = serde_json::to_string(&vector("pool", "mint", &state, 10)).unwrap();
        apply_event(&mut state, &buy("pool", "mint", "teacher"), 10, 8);
        let after = serde_json::to_string(&vector("pool", "mint", &state, 11)).unwrap();
        assert_ne!(before, after);
        // A frozen pre-action serialization stays fixed even if later events arrive.
        assert_eq!(before, serde_json::to_string(&ChoiceVector { pool:"pool", mint:"mint", quote_reserve_raw:200, base_reserve_raw:100, fee_tuple:(1,2,3,4,5), observed_pool_age_seconds:0, seconds_since_prior_observed_buy:None, prior_pre_pool_quote_raw:None, buy_count_5s:0,buy_count_20s:0,buy_count_60s:0,buy_quote_flow_5s:0,buy_quote_flow_20s:0,buy_quote_flow_60s:0,no_prior_buy:true,sell_count_60s:0,sell_quote_flow_60s:0,first_public_observation_unix_ns:1,last_event_unix_ns:1,last_durable_output_sequence:1 }).unwrap());
    }

    fn ix(side: &'static str, pool: &str, mint: &str) -> Instruction {
        let mut accounts = vec!["pool".to_owned(), "user".to_owned(), "x".to_owned(), mint.to_owned(), WRAPPED_SOL_MINT.to_owned()];
        accounts.resize(if side == "BUY" { PUMPSWAP_MIN_BUY_ACCOUNTS } else { PUMPSWAP_MIN_SELL_ACCOUNTS }, "x".to_owned());
        accounts[0] = pool.to_owned();
        if side == "SELL" { accounts[3] = WRAPPED_SOL_MINT.to_owned(); accounts[4] = mint.to_owned(); }
        Instruction { side, variant: "fixture", accounts }
    }

    #[test]
    fn explicit_layout_handles_multi_invocation_and_rejects_ambiguity() {
        let rows = vec![ix("BUY", "pool-a", "mint-a"), ix("BUY", "pool-b", "mint-b")];
        assert_eq!(explicit_layout_mint("BUY", "pool-b", &rows).unwrap(), "mint-b");
        assert_eq!(explicit_layout_mint("SELL", "pool-a", &[ix("SELL", "pool-a", "mint-sell")]).unwrap(), "mint-sell");
        let duplicated = vec![ix("BUY", "pool-a", "mint-a"), ix("BUY", "pool-a", "mint-a")];
        assert_eq!(explicit_layout_mint("BUY", "pool-a", &duplicated).unwrap(), "mint-a");
        let ambiguous = vec![ix("BUY", "pool-a", "mint-a"), ix("BUY", "pool-a", "mint-b")];
        assert!(explicit_layout_mint("BUY", "pool-a", &ambiguous).is_err());
        assert!(explicit_layout_mint("SELL", "pool-a", &rows).is_err());
    }

    fn pool_fixture(base: &str, quote: &str) -> Vec<u8> {
        let mut bytes = vec![0_u8; 203]; bytes[..8].copy_from_slice(&pool_account_discriminator());
        bytes[43..75].copy_from_slice(&bs58::decode(base).into_vec().unwrap());
        bytes[75..107].copy_from_slice(&bs58::decode(quote).into_vec().unwrap());
        bytes[139..171].copy_from_slice(&[7_u8; 32]); bytes[171..203].copy_from_slice(&[8_u8; 32]); bytes
    }

    #[test]
    fn authoritative_pool_account_resolves_routed_sell_fixture_identity() {
        // Exact retained routed fixture pool.  The expected mint was also
        // independently present in the transaction's token-balance evidence.
        let raw = BASE64_STANDARD.decode("8ZptBBGxbbz/AADkWKFrWOY9nen7N55JDfBSXT87vFKdhldlY/+9yeLPAgabiFf+q4GE+2h/Y0YYwDXaxDncGus7VZig8AAAAAABCGgsiTqTbNEQDwMzCY4PyWM7JmgAP30IWJUQtNxVXIwJNWSGD8KsYEHJs3vFs9dAlTy3UkTrE6Nof4/bKzRjRxfNLBnzJZO5j59ldGfsidQo3ClCV7rqW/eKyeIiHKDnJrAQp8/8O1QtlgScPIYib81j6/8zcVh421ySNHHOeWBkAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=").unwrap();
        let identity = decode_pool_identity("5Usi52pa9tinNn4Bc4MBQfu8S4H7CdFAwCNdqoFtyZrK", PUMPSWAP_PROGRAM, &raw, 456_373_320).unwrap();
        assert_eq!(identity.base_mint, WRAPPED_SOL_MINT);
        assert_eq!(identity.quote_mint, "ZpPpxuMLtwskcxNjgNESGgk6HafcTXmgb3NhVqK7eKu");
        assert_eq!(identity.selection_mint, "ZpPpxuMLtwskcxNjgNESGgk6HafcTXmgb3NhVqK7eKu");
    }

    #[test]
    fn pool_identity_rejects_owner_layout_quote_and_cache_conflicts() {
        let base = "ZpPpxuMLtwskcxNjgNESGgk6HafcTXmgb3NhVqK7eKu";
        let valid = pool_fixture(base, WRAPPED_SOL_MINT);
        assert!(decode_pool_identity("pool", "not-pump", &valid, 1).is_err());
        let mut malformed = valid.clone(); malformed[0] ^= 1; assert!(decode_pool_identity("pool", PUMPSWAP_PROGRAM, &malformed, 1).is_err());
        let unsupported_quote = pool_fixture(base, base); assert!(decode_pool_identity("pool", PUMPSWAP_PROGRAM, &unsupported_quote, 1).is_err());
        let one = decode_pool_identity("pool", PUMPSWAP_PROGRAM, &valid, 1).unwrap();
        let mut cache = BTreeMap::new(); cache_identity(&mut cache, one.clone()).unwrap();
        let mut conflicting = one; conflicting.selection_mint = "11111111111111111111111111111111".into();
        assert!(cache_identity(&mut cache, conflicting).is_err());
    }

    #[test]
    fn direct_layout_is_only_a_cross_check_not_a_router_fallback() {
        let direct = ix("SELL", "pool-a", "mint-sell");
        assert_eq!(instruction_mint_cross_check("SELL", "pool-a", &[direct]).as_deref(), Some("mint-sell"));
        // An unrecognised routed invocation has no vote over the Pool account.
        assert_eq!(instruction_mint_cross_check("SELL", "pool-a", &[]), None);
    }

    #[test]
    fn one_teacher_transaction_can_only_have_one_canonical_buy_decision() {
        let mut identities = BTreeMap::new();
        let identity = |pool: &str, mint: &str| PoolIdentity { pool: pool.into(), base_mint: mint.into(), quote_mint: WRAPPED_SOL_MINT.into(), selection_mint: mint.into(), pool_base_token_account: "base".into(), pool_quote_token_account: "quote".into(), account_owner: PUMPSWAP_PROGRAM.into(), layout: "fixture".into(), resolution_slot: 1 };
        identities.insert("pool".into(), identity("pool", "mint"));
        let rows = vec![(9, buy("pool", "ignored", "teacher")), (3, buy("pool", "ignored", "teacher"))];
        assert_eq!(canonical_teacher_buy_event(&rows, &identities, Some("teacher")).unwrap(), Some(3));
    }

    #[test]
    fn multi_mint_teacher_buy_is_a_hard_wall() {
        let mut identities = BTreeMap::new();
        let identity = |pool: &str, mint: &str| PoolIdentity { pool: pool.into(), base_mint: mint.into(), quote_mint: WRAPPED_SOL_MINT.into(), selection_mint: mint.into(), pool_base_token_account: "base".into(), pool_quote_token_account: "quote".into(), account_owner: PUMPSWAP_PROGRAM.into(), layout: "fixture".into(), resolution_slot: 1 };
        identities.insert("pool-a".into(), identity("pool-a", "mint-a"));
        identities.insert("pool-b".into(), identity("pool-b", "mint-b"));
        let rows = vec![(1, buy("pool-a", "ignored", "teacher")), (2, buy("pool-b", "ignored", "teacher"))];
        assert!(canonical_teacher_buy_event(&rows, &identities, Some("teacher")).unwrap_err().to_string().contains("AMBIGUOUS_MULTI_MINT_TEACHER_SELECTION"));
    }

    #[test]
    fn absent_or_unsupported_pool_is_excluded_without_identity_fallback() {
        assert!(pool_is_explicitly_unusable("PumpSwap Pool account is absent"));
        assert!(pool_is_explicitly_unusable("PumpSwap Pool has no supported quote mint"));
        assert!(!pool_is_explicitly_unusable("PumpSwap Pool account owner mismatch"));
        assert!(!pool_is_explicitly_unusable("Pool identity RPC error: timeout"));
    }
}
