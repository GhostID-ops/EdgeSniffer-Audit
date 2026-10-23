#[path = "../executor.rs"]
mod executor;
#[path = "../exit_watcher.rs"]
mod exit_watcher;

use anyhow::{bail, Context, Result};
use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine;
use executor::{ExecutePumpfunSellCommand, SourcePumpSwapAccountMeta};
use reqwest::Client;
use serde_json::{json, Value};
use solana_pubkey::Pubkey;
use std::env;
use std::time::{Duration, Instant};

const TOKEN_2022_PROGRAM_ID: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";

#[tokio::main]
async fn main() -> Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let mint = env::var("MANUAL_SELL_MINT").context("MANUAL_SELL_MINT is required")?;
    let bonding_curve =
        env::var("MANUAL_SELL_BONDING_CURVE").context("MANUAL_SELL_BONDING_CURVE is required")?;
    let tokens_in_raw = env::var("MANUAL_SELL_TOKENS_IN_RAW")
        .context("MANUAL_SELL_TOKENS_IN_RAW is required")?
        .parse::<u64>()
        .context("MANUAL_SELL_TOKENS_IN_RAW must be u64")?;
    let rpc_url = read_rpc_url()?;
    let source_ix_accounts = env_csv_any(&["MANUAL_SELL_SOURCE_IX_ACCOUNTS"]);
    let (sell_v2, accounts) = if source_ix_accounts.is_empty() {
        let data = fetch_account_data_base64(&rpc_url, &bonding_curve).await?;
        let creator = decode_creator(&data).context("curve account missing creator")?;
        let template = executor::build_pumpfun_exit_sell_template(
            &mint,
            &bonding_curve,
            &creator,
            Some(TOKEN_2022_PROGRAM_ID),
        )?;
        if !template.0 {
            bail!("manual emergency close expected Pump.fun v2/token2022 template");
        }
        template
    } else {
        executor::build_pumpfun_exit_sell_template_from_source_accounts(
            &mint,
            &source_ix_accounts,
            Some(TOKEN_2022_PROGRAM_ID),
        )?
    };
    let blockhash = executor::fetch_latest_blockhash(&rpc_url).await?;
    let command = ExecutePumpfunSellCommand {
        id: format!("manual-emergency-close-{}-{}", mint, unix_ms()),
        route: "pumpfun-direct-sell-v2-manual-emergency".to_string(),
        mint: mint.clone(),
        tokens_in_raw,
        expected_sol_out_lamports: 1,
        min_sol_out_lamports: env::var("MANUAL_SELL_MIN_SOL_OUT_LAMPORTS")
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(1),
        sell_v2,
        accounts: accounts
            .into_iter()
            .map(|account| SourcePumpSwapAccountMeta {
                pubkey: account.pubkey,
                is_signer: account.is_signer,
                is_writable: account.is_writable,
            })
            .collect(),
        blockhash,
        compute_unit_limit: env_u32("COMPUTE_UNIT_LIMIT", 160_000),
        priority_fee_micro_lamports: env_u64_any(
            &[
                "RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS",
                "PRIORITY_FEE_MICRO_LAMPORTS",
            ],
            0,
        ),
        jito_tip_lamports: env_u64_any(
            &["RUST_HOT_SNIPER_JITO_TIP_LAMPORTS", "JITO_TIP_LAMPORTS"],
            0,
        ),
        helius_tip_lamports: env_u64_any(
            &["RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS", "HELIUS_TIP_LAMPORTS"],
            0,
        ),
        use_jito_direct: env_bool_any(
            &["RUST_HOT_SNIPER_USE_JITO_DIRECT", "USE_JITO_DIRECT"],
            true,
        ),
        use_jito_fallback: env_bool_any(
            &["RUST_HOT_SNIPER_USE_JITO_FALLBACK", "USE_JITO_FALLBACK"],
            true,
        ),
        sender_rpc_fallback_enabled: env_bool_any(
            &[
                "RUST_HOT_SNIPER_SENDER_RPC_FALLBACK_ENABLED",
                "SENDER_RPC_FALLBACK_ENABLED",
            ],
            true,
        ),
        execution_multi_send_enabled: env_bool_any(
            &[
                "RUST_HOT_SNIPER_EXECUTION_MULTI_SEND_ENABLED",
                "EXECUTION_MULTI_SEND_ENABLED",
            ],
            false,
        ),
        jito_send_mode: env_string_any(
            &["RUST_HOT_SNIPER_JITO_SEND_MODE", "JITO_SEND_MODE"],
            "transaction",
        ),
        jito_transaction_urls: env_csv_any(&[
            "RUST_HOT_SNIPER_JITO_TRANSACTION_URLS",
            "JITO_TRANSACTION_URLS",
        ]),
        jito_bundle_urls: env_csv_any(&["RUST_HOT_SNIPER_JITO_BUNDLE_URLS", "JITO_BUNDLE_URLS"]),
        sender_endpoint_url: env_string_any(
            &["RUST_HOT_SNIPER_SENDER_ENDPOINT_URL", "SENDER_ENDPOINT_URL"],
            "",
        ),
        rpc_fallback_url: rpc_url.clone(),
        jito_auth_uuid: env_string_optional_any(&[
            "RUST_HOT_SNIPER_JITO_AUTH_UUID",
            "JITO_AUTH_UUID",
        ]),
    };
    let started = Instant::now();
    let response = executor::execute_pumpfun_sell_command(command, started).await?;
    println!("{}", serde_json::to_string(&response)?);
    if !response.ok {
        bail!("manual sell broadcast failed: {:?}", response.error);
    }
    if let Some(signature) = response.signature.as_deref() {
        let finality = wait_signature(&rpc_url, signature, 6_000).await?;
        println!("{}", serde_json::to_string(&finality)?);
        if !finality.get("ok").and_then(Value::as_bool).unwrap_or(false) {
            bail!("manual sell did not finalize ok");
        }
    }
    Ok(())
}

