#!/usr/bin/env python3
"""Fail-closed cohort admission guard for future kEfi scientific boundaries.

The guard is intentionally small and outcome-blind.  It verifies the epoch
registry first, then checks every candidate timestamp against the frozen
boundary and registry status.  It is designed to be called by a future
materializer/research-table publisher; it does not create a cohort itself.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
from pathlib import Path


def registry_module(path: Path):
    spec = importlib.util.spec_from_file_location("kefi_epoch_registry", path)
    if not spec or not spec.loader:
        raise RuntimeError("FAIL_CLOSED: cannot load epoch registry")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def utc_from_unix_ns(value: int) -> str:
    timestamp = dt.datetime.fromtimestamp(value / 1_000_000_000, tz=dt.timezone.utc)
    return timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def assert_eligible(registry_path: Path, registry_root: Path, boundary_utc: str,
                    received_at_unix_ns: int) -> None:
    if not boundary_utc.endswith("Z"):
        raise RuntimeError("FAIL_CLOSED: boundary must be UTC Z")
    registry_module(registry_path).eligible(
        registry_root, utc_from_unix_ns(received_at_unix_ns), boundary_utc
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry-script", type=Path, required=True)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--received-at-unix-ns", type=int, required=True)
    args = parser.parse_args()
    assert_eligible(args.registry_script, args.registry_root, args.boundary_utc, args.received_at_unix_ns)
    print("ELIGIBLE")


if __name__ == "__main__":
    main()
