use std::{env, fs, str::FromStr, time::Duration};

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use serde_json::{json, Value};
use solana_keypair::Keypair;
use solana_pubkey::Pubkey;
use solana_signer::Signer;
use tokio::time::sleep;

#[path = "../executor.rs"]
mod executor;
#[path = "../exit_watcher.rs"]
mod exit_watcher;

const PUMP_FUN_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const TOKEN_PROGRAM_ID: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const TOKEN_2022_PROGRAM_ID: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const WRAPPED_SOL_MINT: &str = "So11111111111111111111111111111111111111112";
const USDC_MINT: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const LAMPORTS_PER_SOL: f64 = 1_000_000_000.0;

#[derive(Debug, Clone)]
struct TokenPosition {
    mint: String,
    token_program_id: String,
    amount_raw: u64,
    ui_amount: Option<String>,
}

#[derive(Debug, Clone)]
struct CurveState {
    virtual_token_reserves: u64,
    virtual_sol_reserves: u64,
    complete: bool,
    creator: String,
}

#[derive(Debug)]
struct SignatureStatusCheck {
    status: String,
    ok: Option<bool>,
    confirmation_status: Option<String>,
    error: Option<String>,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<()> {
    load_env_file("/root/bot/FinalBot-cya/.env");
    load_env_file(".env");

    let rpc_url = read_rpc_url();
    let owner = wallet_pubkey()?;
    let client = reqwest::Client::new();
    let positions = fetch_nonzero_positions(&client, &rpc_url, &owner.to_string()).await?;
    println!(
        "{}",
        json!({
            "kind": "sellAllPumpfunPositionsStarted",
            "owner": owner.to_string(),
            "positionsSeen": positions.len(),
            "positions": positions.iter().map(|p| json!({
                "mint": p.mint,
                "tokenProgramId": p.token_program_id,
                "amountRaw": p.amount_raw,
                "uiAmount": p.ui_amount,
            })).collect::<Vec<_>>(),
        })
    );

    for position in positions {
        if position.mint == WRAPPED_SOL_MINT || position.mint == USDC_MINT {
            continue;
        }
        let bonding_curve = pumpfun_bonding_curve_pda(&position.mint)?;
        let curve = match fetch_curve_state(&client, &rpc_url, &bonding_curve).await {
            Ok(curve) => curve,
            Err(err) => {
                println!(
                    "{}",
                    json!({
                        "kind": "sellAllPumpfunPositionSkipped",
                        "mint": position.mint,
                        "reason": "curve_fetch_failed",
                        "error": err.to_string(),
                    })
                );
                continue;
            }
        };
        if curve.complete {
            println!(
                "{}",
                json!({
                    "kind": "sellAllPumpfunPositionSkipped",
                    "mint": position.mint,
                    "bondingCurve": bonding_curve,
                    "reason": "bonding_curve_complete",
                })
            );
            continue;
        }
        let expected = quote_sell_lamports(
            curve.virtual_token_reserves,
            curve.virtual_sol_reserves,
            position.amount_raw,
        )
        .unwrap_or(1)
        .max(1);
        let blockhash = executor::fetch_latest_blockhash(&rpc_url).await?;
        let (sell_v2, accounts) = executor::build_pumpfun_exit_sell_template(
            &position.mint,
            &bonding_curve,
            &curve.creator,
            Some(&position.token_program_id),
        )?;
        let command = executor::ExecutePumpfunSellCommand {
            id: format!("manual-flush-{}-{}", now_ms(), position.mint),
            route: if sell_v2 {
                "pumpfun-direct-sell-v2".to_string()
            } else {
                "pumpfun-direct-sell".to_string()
            },
            mint: position.mint.clone(),
            tokens_in_raw: position.amount_raw,
            expected_sol_out_lamports: expected,
            min_sol_out_lamports: 1,
            sell_v2,
            accounts: accounts
                .into_iter()
                .map(|account| executor::SourcePumpSwapAccountMeta {
                    pubkey: account.pubkey,
                    is_signer: account.is_signer,
                    is_writable: account.is_writable,
                })
                .collect(),
            blockhash,
            compute_unit_limit: parse_env_u64("COMPUTE_UNIT_LIMIT", 160_000).min(1_400_000) as u32,
            priority_fee_micro_lamports: parse_env_optional_u64(
                "RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS",
            )
            .or_else(|| parse_env_optional_u64("DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS"))
            .unwrap_or_else(|| parse_env_u64("PRIORITY_FEE_MICRO_LAMPORTS", 50_000)),
            jito_tip_lamports: parse_env_optional_u64("SELL_JITO_TIP_LAMPORTS")
                .or_else(|| parse_env_optional_u64("RUST_HOT_SNIPER_JITO_TIP_LAMPORTS"))
                .unwrap_or_else(|| parse_env_u64("TIP_LAMPORTS", 65_000)),
            helius_tip_lamports: parse_env_optional_u64("SELL_HELIUS_TIP_LAMPORTS")
                .or_else(|| parse_env_optional_u64("RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS"))
                .unwrap_or_else(|| parse_env_u64("HELIUS_TIP_LAMPORTS", 65_000)),
            use_jito_direct: parse_env_bool("USE_JITO_DIRECT", false),
            use_jito_fallback: parse_env_bool("USE_JITO_FALLBACK", true),
            sender_rpc_fallback_enabled: parse_env_bool("SENDER_RPC_FALLBACK_ENABLED", true),
            execution_multi_send_enabled: parse_env_bool("EXECUTION_MULTI_SEND_ENABLED", false)
                || parse_env_bool("BROADCAST_RACE_ENABLED", false),
            jito_send_mode: env::var("JITO_SEND_MODE").unwrap_or_else(|_| "bundle".to_string()),
            jito_transaction_urls: configured_jito_regions()
                .iter()
                .map(|region| format!("{}/api/v1/transactions", jito_region_base_url(region)))
                .collect(),
            jito_bundle_urls: configured_jito_regions()
                .iter()
                .map(|region| format!("{}/api/v1/bundles", jito_region_base_url(region)))
                .collect(),
            sender_endpoint_url: env::var("SENDER_ENDPOINT")
                .unwrap_or_else(|_| "http://ewr-sender.helius-rpc.com/fast".to_string()),
            rpc_fallback_url: rpc_url.clone(),
            jito_auth_uuid: env::var("JITO_AUTH_UUID")
                .ok()
                .filter(|value| !value.trim().is_empty()),
        };

        let started = std::time::Instant::now();
        let response = executor::execute_pumpfun_sell_command(command, started).await?;
        let signature = response.signature.clone();
        let finality = if let Some(signature) = signature.as_deref() {
            confirm_signature_status(&client, &rpc_url, signature, 5_000, 150).await
        } else {
            SignatureStatusCheck {
                status: "missing_signature".to_string(),
                ok: Some(false),
                confirmation_status: None,
                error: response.error.clone(),
            }
        };
        println!(
            "{}",
            json!({
                "kind": "sellAllPumpfunPositionResult",
                "mint": position.mint,
                "tokenProgramId": position.token_program_id,
                "tokensInRaw": position.amount_raw,
                "expectedSolOutLamports": expected,
                "expectedSolOutSol": expected as f64 / LAMPORTS_PER_SOL,
                "ok": response.ok,
                "signature": signature,
                "senderPath": response.sender_path,
                "route": response.route,
                "elapsedMs": response.elapsed_ms,
                "buildMs": response.build_ms,
                "broadcastMs": response.broadcast_ms,
                "finality": {
                    "status": finality.status,
                    "ok": finality.ok,
                    "confirmationStatus": finality.confirmation_status,
                    "error": finality.error,
                },
                "error": response.error,
            })
        );
        sleep(Duration::from_millis(250)).await;
    }

    let remaining = fetch_nonzero_positions(&client, &rpc_url, &owner.to_string()).await?;
    println!(
        "{}",
        json!({
            "kind": "sellAllPumpfunPositionsFinished",
            "remainingNonzero": remaining.iter().map(|p| json!({
                "mint": p.mint,
                "tokenProgramId": p.token_program_id,
                "amountRaw": p.amount_raw,
                "uiAmount": p.ui_amount,
            })).collect::<Vec<_>>(),
        })
    );
    Ok(())
}

fn load_env_file(path: &str) {
    let Ok(text) = fs::read_to_string(path) else {
        return;
    };
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let Some((key, value)) = trimmed.split_once('=') else {
            continue;
        };
        if env::var(key.trim()).is_ok() {
            continue;
        }
        let mut value = value.trim().to_string();
        if (value.starts_with('"') && value.ends_with('"'))
            || (value.starts_with('\'') && value.ends_with('\''))
        {
            value = value[1..value.len().saturating_sub(1)].to_string();
        }
        env::set_var(key.trim(), value);
    }
}

