import importlib.util
from pathlib import Path


PATH = Path(__file__).with_name("kefi_rich_preentry_feasibility_v2.py")
SPEC = importlib.util.spec_from_file_location("feasibility", PATH)
MOD = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)


def test_train_selected_threshold_has_nontrivial_balanced_accuracy():
    rows = []
    for idx in range(60):
        rows.append({"label": 1 if idx % 2 else 0, "z": [2.0 if idx % 2 else -2.0]})
    values = [MOD.sigmoid(row["z"][0]) for row in rows]
    threshold = MOD.choose_threshold(rows, values)
    assert MOD.metrics(rows, values, threshold)["balanced_accuracy"] == 1.0


def test_fit_is_deterministic_with_bounded_schedule():
    rows = [{"label": label, "z": [float(label * 2 - 1)]} for label in [0, 1] * 10]
    assert MOD.fit(rows, epochs=8) == MOD.fit(rows, epochs=8)


def test_threshold_grid_is_bounded():
    rows = [{"label": int(index % 2 == 0), "z": [0.0]} for index in range(1_000)]
    values = [index / 1_000 for index in range(1_000)]
    threshold = MOD.choose_threshold(rows, values)
    assert 0 <= threshold < 1


def test_vector_has_no_target_transaction_fields():
    pre = {"log_pre_pool_quote": 1, "log_pre_pool_base": 2, "amm_fee_bps": 3, "observed_pool_age_seconds": 4,
           "seconds_since_previous_observed_buy": None, "prior_pre_pool_quote_log_change": 0,
           **{f"prior_buy_count_{s}s": 0 for s in (5,20,60)}, **{f"prior_buy_quote_{s}s": 0 for s in (5,20,60)}}
    assert len(MOD.vector(pre)) == len(MOD.FEATURES)
