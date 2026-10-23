#!/usr/bin/env python3
"""Reproduce the four offline EdgeSniffer audit probes using Python stdlib."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


HERE = Path(__file__).resolve().parent
PROBES = (
    ("governance", "probe_governance.py", "--source-root"),
    ("scorer", "adversarial_scorer_probes.py", None),
    ("native", "probe_native.py", "--source-root"),
    ("economics", "probe_economics.py", "--source"),
)
PRESERVED_RESEARCH = (
    "research/2cq-event-driven-fingerprint-freeze-v1/frozen-scorer.json",
    "research/2cq-event-driven-fingerprint-freeze-v1/report.json",
)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="Extracted audited source directory containing scripts/ and research/.")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Directory for new results and logs, outside the source and probe bundle.")
    args = parser.parse_args()
    source = args.source_root.resolve()
    output = args.output_dir.resolve()
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required by the supplied source modules.")
    if not (source / "scripts").is_dir():
        parser.error("--source-root must contain scripts/; pass the extracted source root.")
    missing = [name for name in PRESERVED_RESEARCH if not (source / name).is_file()]
    if missing:
        parser.error("Required preserved research files are missing: " + ", ".join(missing))
    if output == source or source in output.parents:
        parser.error("--output-dir must be outside --source-root to preserve the supplied source.")
    if output == HERE or HERE in output.parents:
        parser.error("--output-dir must be outside the probe bundle to preserve its references.")
    output.mkdir(parents=True, exist_ok=True)

    # Record only source .py files and the two explicitly required research JSON
    # files. No runtime configuration, credentials, account files, or raw tapes
    # are sought. This inventory is provenance, not a security sandbox.
    source_files = sorted((source / "scripts").rglob("*.py"))
    source_files += [source / name for name in PRESERVED_RESEARCH]
    inventory = {path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in source_files}
    write_json(output / "source_file_sha256.json", inventory)

    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    summary = {
        "purpose": "Synthetic counterexamples and preserved-report recalculations; not market PnL validation.",
        "success_means": "The audited implementation behavior reproduced and all four JSON outputs matched the audit references.",
        "python_version": platform.python_version(),
        "source_root": str(source),
        "output_dir": str(output),
        "source_inventory": "source_file_sha256.json",
        "probes": [],
    }
    for name, filename, source_flag in PROBES:
        directory = output / name
        directory.mkdir(exist_ok=True)
        result_path = directory / "probe_results.json"
        if result_path.exists():
            result_path.unlink()
        command = [sys.executable, "-B", str(HERE / "probes" / filename)]
        if source_flag is not None:
            command.append(source_flag)
        command += [str(source), "--output", str(result_path)]
        print(f"Running {name} ...", flush=True)
        with (directory / "stdout.txt").open("w", encoding="utf-8") as stdout, \
             (directory / "stderr.txt").open("w", encoding="utf-8") as stderr:
            completed = subprocess.run(command, cwd=directory, env=environment,
                                       stdout=stdout, stderr=stderr, check=False)
        item = {"name": name, "probe": "probes/" + filename,
                "return_code": completed.returncode,
                "result": name + "/probe_results.json",
                "stdout": name + "/stdout.txt", "stderr": name + "/stderr.txt",
                "reference": "reference_outputs/" + name + ".json",
                "matches_reference": False}
        if completed.returncode == 0 and result_path.is_file():
            try:
                actual = json.loads(result_path.read_text(encoding="utf-8"))
                expected = json.loads((HERE / "reference_outputs" / (name + ".json")).read_text(encoding="utf-8"))
                item["matches_reference"] = actual == expected
                item["result_groups"] = len(actual)
                if not item["matches_reference"]:
                    item["changed_result_groups"] = sorted(
                        key for key in set(actual) | set(expected)
                        if key not in actual or key not in expected or actual[key] != expected[key])
            except (OSError, ValueError, TypeError) as exc:
                item["comparison_error"] = str(exc)
        elif completed.returncode == 0:
            item["comparison_error"] = "Probe exited without writing its expected JSON result."
        summary["probes"].append(item)
        if item["matches_reference"]:
            print(f"  Reproduced {item['result_groups']} result groups; JSON matches audit reference.", flush=True)
        else:
            print(f"  Did not reproduce reference; see {directory / 'stderr.txt'} and summary.json.", flush=True)

    summary["all_reproduced"] = all(item["return_code"] == 0 and item["matches_reference"]
                                    for item in summary["probes"])
    summary["total_result_groups"] = sum(item.get("result_groups", 0) for item in summary["probes"])
    write_json(output / "summary.json", summary)
    print(f"Summary: {output / 'summary.json'}", flush=True)
    return 0 if summary["all_reproduced"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