fn wallet_pubkey() -> Result<Pubkey> {
    let private_key = env::var("PRIVATE_KEY_BASE58").context("PRIVATE_KEY_BASE58 is not set")?;
    let keypair_bytes = bs58::decode(private_key.trim())
        .into_vec()
        .context("PRIVATE_KEY_BASE58 is not valid base58")?;
    let keypair = Keypair::from_bytes(&keypair_bytes)
        .context("PRIVATE_KEY_BASE58 is not a Solana keypair")?;
    Ok(keypair.pubkey())
}

async fn fetch_nonzero_positions(
    client: &reqwest::Client,
    rpc_url: &str,
    owner: &str,
) -> Result<Vec<TokenPosition>> {
    let mut out = Vec::new();
    for program_id in [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID] {
        let json = rpc(
            client,
            rpc_url,
            "getTokenAccountsByOwner",
            json!([
                owner,
                { "programId": program_id },
                { "encoding": "jsonParsed", "commitment": "processed" }
            ]),
        )
        .await?;
        for row in json
            .get("value")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            let Some(info) = row
                .get("account")
                .and_then(|v| v.get("data"))
                .and_then(|v| v.get("parsed"))
                .and_then(|v| v.get("info"))
            else {
                continue;
            };
            let Some(mint) = info.get("mint").and_then(Value::as_str) else {
                continue;
            };
            let amount = info
                .get("tokenAmount")
                .and_then(|v| v.get("amount"))
                .and_then(Value::as_str)
                .and_then(|v| v.parse::<u64>().ok())
                .unwrap_or(0);
            if amount == 0 {
                continue;
            }
            out.push(TokenPosition {
                mint: mint.to_string(),
                token_program_id: program_id.to_string(),
                amount_raw: amount,
                ui_amount: info
                    .get("tokenAmount")
                    .and_then(|v| v.get("uiAmountString"))
                    .and_then(Value::as_str)
                    .map(str::to_string),
            });
        }
    }
    out.sort_by(|a, b| a.mint.cmp(&b.mint));
    Ok(out)
}

