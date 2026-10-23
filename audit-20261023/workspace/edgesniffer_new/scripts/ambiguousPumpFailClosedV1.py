#!/usr/bin/env python3
"""Exact fail-closed resolver for unresolved Pump-family evidence."""
from __future__ import annotations

from typing import Any


ARM_ID = "ARM13_AMBIGUOUS_PUMP_FAIL_CLOSED"


def resolve(raw_evidence: dict[str, Any]) -> dict[str, Any]:
    """Preserve unresolved evidence without creating an economic entry."""
    if not isinstance(raw_evidence, dict):
        raise TypeError("raw_evidence must be an object")
    candidate_ids = raw_evidence.get("candidate_arm_ids") or []
    observed_programs = raw_evidence.get("observed_program_ids") or []
    if raw_evidence.get("resolved_venue") or raw_evidence.get("canonical_venue"):
        return {
            "arm_id": ARM_ID,
            "status": "REJECTED_CANONICAL_VENUE_PRESENT",
            "economic_entry": False,
            "reason": "ARM13 cannot absorb evidence with a resolved venue",
        }
    return {
        "arm_id": ARM_ID,
        "status": "UNRESOLVED_FAIL_CLOSED",
        "economic_entry": False,
        "reason": "no canonical venue identity admitted",
        "candidate_arm_ids": sorted(str(value) for value in candidate_ids),
        "observed_program_ids": sorted(str(value) for value in observed_programs),
    }
