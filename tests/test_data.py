from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from modelgate import data
from modelgate.common import Paths
from modelgate.train import TrainConfig

MIX = (0.5, 0.38, 0.12)


def small(seed: int, n: int = 2000) -> data.Dataset:
    return data.generate(n, np.random.default_rng(seed), MIX, slice_skew=0.7)


def test_same_seed_same_rows() -> None:
    a, b = small(3), small(3)
    assert np.array_equal(a.segment, b.segment)
    assert np.array_equal(a.label, b.label)
    for name in data.FEATURES:
        assert np.array_equal(a.features[name], b.features[name])


def test_different_seed_different_rows() -> None:
    assert not np.array_equal(small(3).features["income"], small(4).features["income"])


def test_region_score_only_collected_for_segment_c() -> None:
    ds = small(5)
    outside = ds.features["region_score"][ds.segment != "C"]
    inside = ds.features["region_score"][ds.segment == "C"]
    assert np.all(outside == 0.0)
    assert inside.std() > 0.5


def test_segment_mix_is_respected() -> None:
    ds = small(6, n=20000)
    for name, share in zip(data.SEGMENTS, MIX, strict=True):
        assert abs((ds.segment == name).mean() - share) < 0.015


def test_csv_round_trip(tmp_path) -> None:
    ds = small(7, n=300)
    path = tmp_path / "x.csv"
    data.write_csv(ds, path)
    back = data.read_csv(path)
    assert np.array_equal(back.segment, ds.segment)
    assert np.array_equal(back.label, ds.label)
    for name in data.FEATURES:
        assert np.array_equal(back.features[name], ds.features[name])


def test_generate_all_is_byte_stable_and_holdout_is_isolated(repo_copy: Paths) -> None:
    cfg = replace(data.DataConfig.from_yaml(repo_copy.config / "data.yaml"), n_train=500)
    cfg = replace(cfg, n_holdout=400, n_drift=300)
    first = data.generate_all(cfg, repo_copy.root / "a")
    second = data.generate_all(cfg, repo_copy.root / "b")
    assert first == second
    # Changing the training set size must not change the holdout set.
    third = data.generate_all(replace(cfg, n_train=900), repo_copy.root / "c")
    assert third["holdout"] == first["holdout"]
    assert third["train"] != first["train"]


def test_drift_batch_is_shifted(repo_copy: Paths) -> None:
    cfg = data.DataConfig.from_yaml(repo_copy.config / "data.yaml")
    data.generate_all(cfg, repo_copy.data)
    holdout = data.read_csv(repo_copy.data / "holdout.csv")
    drift = data.read_csv(repo_copy.data / "drift_batch.csv")
    assert drift.features["income"].mean() < holdout.features["income"].mean() - 0.2
    assert (drift.segment == "C").mean() > (holdout.segment == "C").mean() + 0.05


def test_segment_mix_must_sum_to_one(repo_copy: Paths) -> None:
    path = repo_copy.config / "data.yaml"
    text = path.read_text(encoding="utf-8").replace("C: 0.12", "C: 0.5")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=r"sum to 1\.0"):
        data.DataConfig.from_yaml(path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"features": ("income", "segment")}, "protected attribute"),
        ({"features": ("income", "shoe_size")}, "unknown features"),
        ({"features": ("income", "income")}, "duplicate"),
        ({"hyperparams": {"C": 1.0, "penalty": "l1"}}, "unknown hyperparams"),
        ({"model_family": "deep_net"}, "model_family"),
    ],
)
def test_train_config_validation(change: dict, message: str) -> None:
    base = {
        "seed": 1,
        "model_family": "logistic_regression",
        "output_schema": "s",
        "features": ("income",),
        "hyperparams": {"C": 1.0},
    }
    with pytest.raises(ValueError, match=message):
        TrainConfig(**{**base, **change})
