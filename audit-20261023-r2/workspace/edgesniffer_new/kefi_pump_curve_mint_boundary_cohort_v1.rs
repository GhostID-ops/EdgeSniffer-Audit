// Materialize an explicitly cross-venue Pump curve cohort from sealed
// selection snapshots.
//
// The input snapshots remain authoritative and are never edited.  Each
// output snapshot preserves the original PumpSwap selection fields and adds
// the deterministic Pump bonding-curve PDA derived from chosen_mint.  This
// is a mint-boundary experiment, not evidence that the teacher traded Pump.

use anyhow::{bail, Context, Result};
use pumpfun_sidecar::kefi_frozen_pump_curve_transition_tape_v1::pump_curve_for_mint;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::env;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};

const OUTPUT_SCHEMA: &str = "KEFI_FROZEN_PUMP_CURVE_MINT_BOUNDARY_SNAPSHOT_V1";
const MANIFEST_SCHEMA: &str = "KEFI_FROZEN_PUMP_CURVE_MINT_BOUNDARY_COHORT_MANIFEST_V1";

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn env_path(name: &str) -> Result<PathBuf> {
    env::var_os(name)
        .map(PathBuf::from)
        .with_context(|| format!("missing environment variable {name}"))
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let tmp = path.with_extension("json.tmp");
    let mut file = File::create(&tmp).with_context(|| format!("create {}", tmp.display()))?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    fs::rename(&tmp, path).with_context(|| format!("rename {}", path.display()))?;
    Ok(())
}

fn main() -> Result<()> {
    let input_root = env_path("KEFI_PUMP_CURVE_MINT_COHORT_INPUT_ROOT")?;
    let output_root = env_path("KEFI_PUMP_CURVE_MINT_COHORT_OUTPUT_ROOT")?;
    let input_snapshots = input_root.join("snapshots");
    let output_snapshots = output_root.join("snapshots");

    if !input_snapshots.is_dir() {
        bail!("FAIL_CLOSED: input snapshots directory missing: {}", input_snapshots.display());
    }
    if output_root.exists() {
        bail!("FAIL_CLOSED: output root already exists: {}", output_root.display());
    }

    let mut inputs: Vec<PathBuf> = fs::read_dir(&input_snapshots)?
        .filter_map(|entry| entry.ok().map(|e| e.path()))
        .filter(|path| path.extension().and_then(|x| x.to_str()) == Some("json"))
        .collect();
    inputs.sort();
    if inputs.is_empty() {
        bail!("FAIL_CLOSED: no sealed snapshots");
    }

    fs::create_dir_all(&output_snapshots)?;
    let mut output_rows = Vec::with_capacity(inputs.len());
    for input_path in &inputs {
        let raw = fs::read(input_path).with_context(|| format!("read {}", input_path.display()))?;
        let mut value: Value = serde_json::from_slice(&raw)
            .with_context(|| format!("parse {}", input_path.display()))?;
        let object = value
            .as_object_mut()
            .context("snapshot must be a JSON object")?;
        let mint = object
            .get("chosen_mint")
            .or_else(|| object.get("chosenMint"))
            .and_then(Value::as_str)
            .context("snapshot missing chosen_mint")?
            .to_owned();
        let original_pool = object
            .get("chosen_pool")
            .or_else(|| object.get("chosenPool"))
            .and_then(Value::as_str)
            .context("snapshot missing chosen_pool")?
            .to_owned();
        let curve = pump_curve_for_mint(&mint)
            .with_context(|| format!("derive Pump curve for mint {mint}"))?;
        let source_hash = sha256_hex(&raw);

        object.insert("schema".to_owned(), Value::String(OUTPUT_SCHEMA.to_owned()));
        object.insert("selectionVenue".to_owned(), Value::String("PUMPSWAP_POOL".to_owned()));
        object.insert("chosenPoolOriginal".to_owned(), Value::String(original_pool.clone()));
        object.insert("chosenCurve".to_owned(), Value::String(curve.clone()));
        // Keep the evaluator's frozen snake_case contract explicit as well as
        // the public camelCase rendering used by the materialized snapshot.
        object.insert("chosen_curve".to_owned(), Value::String(curve.clone()));
        object.insert("sourceSnapshotSha256".to_owned(), Value::String(source_hash.clone()));
        object.insert(
            "venueJoinPolicy".to_owned(),
            Value::String("MINT_AND_DERIVED_PUMP_CURVE_AFTER_SNAPSHOT_BOUNDARY".to_owned()),
        );
        object.insert("crossVenueMintBoundary".to_owned(), Value::Bool(true));
        object.insert("teacherPumpVenueConfirmed".to_owned(), Value::Bool(false));

        let name = input_path
            .file_name()
            .context("snapshot filename")?
            .to_owned();
        let output_path = output_snapshots.join(name);
        let encoded = serde_json::to_vec(&value)?;
        atomic_write(&output_path, &encoded)?;
        output_rows.push(json!({
            "input": input_path.file_name().unwrap().to_string_lossy(),
            "output": output_path.file_name().unwrap().to_string_lossy(),
            "mint": mint,
            "chosenCurve": curve,
            "sourceSnapshotSha256": source_hash,
        }));
    }

    let manifest = json!({
        "schema": MANIFEST_SCHEMA,
        "inputRoot": input_root,
        "outputRoot": output_root,
        "snapshotCount": output_rows.len(),
        "selectionVenue": "PUMPSWAP_POOL",
        "analysisVenue": "PUMP_BONDING_CURVE",
        "crossVenueMintBoundary": true,
        "teacherPumpVenueConfirmed": false,
        "joinKey": "chosen_mint",
        "boundary": "teacher_received_at_unix_ns",
        "policy": "derive Pump bonding-curve PDA from each sealed chosen_mint and evaluate only Pump rows after the sealed timestamp",
        "snapshots": output_rows,
    });
    atomic_write(
        &output_root.join("cohort-manifest.json"),
        serde_json::to_string_pretty(&manifest)?.as_bytes(),
    )?;
    println!(
        "{{\"status\":\"MATERIALIZED\",\"schema\":\"{}\",\"snapshots\":{}}}",
        OUTPUT_SCHEMA,
        inputs.len()
    );
    Ok(())
}
