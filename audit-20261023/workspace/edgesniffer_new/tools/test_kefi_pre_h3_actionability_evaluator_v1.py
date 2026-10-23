import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "kefi_pre_h3_actionability_evaluator_v1.py"
spec = importlib.util.spec_from_file_location("evaluator", SCRIPT); assert spec and spec.loader
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def assignments(*, target_true: int, control_true: int, controls: bool = True) -> list[dict]:
    result = []
    for index in range(320):
        record = {
            "target_id": f"target-{index}", "cluster_id": f"cluster-{index}",
            "pool": f"pool-{index % 80}", "chronological_block": f"block-{index % 8}",
            "target_complete_actionable": index < target_true,
            "controls": ([{"complete_actionable": index < control_true}] if controls else []),
        }
        result.append(record)
    return result


def test_pass_requires_meaningful_absolute_and_paired_advantage():
    result = m.classify(assignments(target_true=288, control_true=128), "seed", 300)
    assert result["status"] == "PASS"
    assert result["A_T_all"] == 0.9
    assert result["Delta_A_paired"] == 0.5


def test_reject_when_actionability_is_conclusively_inferior():
    result = m.classify(assignments(target_true=96, control_true=256), "seed", 300)
    assert result["status"] == "REJECT"


def test_inconclusive_when_coverage_is_insufficient_but_all_targets_remain_counted():
    sample = assignments(target_true=288, control_true=128)
    for item in sample[160:]:
        item["controls"] = []
    result = m.classify(sample, "seed", 300)
    assert result["status"] == "INCONCLUSIVE"
    assert result["targets_all"] == 320
    assert result["targets_paired"] == 160
    assert result["A_T_all"] == 0.9


def test_delta_is_paired_only_and_never_diluted_by_unmatched_targets():
    sample = assignments(target_true=160, control_true=0)
    for item in sample[160:]:
        item["controls"] = []
        item["target_complete_actionable"] = False
    result = m.classify(sample, "seed", 100)
    assert result["A_T_all"] == 0.5
    assert result["A_C_paired_equal_target_weighted"] == 0.0
    assert result["Delta_A_paired"] == 1.0
