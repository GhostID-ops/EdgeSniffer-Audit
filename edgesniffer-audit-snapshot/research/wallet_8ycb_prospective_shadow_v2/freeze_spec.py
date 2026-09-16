#!/usr/bin/env python3
"""Render the immutable cohort spec exactly once at the prospective boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True)
    parser.add_argument("--recurrent-source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start-delay-seconds", type=int, default=60)
    args = parser.parse_args()
    template = Path(args.template)
    recurrent = Path(args.recurrent_source)
    output = Path(args.output)
    if output.exists():
        raise SystemExit(f"refusing to overwrite frozen spec: {output}")
    recurrent_sha = hashlib.sha256(recurrent.read_bytes()).hexdigest()
    start = (datetime.now(timezone.utc) + timedelta(seconds=max(0, args.start_delay_seconds))).isoformat(timespec="microseconds").replace("+00:00", "Z")
    raw = template.read_text(encoding="utf-8")
    raw = raw.replace("__COHORT_START_UTC__", start).replace("__RECURRENT_SOURCE_SHA256__", recurrent_sha)
    value = json.loads(raw)
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"cohort_start_utc": start, "frozen_spec_sha256": hashlib.sha256(canonical).hexdigest()}))


if __name__ == "__main__":
    main()