async fn fetch_curve_state(
    client: &reqwest::Client,
    rpc_url: &str,
    bonding_curve: &str,
) -> Result<CurveState> {
    let json = rpc(
        client,
        rpc_url,
        "getAccountInfo",
        json!([bonding_curve, { "encoding": "base64", "commitment": "processed" }]),
    )
    .await?;
    let owner_ok = json
        .get("value")
        .and_then(|v| v.get("owner"))
        .and_then(Value::as_str)
        .is_some_and(|owner| owner == PUMP_FUN_PROGRAM_ID);
    if !owner_ok {
        bail!("bonding curve is not owned by Pump.fun");
    }
    let data_base64 = json
        .get("value")
        .and_then(|v| v.get("data"))
        .and_then(Value::as_array)
        .and_then(|arr| arr.first())
        .and_then(Value::as_str)
        .context("missing bonding curve data")?;
    let data = BASE64_STANDARD
        .decode(data_base64)
        .context("bonding curve data is not base64")?;
    if data.len() < 81 {
        bail!("bonding curve data too short");
    }
    let creator_bytes: [u8; 32] = data[49..81].try_into().context("creator slice failed")?;
    Ok(CurveState {
        virtual_token_reserves: read_u64_le(&data, 8).context("missing token reserves")?,
        virtual_sol_reserves: read_u64_le(&data, 16).context("missing sol reserves")?,
        complete: data.get(48).copied().unwrap_or(0) != 0,
        creator: Pubkey::new_from_array(creator_bytes).to_string(),
    })
}

fn quote_sell_lamports(
    virtual_token_reserves: u64,
    virtual_sol_reserves: u64,
    tokens_in_raw: u64,
) -> Option<u64> {
    if tokens_in_raw == 0 || virtual_token_reserves == 0 || virtual_sol_reserves == 0 {
        return None;
    }
    let pre_sol = virtual_sol_reserves as u128;
    let pre_tokens = virtual_token_reserves as u128;
    let post_tokens = pre_tokens.checked_add(tokens_in_raw as u128)?;
    let k = pre_sol.checked_mul(pre_tokens)?;
    let post_sol = k / post_tokens;
    let gross = pre_sol.checked_sub(post_sol)?;
    let fee = gross.checked_mul(100)? / 10_000;
    (gross > fee).then_some((gross - fee).min(u64::MAX as u128) as u64)
}

