//! One-shot fail-closed terminalizer for a forward capture that died before
//! the canary's normal interruption path could persist its terminal state.

use anyhow::{bail, Result};
use pump_exact_evidence_ledger::{
    canonical_capture_run_v1::{CaptureLifecycle, CaptureRunJournal},
    forward_capture_session_v1::ForwardCaptureSession,
};
use std::{env, fs, path::PathBuf};

fn terminal(lifecycle: &CaptureLifecycle) -> bool {
    matches!(
        lifecycle,
        CaptureLifecycle::VerifiedDiagnostic
            | CaptureLifecycle::Inconclusive
            | CaptureLifecycle::Quarantined
            | CaptureLifecycle::Voided
    )
}

fn main() -> Result<()> {
    let root = PathBuf::from(env::var("FORWARD_CAPTURE_ROOT")?);
    let reason = env::var("FORWARD_CAPTURE_VOID_REASON")?;
    let mut session = ForwardCaptureSession::read(&root)?;
    if session.state != "OPEN" {
        bail!("FORWARD_CAPTURE_SESSION_NOT_OPEN")
    }
    let mut segment_roots = fs::read_dir(root.join("segments"))?
        .filter_map(|entry| entry.ok())
        .filter_map(|entry| entry.file_type().ok().filter(|kind| kind.is_dir()).map(|_| entry.path()))
        .collect::<Vec<_>>();
    segment_roots.sort();
    let active = segment_roots.last().ok_or_else(|| anyhow::anyhow!("FORWARD_CAPTURE_NO_SEGMENTS"))?;
    let mut run = CaptureRunJournal::load(active)?;
    if !terminal(&run.checkpoint.lifecycle) {
        run.terminal(CaptureLifecycle::Voided, &reason)?;
    }
    session.void()?;
    session.write_atomic(&root)?;
    println!("VOIDED:{}", root.display());
    Ok(())
}