fn decode_creator(data: &[u8]) -> Option<String> {
    let bytes: [u8; 32] = data.get(49..81)?.try_into().ok()?;
    Some(Pubkey::new_from_array(bytes).to_string())
}

async fn fetch_account_data_base64(rpc_url: &str, pubkey: &str) -> Result<Vec<u8>> {
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [pubkey, { "encoding": "base64", "commitment": "processed" }]
    });
    let response: Value = Client::new()
        .post(rpc_url)
        .json(&body)
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    let encoded = response
        .get("result")
        .and_then(|v| v.get("value"))
        .and_then(|v| v.get("data"))
        .and_then(Value::as_array)
        .and_then(|items| items.first())
        .and_then(Value::as_str)
        .context("getAccountInfo returned no base64 account data")?;
    Ok(BASE64_STANDARD.decode(encoded)?)
}

async fn wait_signature(rpc_url: &str, signature: &str, timeout_ms: u64) -> Result<Value> {
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    while Instant::now() < deadline {
        let body = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [[signature], { "searchTransactionHistory": true }]
        });
        let response: Value = Client::new()
            .post(rpc_url)
            .json(&body)
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        if let Some(status) = response
            .get("result")
            .and_then(|v| v.get("value"))
            .and_then(Value::as_array)
            .and_then(|items| items.first())
        {
            if !status.is_null() {
                let err = status.get("err").cloned().unwrap_or(Value::Null);
                return Ok(json!({
                    "kind": "manualSellFinality",
                    "signature": signature,
                    "ok": err.is_null(),
                    "confirmationStatus": status.get("confirmationStatus").cloned().unwrap_or(Value::Null),
                    "slot": status.get("slot").cloned().unwrap_or(Value::Null),
                    "error": err,
                }));
            }
        }
        tokio::time::sleep(Duration::from_millis(150)).await;
    }
    Ok(
        json!({ "kind": "manualSellFinality", "signature": signature, "ok": false, "error": "status_timeout" }),
    )
}

fn read_rpc_url() -> Result<String> {
    env_string_optional_any(&[
        "SECURE_RPC_URL",
        "RPC_URL",
        "HELIUS_RPC_URL",
        "SOLANA_RPC_URL",
        "EDGE_LIVE_CONFIRM_RPC_URLS",
    ])
    .map(|value| value.split(',').next().unwrap_or("").trim().to_string())
    .filter(|value| !value.is_empty())
    .context("no RPC URL configured")
}

fn env_u32(name: &str, fallback: u32) -> u32 {
    env::var(name)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(fallback)
}

fn env_u64_any(names: &[&str], fallback: u64) -> u64 {
    names
        .iter()
        .find_map(|name| env::var(name).ok().and_then(|value| value.parse().ok()))
        .unwrap_or(fallback)
}

fn env_bool_any(names: &[&str], fallback: bool) -> bool {
    names
        .iter()
        .find_map(|name| env::var(name).ok())
        .map(|value| {
            matches!(
                value.to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(fallback)
}

fn env_string_any(names: &[&str], fallback: &str) -> String {
    env_string_optional_any(names).unwrap_or_else(|| fallback.to_string())
}

fn env_string_optional_any(names: &[&str]) -> Option<String> {
    names
        .iter()
        .find_map(|name| env::var(name).ok())
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn env_csv_any(names: &[&str]) -> Vec<String> {
    env_string_optional_any(names)
        .map(|value| {
            value
                .split(',')
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn unix_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}
