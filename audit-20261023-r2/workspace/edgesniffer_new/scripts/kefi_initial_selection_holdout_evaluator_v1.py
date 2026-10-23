#!/usr/bin/env python3
"""One-shot evaluator for the frozen kEfi 40/20 selection split.

Writes a durable OPENED marker before reading a single holdout snapshot.  A
marker without a result is intentionally fail-closed and cannot be retried.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
from pathlib import Path
from statistics import median


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, sort_keys=True, separators=(",", ":")); f.write("\n"); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("selection", path)
    if not spec or not spec.loader: raise RuntimeError("FAIL_CLOSED: cannot load frozen analysis tool")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def rank(values: list[float], chosen: int) -> tuple[int, float]:
    value = values[chosen]; return 1 + sum(x > value for x in values), sum(x <= value for x in values) / len(values)


def metrics(rows: list[dict]) -> dict:
    n = len(rows)
    return {"n":n, "top1":sum(r["rank"] <= 1 for r in rows)/n, "top3":sum(r["rank"] <= 3 for r in rows)/n,
            "top5":sum(r["rank"] <= 5 for r in rows)/n, "top10":sum(r["rank"] <= 10 for r in rows)/n,
            "mrr":sum(1/r["rank"] for r in rows)/n, "median_chosen_percentile":median(r["percentile"] for r in rows),
            "candidate_count_min":min(r["candidate_count"] for r in rows), "candidate_count_median":median(r["candidate_count"] for r in rows), "candidate_count_max":max(r["candidate_count"] for r in rows)}


def bootstrap(rows_a: list[dict], rows_b: list[dict], seed: int, draws: int) -> dict:
    rng = random.Random(seed); n = len(rows_a); measures = ("top1", "top3", "top5", "top10", "mrr")
    values = {name: [] for name in measures}
    for _ in range(draws):
        indices = [rng.randrange(n) for _ in range(n)]
        a = metrics([rows_a[i] for i in indices]); b = metrics([rows_b[i] for i in indices])
        for name in measures: values[name].append(b[name] - a[name])
    return {name:{"p2_5":sorted(items)[int(.025*(draws-1))], "p97_5":sorted(items)[int(.975*(draws-1))]} for name, items in values.items()}


def make_rows(selection, split: dict, frozen: dict, expanded: dict, universe: str) -> tuple[list[dict], list[dict]]:
    seen: set[str] = set(); baseline_rows: list[dict] = []; expanded_rows: list[dict] = []
    entries = split["development_snapshots"] + split["unopened_selection_holdout_snapshots"]
    for ordinal, entry in enumerate(entries):
        # This invokes receipt/hash validation before parsing the payload.
        selection.verify_entry(entry)
        snap = json.loads(Path(entry["snapshot_path"]).read_text())
        candidates = snap["candidates"]
        if universe == "NEW_TO_OBSERVED_WALLET": candidates = [c for c in candidates if str(c.get("mint")) not in seen]
        chosen = next((i for i, c in enumerate(candidates) if c.get("mint") == snap["chosen_mint"] and c.get("pool") == snap["chosen_pool"]), None)
        if chosen is None: raise RuntimeError("FAIL_CLOSED: chosen absent from frozen candidate universe")
        vectors = [selection.feature_vector(c, int(snap["teacher_received_at_unix_ns"])) for c in candidates]
        base_scores = [selection.score(v, frozen) for v in vectors]
        expanded_scores = [float(expanded["intercept"]) + sum(float(expanded["coefficients"][name]) * ((v[name]-float(mean))/float(scale)) for name, mean, scale in zip(expanded["feature_names"], expanded["standardization_means"], expanded["standardization_scales"])) for v in vectors]
        if ordinal >= 40:
            br, bp = rank(base_scores, chosen); er, ep = rank(expanded_scores, chosen)
            common = {"decision_ordinal":ordinal + 1, "candidate_count":len(candidates)}
            baseline_rows.append({**common,"rank":br,"percentile":bp}); expanded_rows.append({**common,"rank":er,"percentile":ep})
        seen.add(str(snap["chosen_mint"]))
    return baseline_rows, expanded_rows


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--plan",type=Path,required=True); p.add_argument("--split",type=Path,required=True); p.add_argument("--fingerprint",type=Path,required=True); p.add_argument("--expanded",type=Path,required=True); p.add_argument("--analysis-tool",type=Path,required=True); p.add_argument("--opened-marker",type=Path,required=True); p.add_argument("--output",type=Path,required=True)
    a=p.parse_args(); plan=json.loads(a.plan.read_text())
    for key,path in (("split_sha256",a.split),("fingerprint_sha256",a.fingerprint),("expanded_model_sha256",a.expanded),("analysis_tool_sha256",a.analysis_tool),("evaluator_sha256",Path(__file__))):
        if plan.get(key) != sha256(path): raise SystemExit(f"FAIL_CLOSED: plan binding mismatch for {key}")
    if a.opened_marker.exists() or a.output.exists(): raise SystemExit("FAIL_CLOSED: holdout already opened or evaluated")
    split=json.loads(a.split.read_text())
    if split.get("holdout_opened") is not False or len(split.get("unopened_selection_holdout_snapshots",[])) != 20: raise SystemExit("FAIL_CLOSED: invalid unopened holdout")
    # The marker is deliberately published before any holdout payload is parsed.
    atomic(a.opened_marker,{"schema":"KEFI_INITIAL_SELECTION_HOLDOUT_OPENED_V1","plan_sha256":sha256(a.plan),"split_sha256":sha256(a.split),"holdout_decisions":20,"opened":True,"outcomes_or_pnl_accessed":False})
    selection=load_module(a.analysis_tool); frozen=json.loads(a.fingerprint.read_text()); expanded=json.loads(a.expanded.read_text())
    results={}
    for universe in ("NEW_TO_OBSERVED_WALLET","ALL_ELIGIBLE_POOLS"):
        baseline, model=make_rows(selection,split,frozen,expanded,universe)
        if len(baseline)!=20 or len(model)!=20: raise RuntimeError("FAIL_CLOSED: holdout count mismatch")
        b,m=metrics(baseline),metrics(model)
        results[universe]={"baseline":b,"expanded":m,"absolute_improvement":{name:m[name]-b[name] for name in ("top1","top3","top5","top10","mrr")},"rank_change":{"improved":sum(x["rank"]<y["rank"] for x,y in zip(model,baseline)),"worsened":sum(x["rank"]>y["rank"] for x,y in zip(model,baseline)),"unchanged":sum(x["rank"]==y["rank"] for x,y in zip(model,baseline))},"clustered_decision_bootstrap_delta_expanded_minus_baseline":bootstrap(baseline,model,int(plan["bootstrap_seed"]),int(plan["bootstrap_draws"])),"per_decision":{"baseline":baseline,"expanded":model}}
    atomic(a.output,{"schema":"KEFI_INITIAL_SELECTION_HOLDOUT_EVALUATION_V1","plan_sha256":sha256(a.plan),"split_sha256":sha256(a.split),"holdout_decisions":20,"outcomes_or_pnl_accessed":False,"results":results})
    print(sha256(a.output))
    return 0

if __name__=="__main__": raise SystemExit(main())
