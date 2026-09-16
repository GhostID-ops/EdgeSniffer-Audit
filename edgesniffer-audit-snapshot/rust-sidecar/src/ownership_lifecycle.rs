use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BuyConfirmationState {
    NotSent,
    Pending,
    Confirmed,
    Uncertain,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WalletOwnershipState {
    Unknown,
    Owned,
    Flat,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum StrategyPositionState {
    Reserved,
    Managed,
    OwnedInventoryRecovery,
    Closed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExitActionState {
    Idle,
    Pending,
    RetryActive,
    ExplicitlyBlocked,
    Closed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CapacityState {
    Held,
    Free,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OwnershipRecord {
    pub mint: String,
    pub buy_confirmation: BuyConfirmationState,
    pub wallet_ownership: WalletOwnershipState,
    pub strategy_position: StrategyPositionState,
    pub exit_action: ExitActionState,
    pub capacity: CapacityState,
    pub amount_raw: u64,
    pub last_proof_at_ms: Option<u64>,
    pub last_proof_source: Option<String>,
    pub canonical_position_present: bool,
}

impl OwnershipRecord {
    fn reserved(mint: &str) -> Self {
        Self {
            mint: mint.to_string(),
            buy_confirmation: BuyConfirmationState::NotSent,
            wallet_ownership: WalletOwnershipState::Unknown,
            strategy_position: StrategyPositionState::Reserved,
            exit_action: ExitActionState::Idle,
            capacity: CapacityState::Held,
            amount_raw: 0,
            last_proof_at_ms: None,
            last_proof_source: None,
            canonical_position_present: false,
        }
    }

    pub fn economically_open(&self) -> bool {
        self.wallet_ownership == WalletOwnershipState::Owned && self.amount_raw > 0
    }

    pub fn invariant_holds(&self) -> bool {
        if self.economically_open() {
            self.capacity == CapacityState::Held
                && matches!(
                    self.strategy_position,
                    StrategyPositionState::Managed | StrategyPositionState::OwnedInventoryRecovery
                )
        } else {
            true
        }
    }

    pub fn terminal_classification(&self) -> &'static str {
        if self.wallet_ownership == WalletOwnershipState::Flat
            && self.strategy_position == StrategyPositionState::Closed
            && self.capacity == CapacityState::Free
        {
            "CLOSED"
        } else if self.economically_open()
            && self.capacity == CapacityState::Held
            && matches!(
                self.strategy_position,
                StrategyPositionState::Managed | StrategyPositionState::OwnedInventoryRecovery
            )
        {
            "EXPLICITLY_BLOCKED_OPEN_POSITION_WITH_ACTIVE_RECOVERY"
        } else {
            "INVALID_UNMANAGED_STATE"
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct OwnershipLifecycleRegistry {
    records: Arc<Mutex<HashMap<String, OwnershipRecord>>>,
}

impl OwnershipLifecycleRegistry {
    pub fn reserve(&self, mint: &str) -> OwnershipRecord {
        let mut records = self
            .records
            .lock()
            .expect("ownership lifecycle mutex poisoned");
        let record = records
            .entry(mint.to_string())
            .or_insert_with(|| OwnershipRecord::reserved(mint));
        if !record.economically_open() {
            record.strategy_position = StrategyPositionState::Reserved;
            record.capacity = CapacityState::Held;
            record.exit_action = ExitActionState::Idle;
        }
        record.clone()
    }

    pub fn note_buy_submitted(&self, mint: &str) -> OwnershipRecord {
        self.mutate(mint, |record| {
            record.buy_confirmation = BuyConfirmationState::Pending;
            record.capacity = CapacityState::Held;
        })
    }

    pub fn note_buy_confirmation(
        &self,
        mint: &str,
        confirmation: BuyConfirmationState,
    ) -> OwnershipRecord {
        self.mutate(mint, |record| {
            record.buy_confirmation = confirmation;
            if record.economically_open() {
                record.capacity = CapacityState::Held;
                if record.strategy_position == StrategyPositionState::Closed {
                    record.strategy_position = StrategyPositionState::OwnedInventoryRecovery;
                    record.exit_action = ExitActionState::RetryActive;
                    record.canonical_position_present = false;
                }
            }
        })
    }

    pub fn prove_owned(
        &self,
        mint: &str,
        amount_raw: u64,
        observed_at_ms: u64,
        source: &str,
        canonical_position_present: bool,
    ) -> OwnershipRecord {
        assert!(amount_raw > 0, "prove_owned requires nonzero inventory");
        self.mutate(mint, |record| {
            record.wallet_ownership = WalletOwnershipState::Owned;
            record.amount_raw = amount_raw;
            record.last_proof_at_ms = Some(observed_at_ms);
            record.last_proof_source = Some(source.to_string());
            record.capacity = CapacityState::Held;
            record.canonical_position_present = canonical_position_present;
            if canonical_position_present {
                record.strategy_position = StrategyPositionState::Managed;
                if record.exit_action == ExitActionState::Closed {
                    record.exit_action = ExitActionState::Idle;
                }
            } else {
                record.strategy_position = StrategyPositionState::OwnedInventoryRecovery;
                record.exit_action = ExitActionState::RetryActive;
            }
        })
    }

    pub fn mark_canonical_present(&self, mint: &str, present: bool) -> OwnershipRecord {
        self.mutate(mint, |record| {
            record.canonical_position_present = present;
            if record.economically_open() {
                record.strategy_position = if present {
                    StrategyPositionState::Managed
                } else {
                    StrategyPositionState::OwnedInventoryRecovery
                };
                record.exit_action = if !present {
                    ExitActionState::RetryActive
                } else if matches!(
                    record.exit_action,
                    ExitActionState::Closed | ExitActionState::ExplicitlyBlocked
                ) {
                    ExitActionState::Idle
                } else {
                    record.exit_action
                };
                record.capacity = CapacityState::Held;
            }
        })
    }

    pub fn mark_exit_pending(&self, mint: &str) -> OwnershipRecord {
        self.mutate(mint, |record| {
            record.exit_action = ExitActionState::Pending;
            if record.economically_open() {
                record.capacity = CapacityState::Held;
            }
        })
    }

    pub fn mark_exit_rejected(&self, mint: &str) -> OwnershipRecord {
        self.mutate(mint, |record| {
            if record.economically_open() {
                record.exit_action = ExitActionState::RetryActive;
                record.capacity = CapacityState::Held;
                if !record.canonical_position_present {
                    record.strategy_position = StrategyPositionState::OwnedInventoryRecovery;
                }
            }
        })
    }

    pub fn mark_exit_blocked(&self, mint: &str) -> OwnershipRecord {
        self.mutate(mint, |record| {
            if record.economically_open() {
                record.exit_action = ExitActionState::ExplicitlyBlocked;
                record.capacity = CapacityState::Held;
                record.strategy_position = StrategyPositionState::OwnedInventoryRecovery;
            }
        })
    }

    pub fn prove_flat(&self, mint: &str, observed_at_ms: u64, source: &str) -> OwnershipRecord {
        self.mutate(mint, |record| {
            record.wallet_ownership = WalletOwnershipState::Flat;
            record.amount_raw = 0;
            record.last_proof_at_ms = Some(observed_at_ms);
            record.last_proof_source = Some(source.to_string());
            record.strategy_position = StrategyPositionState::Closed;
            record.exit_action = ExitActionState::Closed;
            record.capacity = CapacityState::Free;
            record.canonical_position_present = false;
        })
    }

    pub fn blocks_release(&self, mint: &str) -> bool {
        self.snapshot(mint)
            .map(|record| record.economically_open())
            .unwrap_or(false)
    }

    pub fn snapshot(&self, mint: &str) -> Option<OwnershipRecord> {
        self.records
            .lock()
            .expect("ownership lifecycle mutex poisoned")
            .get(mint)
            .cloned()
    }

    pub fn economically_open_mints(&self) -> Vec<String> {
        let mut mints = self
            .records
            .lock()
            .expect("ownership lifecycle mutex poisoned")
            .values()
            .filter(|record| record.economically_open())
            .map(|record| record.mint.clone())
            .collect::<Vec<_>>();
        mints.sort();
        mints
    }

    fn mutate(&self, mint: &str, f: impl FnOnce(&mut OwnershipRecord)) -> OwnershipRecord {
        let mut records = self
            .records
            .lock()
            .expect("ownership lifecycle mutex poisoned");
        let record = records
            .entry(mint.to_string())
            .or_insert_with(|| OwnershipRecord::reserved(mint));
        f(record);
        debug_assert!(record.invariant_holds());
        record.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Clone, Copy)]
    enum Injection {
        BalanceBeforeAck,
        BalanceBeforeFinality,
        BalanceAfterFinality,
        AckMissing,
        StatusTimeout,
        StatusFailure,
        BuyResultUncertain,
        ExitPendingAtProof,
        StaleTpRejected,
        StaleSlRejected,
        SellBuildFailure,
        SellSubmissionFailure,
        SellFinalityUncertain,
        CanonicalDeleted,
        ProcessRestart,
        OrphanReconcile,
        Token2022,
        LegacyToken,
        MaxHoldLateConfirmation,
        MultipleBalanceUpdates,
    }

    #[test]
    fn failure_injection_matrix_never_abandons_owned_inventory() {
        let injections = [
            Injection::BalanceBeforeAck,
            Injection::BalanceBeforeFinality,
            Injection::BalanceAfterFinality,
            Injection::AckMissing,
            Injection::StatusTimeout,
            Injection::StatusFailure,
            Injection::BuyResultUncertain,
            Injection::ExitPendingAtProof,
            Injection::StaleTpRejected,
            Injection::StaleSlRejected,
            Injection::SellBuildFailure,
            Injection::SellSubmissionFailure,
            Injection::SellFinalityUncertain,
            Injection::CanonicalDeleted,
            Injection::ProcessRestart,
            Injection::OrphanReconcile,
            Injection::Token2022,
            Injection::LegacyToken,
            Injection::MaxHoldLateConfirmation,
            Injection::MultipleBalanceUpdates,
        ];

        for (index, injection) in injections.into_iter().enumerate() {
            let mint = format!("mint-{index}");
            let registry = OwnershipLifecycleRegistry::default();
            registry.reserve(&mint);
            registry.note_buy_submitted(&mint);

            match injection {
                Injection::BalanceAfterFinality => {
                    registry.note_buy_confirmation(&mint, BuyConfirmationState::Confirmed);
                    registry.prove_owned(&mint, 100, 10, "yellowstone", true);
                }
                Injection::CanonicalDeleted
                | Injection::ProcessRestart
                | Injection::OrphanReconcile => {
                    registry.prove_owned(&mint, 100, 10, "wallet-reconcile", false);
                }
                Injection::StatusFailure => {
                    registry.prove_owned(&mint, 100, 10, "yellowstone", true);
                    registry.note_buy_confirmation(&mint, BuyConfirmationState::Failed);
                }
                Injection::AckMissing
                | Injection::StatusTimeout
                | Injection::BuyResultUncertain => {
                    registry.prove_owned(&mint, 100, 10, "yellowstone", true);
                    registry.note_buy_confirmation(&mint, BuyConfirmationState::Uncertain);
                }
                Injection::ExitPendingAtProof | Injection::MultipleBalanceUpdates => {
                    registry.mark_exit_pending(&mint);
                    registry.prove_owned(&mint, 100, 10, "yellowstone", true);
                    registry.prove_owned(&mint, 125, 11, "yellowstone", true);
                }
                Injection::StaleTpRejected | Injection::StaleSlRejected => {
                    registry.prove_owned(&mint, 100, 10, "yellowstone", true);
                    registry.mark_exit_pending(&mint);
                    registry.mark_exit_rejected(&mint);
                }
                Injection::SellBuildFailure
                | Injection::SellSubmissionFailure
                | Injection::SellFinalityUncertain
                | Injection::MaxHoldLateConfirmation => {
                    registry.prove_owned(&mint, 100, 10, "yellowstone", true);
                    registry.mark_exit_pending(&mint);
                    registry.mark_exit_rejected(&mint);
                }
                Injection::BalanceBeforeAck
                | Injection::BalanceBeforeFinality
                | Injection::Token2022
                | Injection::LegacyToken => {
                    registry.prove_owned(&mint, 100, 10, "yellowstone", true);
                }
            }

            let record = registry.snapshot(&mint).unwrap();
            assert!(
                record.invariant_holds(),
                "matrix case {index} violated invariant"
            );
            assert_eq!(
                record.terminal_classification(),
                "EXPLICITLY_BLOCKED_OPEN_POSITION_WITH_ACTIVE_RECOVERY",
                "matrix case {index} abandoned inventory"
            );

            registry.prove_flat(&mint, 20, "wallet-zero-proof");
            assert_eq!(
                registry.snapshot(&mint).unwrap().terminal_classification(),
                "CLOSED",
                "matrix case {index} failed to close after zero proof"
            );
        }
    }

    #[test]
    fn late_negative_confirmation_cannot_override_authoritative_ownership() {
        let registry = OwnershipLifecycleRegistry::default();
        registry.reserve("mint");
        registry.note_buy_submitted("mint");
        registry.prove_owned("mint", 321_914_716_761, 1_000, "yellowstone", true);
        registry.mark_exit_pending("mint");
        let record = registry.note_buy_confirmation("mint", BuyConfirmationState::Failed);

        assert!(record.economically_open());
        assert_eq!(record.capacity, CapacityState::Held);
        assert_eq!(record.strategy_position, StrategyPositionState::Managed);
        assert!(registry.blocks_release("mint"));
    }

    #[test]
    fn missing_canonical_position_becomes_explicit_recovery() {
        let registry = OwnershipLifecycleRegistry::default();
        let record = registry.prove_owned("mint", 50, 1_000, "wallet-reconcile", false);

        assert_eq!(
            record.strategy_position,
            StrategyPositionState::OwnedInventoryRecovery
        );
        assert_eq!(record.exit_action, ExitActionState::RetryActive);
        assert_eq!(record.capacity, CapacityState::Held);
    }

    #[test]
    fn canonical_reassertion_does_not_erase_pending_exit_state() {
        let registry = OwnershipLifecycleRegistry::default();
        registry.prove_owned("mint", 100, 1_000, "yellowstone", true);
        registry.mark_exit_pending("mint");

        let record = registry.mark_canonical_present("mint", true);

        assert_eq!(record.exit_action, ExitActionState::Pending);
        assert_eq!(record.strategy_position, StrategyPositionState::Managed);
        assert_eq!(record.capacity, CapacityState::Held);
    }

    #[test]
    fn authoritative_zero_is_the_only_owned_to_closed_transition() {
        let registry = OwnershipLifecycleRegistry::default();
        registry.prove_owned("mint", 100, 1_000, "yellowstone", true);
        registry.note_buy_confirmation("mint", BuyConfirmationState::Failed);
        registry.mark_exit_rejected("mint");
        assert!(registry.blocks_release("mint"));

        let record = registry.prove_flat("mint", 2_000, "yellowstone-zero");
        assert_eq!(record.terminal_classification(), "CLOSED");
        assert!(!registry.blocks_release("mint"));
    }
}
