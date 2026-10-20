"""Train the challenger model described by config/train.yaml.

Outputs models/challenger/model.joblib and models/challenger/meta.json.
"""

from __future__ import annotations

import logging
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sklearn.base import ClassifierMixin
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from modelgate import data
from modelgate.common import Paths, load_yaml, round_metric, sha256_file, write_json

logger = logging.getLogger(__name__)

PROTECTED_ATTRIBUTE = "segment"

# Allowed hyperparameters per model family. Anything else in train.yaml is an error,
# so a typo cannot silently fall back to a library default.
FAMILY_HYPERPARAMS: dict[str, frozenset[str]] = {
    "logistic_regression": frozenset({"C", "max_iter"}),
    "gradient_boosting": frozenset({"n_estimators", "max_depth", "learning_rate", "subsample"}),
}


@dataclass(frozen=True)
class TrainConfig:
    seed: int
    model_family: str
    output_schema: str
    features: tuple[str, ...]
    hyperparams: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.model_family not in FAMILY_HYPERPARAMS:
            raise ValueError(
                f"model_family {self.model_family!r} not in {sorted(FAMILY_HYPERPARAMS)}"
            )
        unknown = set(self.hyperparams) - FAMILY_HYPERPARAMS[self.model_family]
        if unknown:
            raise ValueError(f"unknown hyperparams for {self.model_family}: {sorted(unknown)}")
        if not self.features:
            raise ValueError("features must not be empty")
        if len(set(self.features)) != len(self.features):
            raise ValueError(f"duplicate features: {self.features}")
        if PROTECTED_ATTRIBUTE in self.features:
            raise ValueError(f"{PROTECTED_ATTRIBUTE!r} is the protected attribute, not a feature")
        unknown_features = [f for f in self.features if f not in data.FEATURES]
        if unknown_features:
            raise ValueError(f"unknown features {unknown_features}; available: {data.FEATURES}")

    @classmethod
    def from_yaml(cls, path: Path) -> TrainConfig:
        raw = load_yaml(path)
        return cls(
            seed=int(raw["seed"]),
            model_family=str(raw["model_family"]),
            output_schema=str(raw["output_schema"]),
            features=tuple(str(f) for f in raw["features"]),
            hyperparams=dict(raw.get("hyperparams") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "model_family": self.model_family,
            "output_schema": self.output_schema,
            "features": list(self.features),
            "hyperparams": dict(self.hyperparams),
        }


def build_model(cfg: TrainConfig) -> ClassifierMixin:
    hp = cfg.hyperparams
    if cfg.model_family == "logistic_regression":
        return Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        C=float(hp.get("C", 1.0)),
                        max_iter=int(hp.get("max_iter", 500)),
                        random_state=cfg.seed,
                    ),
                ),
            ]
        )
    return GradientBoostingClassifier(
        n_estimators=int(hp.get("n_estimators", 150)),
        max_depth=int(hp.get("max_depth", 3)),
        learning_rate=float(hp.get("learning_rate", 0.1)),
        subsample=float(hp.get("subsample", 1.0)),
        random_state=cfg.seed,
    )


def train(paths: Paths, cfg: TrainConfig) -> dict[str, Any]:
    train_csv = paths.data / "train.csv"
    ds = data.read_csv(train_csv)
    model = build_model(cfg)

    started = time.perf_counter()
    model.fit(ds.matrix(cfg.features), ds.label)
    train_seconds = time.perf_counter() - started

    paths.challenger.mkdir(parents=True, exist_ok=True)
    model_path = paths.challenger / "model.joblib"
    joblib.dump(model, model_path)

    meta = {
        "config": cfg.to_dict(),
        "train_rows": len(ds),
        "train_positive_rate": round_metric(float(np.mean(ds.label))),
        "train_data_sha256": sha256_file(train_csv),
        "model_file": model_path.name,
        "model_sha256": sha256_file(model_path),
        "library_versions": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "numpy": np.__version__,
        },
        # Wall-clock time varies between runs; it is lineage, not a gate input.
        "train_seconds": round(train_seconds, 3),
    }
    write_json(paths.challenger / "meta.json", meta)
    logger.info(
        "trained %s on %d rows with %d features in %.2fs -> %s",
        cfg.model_family,
        len(ds),
        len(cfg.features),
        train_seconds,
        model_path.relative_to(paths.root).as_posix(),
    )
    return meta


def main(paths: Paths) -> dict[str, Any]:
    return train(paths, TrainConfig.from_yaml(paths.config / "train.yaml"))
