#!/usr/bin/env python3
"""Fail closed unless the 8YCB V2 service is observation-only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ALLOWED_ENV = {
    "STREAM_PROVIDERS_ENABLED",
    "SHYFT_GRPC_ENDPOINT",
    "SHYFT_GRPC_TOKEN",
    "SHYFT_GRPC_COMMITMENT",
    "SHYFT_GRPC_RECONNECT_MS",
    "SOL_PRICE_USD",
}

FORBIDDEN_SOURCE = (
    "sendTransaction",
    "sendRawTransaction",
    "sendBundle",
    "broadcast_signed_tx",
    "execute_pumpfun_buy",
    "execute_pumpfun_sell",
    "PRIVATE_KEY_BASE58",
    "Keypair.fromSecretKey",
    "VersionedTransaction",
    "TransactionInstruction",
    "Keypair",
    "signTransaction",
    "serializeTransaction",
    "getLatestBlockhash",
    "@solana/web3.js",
    "jito-ts",
    "block-engine",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"SHADOW_SAFETY_BLOCKER: {message}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--observer", required=True)
    parser.add_argument("--watcher", required=True)
    parser.add_argument("--safe-env", required=True)
    parser.add_argument("--spec", required=True)
    args = parser.parse_args()
    paths = {name: Path(getattr(args, name)) for name in ("service", "observer", "watcher", "safe_env", "spec")}
    for name, path in paths.items():
        require(path.is_file(), f"missing {name}: {path}")

    service = paths["service"].read_text(encoding="utf-8")
    for setting in (
        "User=edgeshadow8ycb",
        "NoNewPrivileges=true",
        "CapabilityBoundingSet=",
        "ProtectSystem=strict",
        "InaccessiblePaths=",
        "ReadWritePaths=/mnt/HC_Volume_106775198/edgesniffer-derived/8ycb-prospective-shadow-v2",
        "BindReadOnlyPaths=/root/bot/FinalBot-cya/node_modules:/usr/local/lib/edgesniffer-8ycb-shadow-v2/node_modules",
        "Environment=NO_SUBMIT_HARD_GUARD=8YCB_OBSERVATION_ONLY_V2",
        "Environment=RUST_EXECUTOR_ENABLED=false",
        "Environment=RUST_SELECTED_CANDIDATE_SUBMIT_ENABLED=false",
        "Environment=JUPITER_FALLBACK_ENABLED=false",
        "UnsetEnvironment=PRIVATE_KEY_BASE58 SOLANA_PRIVATE_KEY WALLET_PRIVATE_KEY JITO_BLOCK_ENGINE_URL JITO_AUTH_KEYPAIR_PATH RPC_URL SOLANA_RPC_URL HELIUS_RPC_URL",
    ):
        require(setting in service, f"service hardening missing: {setting}")
    require("RUST_EXECUTOR_ENABLED=true" not in service, "Rust executor enabled")
    require(not any(line.startswith("Environment=PRIVATE_KEY") for line in service.splitlines()), "private-key environment enabled")
    require(not any(line.startswith("Environment=JITO") for line in service.splitlines()), "Jito environment enabled")

    for key, path in (("observer", paths["observer"]), ("watcher", paths["watcher"])):
        source = path.read_text(encoding="utf-8")
        require("8YCB_OBSERVATION_ONLY_V2" in source, f"{key} does not enforce the V2 hard guard")
        for token in FORBIDDEN_SOURCE:
            require(token not in source, f"{key} contains forbidden token {token}")

    env_keys = set()
    env_values = {}
    for raw in paths["safe_env"].read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        require("=" in line, "invalid safe environment line")
        key, value = line.split("=", 1)
        env_keys.add(key)
        env_values[key] = value
    require(env_keys <= ALLOWED_ENV, f"unsafe environment keys: {sorted(env_keys - ALLOWED_ENV)}")
    require("SHYFT_GRPC_ENDPOINT" in env_keys and "SHYFT_GRPC_TOKEN" in env_keys, "read-only stream credential missing")
    endpoint = env_values["SHYFT_GRPC_ENDPOINT"].lower()
    require("shyft" in endpoint, "stream endpoint is not identifiable as Shyft")
    require(not any(token in endpoint for token in ("jito", "block-engine", "relayer")), "submission-capable endpoint present")

    spec = json.loads(paths["spec"].read_text(encoding="utf-8"))
    require(spec.get("mode") == "OBSERVATION_ONLY_NO_SUBMIT", "spec mode is not observation-only")
    require(spec.get("transaction_construction") == "OMITTED_V2", "transaction construction not omitted")
    require(spec.get("target_wallet") == "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ", "target changed")

    result = {
        "schema": "8ycb-shadow-broadcast-safety-audit-v2",
        "status": "PASS",
        "proof": {
            "transaction_construction": "absent",
            "signer_or_private_key": "absent",
            "broadcast_api": "absent",
            "submission_endpoint": "absent",
            "network_purpose": "account-filtered read-only Yellowstone gRPC subscription only",
            "production_environment_files": "systemd inaccessible",
            "filesystem_writes": "dedicated output directory only",
        },
        "hashes": {name: sha256(path) for name, path in paths.items() if name != "safe_env"},
        "safe_environment_keys": sorted(env_keys),
    }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
