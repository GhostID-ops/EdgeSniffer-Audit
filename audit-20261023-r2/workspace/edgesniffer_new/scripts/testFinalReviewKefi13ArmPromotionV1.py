import json
import subprocess
import sys
from pathlib import Path


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_final_review_distinguishes_evaluated_winner_from_global_winner(tmp_path):
    registry = {"arms": [{"id": "ARM07_PUMPSWAP_DIRECT"}] + [{"id": f"ARM{i:02d}"} for i in range(1, 13)]}
    manifest = {"cohort_id": "cohort-test"}
    cohort = {"status": "PASS_CLEAN_FRESH_COHORT"}
    ranking = {
        "min_actionable_n": 50,
        "winner": "ARM07_PUMPSWAP_DIRECT",
        "arms": [
            {
                "arm_id": "ARM07_PUMPSWAP_DIRECT",
                "status": "PASS_GATE",
                "promotion_eligible": True,
                "kill_decision": "PROMOTE_CANDIDATE",
            },
            *[
                {
                    "arm_id": f"ARM{i:02d}",
                    "status": "NOT_EVALUATED_NO_FRESH_COHORT",
                    "promotion_eligible": False,
                    "kill_decision": "DEFER_NO_DATA",
                }
                for i in range(1, 13)
            ],
        ],
    }
    economics = {
        "arm_id": "ARM07_PUMPSWAP_DIRECT",
        "cohort": "FRESH_PROSPECTIVE",
        "outcome_source": "POST_FREEZE",
        "exact_evaluator": True,
        "metrics": {
            "actionable_n": 51,
            "pnl_quote": 0.1,
            "profit_factor": 1.5,
            "controls_outperformed": True,
        },
    }
    source = {"status": "PASS_PROSPECTIVE_SOURCE_GATE", "source": {"transport_clean": True}}
    paths = {}
    for name, value in (("registry", registry), ("manifest", manifest), ("cohort", cohort), ("ranking", ranking), ("economics", economics), ("source", source)):
        path = tmp_path / f"{name}.json"
        write(path, value)
        paths[name] = path
    output = tmp_path / "review.json"
    script = Path(__file__).with_name("finalReviewKefi13ArmPromotionV1.py")
    result = subprocess.run(
        [sys.executable, str(script), "--registry", str(paths["registry"]), "--manifest", str(paths["manifest"]),
         "--cohort-audit", str(paths["cohort"]), "--ranking", str(paths["ranking"]),
         "--economics", str(paths["economics"]), "--source-compatibility", str(paths["source"]), "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    review = json.loads(output.read_text(encoding="utf-8"))
    assert review["evaluated_arm_winner"] == "ARM07_PUMPSWAP_DIRECT"
    assert review["global_winner_claim"] is False
    assert review["decision"] == "PROMOTE_ARM07_TO_QUOTE_LIVE_PARITY_PAPER"
    assert review["live_trading_authorized"] is False


def test_final_review_rejects_blocked_source_gate(tmp_path):
    registry = {"arms": [{"id": "ARM07_PUMPSWAP_DIRECT"}] + [{"id": f"ARM{i:02d}"} for i in range(1, 13)]}
    manifest = {"cohort_id": "cohort-test"}
    cohort = {"status": "PASS_CLEAN_FRESH_COHORT"}
    ranking = {"min_actionable_n": 50, "winner": "ARM07_PUMPSWAP_DIRECT", "arms": []}
    economics = {
        "arm_id": "ARM07_PUMPSWAP_DIRECT", "cohort": "FRESH_PROSPECTIVE", "outcome_source": "POST_FREEZE",
        "exact_evaluator": True, "metrics": {"actionable_n": 51, "pnl_quote": 0.1, "profit_factor": 1.5, "controls_outperformed": True},
    }
    source = {"status": "BLOCKED_PROSPECTIVE_SOURCE_GATE", "source": {"transport_clean": False}}
    paths = {}
    for name, value in (("registry", registry), ("manifest", manifest), ("cohort", cohort), ("ranking", ranking), ("economics", economics), ("source", source)):
        path = tmp_path / f"{name}.json"
        write(path, value)
        paths[name] = path
    output = tmp_path / "review.json"
    script = Path(__file__).with_name("finalReviewKefi13ArmPromotionV1.py")
    result = subprocess.run(
        [sys.executable, str(script), "--registry", str(paths["registry"]), "--manifest", str(paths["manifest"]),
         "--cohort-audit", str(paths["cohort"]), "--ranking", str(paths["ranking"]),
         "--economics", str(paths["economics"]), "--source-compatibility", str(paths["source"]), "--output", str(output)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    review = json.loads(output.read_text(encoding="utf-8"))
    assert "SOURCE_COMPATIBILITY_GATE_FAILED" in review["errors"]
    assert review["decision"] == "DO_NOT_PROMOTE"
