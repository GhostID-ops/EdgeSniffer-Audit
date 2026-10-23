//! Offline-only inspector for one retained decoder fixture.
//! It deliberately does not open a network connection or any scientific lane.

use anyhow::{Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use prost::Message;
use serde_json::Value;
use std::{env, fs};
use yellowstone_grpc_proto::geyser::{subscribe_update::UpdateOneof, SubscribeUpdate};

const BUY_EVENT_DISC: &[u8] = &[103, 244, 82, 31, 44, 245, 119, 119];
const SELL_EVENT_DISC: &[u8] = &[62, 47, 55, 10, 165, 3, 220, 42];

fn main() -> Result<()> {
    let path = env::args().nth(1).context("usage: kefi_pumpswap_fixture_inspect_v1 <fixture.json>")?;
    let fixture: Value = serde_json::from_slice(&fs::read(&path)?)?;
    let raw = BASE64_STANDARD.decode(fixture["protobuf_update_base64"].as_str().context("missing protobuf_update_base64")?)?;
    let update = SubscribeUpdate::decode(raw.as_slice())?;
    let Some(UpdateOneof::Transaction(tx)) = update.update_oneof else { anyhow::bail!("fixture is not a transaction update") };
    let info = tx.transaction.context("missing transaction")?;
    let meta = info.meta.context("missing meta")?;
    for (log_index, log) in meta.log_messages.iter().enumerate() {
        let Some(encoded) = log.strip_prefix("Program data: ") else { continue; };
        let payload = BASE64_STANDARD.decode(encoded.trim())?;
        let side = if payload.starts_with(BUY_EVENT_DISC) { "BUY" } else if payload.starts_with(SELL_EVENT_DISC) { "SELL" } else { continue; };
        if payload.len() < 184 { continue; }
        println!("{}", serde_json::json!({
            "fixture": path,
            "slot": tx.slot,
            "log_index": log_index,
            "side": side,
            "pool": bs58::encode(&payload[120..152]).into_string(),
            "user": bs58::encode(&payload[152..184]).into_string(),
            "event_payload_sha256": hex_sha256(&payload),
        }));
    }
    Ok(())
}

fn hex_sha256(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    format!("{:x}", Sha256::digest(bytes))
}
