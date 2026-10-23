#![recursion_limit = "256"]
// Work around a current Rust 1.95 diagnostic-rendering ICE in WSL while
// building this workspace.  It changes no runtime behavior.
#![allow(dead_code)]

pub mod decision_input_producer;
pub mod exit_watcher;
#[path = "../../kefi_canonical_source_v1.rs"]
pub mod kefi_canonical_source_v1;
pub mod kefi_frozen_selection_shadow_v1;
#[path = "../../kefi_pump_curve_semantics_v1.rs"]
pub mod kefi_pump_curve_semantics_v1;
#[path = "../../kefi_pumpswap_semantics_v1.rs"]
pub mod kefi_pumpswap_semantics_v1;
#[path = "../../kefi_frozen_pump_curve_transition_tape_v1.rs"]
pub mod kefi_frozen_pump_curve_transition_tape_v1;
pub mod ownership_lifecycle;
pub mod parity_capsule;
pub mod parity_core;
pub mod source_binding;
pub mod teacher_impulse_emitter;
