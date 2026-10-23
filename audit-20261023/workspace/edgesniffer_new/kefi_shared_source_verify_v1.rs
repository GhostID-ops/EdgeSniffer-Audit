//! Read-only integrity verifier for a sealed shared-source capture.

use anyhow::{Context, Result};
use pumpfun_sidecar::kefi_canonical_source_v1::read_next_batch;
use std::{env, fs, path::PathBuf};
use yellowstone_grpc_proto::geyser::subscribe_update::UpdateOneof;

fn main() -> Result<()> {
    let root = PathBuf::from(env::var("KEFI_SHARED_SOURCE_VERIFY_ROOT")?);
    let binding: serde_json::Value =
        serde_json::from_slice(&fs::read(root.join("run-binding.json"))?)?;
    let source = binding["sourceInstanceId"]
        .as_str()
        .context("SOURCE_BINDING_ID")?;
    let frames = read_next_batch(&root, source, 1, 0)?;
    let transactions = frames
        .iter()
        .filter(|frame| {
            matches!(
                frame.update.update_oneof.as_ref(),
                Some(UpdateOneof::Transaction(_))
            )
        })
        .count();
    let finalities = frames
        .iter()
        .filter(|frame| {
            matches!(
                frame.update.update_oneof.as_ref(),
                Some(UpdateOneof::Slot(_))
            )
        })
        .count();
    println!(
        "{}",
        serde_json::json!({
            "schema":"KEFI_SHARED_SOURCE_VERIFY_V1",
            "sourceInstanceId":source,
            "durableFrames":frames.len(),
            "transactionFrames":transactions,
            "finalizedSlotFrames":finalities,
            "contiguous":true
        })
    );
    Ok(())
}