async fn confirm_signature_status(
    client: &reqwest::Client,
    rpc_url: &str,
    signature: &str,
    timeout_ms: u64,
    poll_ms: u64,
) -> SignatureStatusCheck {
    let started = std::time::Instant::now();
    loop {
        match rpc(
            client,
            rpc_url,
            "getSignatureStatuses",
            json!([[signature], { "searchTransactionHistory": true }]),
        )
        .await
        {
            Ok(json) => {
                let value = json
                    .get("value")
                    .and_then(Value::as_array)
                    .and_then(|arr| arr.first())
                    .cloned()
                    .unwrap_or(Value::Null);
                if !value.is_null() {
                    let confirmation_status = value
                        .get("confirmationStatus")
                        .and_then(Value::as_str)
                        .map(str::to_string);
                    if let Some(err) = value.get("err").filter(|v| !v.is_null()) {
                        return SignatureStatusCheck {
                            status: "finalized_error".to_string(),
                            ok: Some(false),
                            confirmation_status,
                            error: Some(err.to_string()),
                        };
                    }
                    if matches!(
                        confirmation_status.as_deref(),
                        Some("processed" | "confirmed" | "finalized")
                    ) {
                        return SignatureStatusCheck {
                            status: "landed_ok".to_string(),
                            ok: Some(true),
                            confirmation_status,
                            error: None,
                        };
                    }
                }
            }
            Err(err) => {
                return SignatureStatusCheck {
                    status: "status_rpc_error".to_string(),
                    ok: None,
                    confirmation_status: None,
                    error: Some(err.to_string()),
                }
            }
        }
        if started.elapsed().as_millis() >= timeout_ms as u128 {
            return SignatureStatusCheck {
                status: "status_timeout".to_string(),
                ok: None,
                confirmation_status: None,
                error: None,
            };
        }
        sleep(Duration::from_millis(poll_ms)).await;
    }
}

async fn rpc(
    client: &reqwest::Client,
    rpc_url: &str,
    method: &str,
    params: Value,
) -> Result<Value> {
    let response = client
        .post(rpc_url)
        .timeout(Duration::from_millis(1_500))
        .json(&json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }))
        .send()
        .await
        .with_context(|| format!("{method} rpc send failed"))?;
    let json = response
        .json::<Value>()
        .await
        .with_context(|| format!("{method} rpc parse failed"))?;
    if let Some(error) = json.get("error") {
        bail!("{method} rpc error: {error}");
    }
    Ok(json.get("result").cloned().unwrap_or(Value::Null))
}

fn pumpfun_bonding_curve_pda(mint: &str) -> Result<String> {
    let mint = Pubkey::from_str(mint).context("mint invalid")?;
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID).context("pump program invalid")?;
    let (bonding_curve, _) =
        Pubkey::find_program_address(&[b"bonding-curve", mint.as_ref()], &program_id);
    Ok(bonding_curve.to_string())
}

fn read_u64_le(data: &[u8], offset: usize) -> Option<u64> {
    let bytes = data.get(offset..offset + 8)?;
    let mut out = [0u8; 8];
    out.copy_from_slice(bytes);
    Some(u64::from_le_bytes(out))
}

fn read_rpc_url() -> String {
    if let Ok(url) = env::var("SECURE_RPC_URL") {
        if !url.trim().is_empty() {
            return url;
        }
    }
    if let Ok(key) = env::var("HELIUS_API_KEY") {
        if !key.trim().is_empty() {
            return format!("https://mainnet.helius-rpc.com/?api-key={key}");
        }
    }
    "https://mainnet.helius-rpc.com".to_string()
}

fn parse_env_bool(name: &str, fallback: bool) -> bool {
    match env::var(name) {
        Ok(value) => match value.trim().to_ascii_lowercase().as_str() {
            "true" | "1" | "yes" | "on" => true,
            "false" | "0" | "no" | "off" => false,
            _ => fallback,
        },
        Err(_) => fallback,
    }
}

fn parse_env_u64(name: &str, fallback: u64) -> u64 {
    env::var(name)
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .unwrap_or(fallback)
}

fn parse_env_optional_u64(name: &str) -> Option<u64> {
    env::var(name).ok().and_then(|v| {
        let trimmed = v.trim();
        if trimmed.is_empty() {
            None
        } else {
            trimmed.parse::<u64>().ok()
        }
    })
}

fn configured_jito_regions() -> Vec<String> {
    env::var("JITO_REGIONS")
        .or_else(|_| env::var("JITO_REGION"))
        .unwrap_or_else(|_| "ny".to_string())
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .collect()
}

fn jito_region_base_url(region: &str) -> &'static str {
    match region.trim().to_ascii_lowercase().as_str() {
        "ny" | "new-york" | "ewr" => "https://ny.mainnet.block-engine.jito.wtf",
        "amsterdam" | "ams" => "https://amsterdam.mainnet.block-engine.jito.wtf",
        "frankfurt" | "fra" => "https://frankfurt.mainnet.block-engine.jito.wtf",
        "london" | "lon" => "https://london.mainnet.block-engine.jito.wtf",
        "singapore" | "sg" | "sgp" => "https://singapore.mainnet.block-engine.jito.wtf",
        "tokyo" | "tyo" => "https://tokyo.mainnet.block-engine.jito.wtf",
        "slc" | "salt-lake-city" => "https://slc.mainnet.block-engine.jito.wtf",
        _ => "https://mainnet.block-engine.jito.wtf",
    }
}

fn now_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}
