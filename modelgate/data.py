"""Seeded synthetic tabular data: a credit-default style problem with a
protected attribute `segment` (A/B/C).

The generator is the only source of data in ModelGate. Nothing is downloaded,
and no row describes a real person. It writes three CSV files:

- train.csv        used by train.py
- holdout.csv      used by evaluate.py; identical across commits while
                   config/data.yaml is unchanged
- drift_batch.csv  same process with the drift knobs at full strength; the nightly drift
                   check in drift.py does not read it and builds its own batch in memory

Two knobs shape the demo scenarios:

- slice_skew moves segment C's risk away from the shared features and onto
  region_score, a bureau score collected only for segment C (zero elsewhere).
  Dropping region_score then hurts segment C while barely moving overall AUC.
- drift shifts income and utilization and changes the segment mix.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from modelgate.common import Paths, load_yaml, sha256_file

logger = logging.getLogger(__name__)

SEGMENTS: tuple[str, ...] = ("A", "B", "C")
FEATURES: tuple[str, ...] = (
    "income",
    "debt_ratio",
    "tenure_years",
    "late_payments",
    "utilization",
    "region_score",
    "txn_velocity",
)
LABEL = "default_12m"
COLUMNS: tuple[str, ...] = ("row_id", "segment", *FEATURES, LABEL)
SPLITS: tuple[str, ...] = ("train", "holdout", "drift_batch")

# Per-segment constants of the risk process. Index order follows SEGMENTS.
_INCOME_OFFSET = np.array([0.2, 0.0, -0.3])
_BASE_LOGIT = np.array([-1.3, -1.1, -1.0])
# Effect sizes, tuned so the scenarios in modelgate/scenarios.py give GO,
# NEEDS_REVIEW and NO_GO against the baseline champion with clear margins.
# tests/test_scenarios.py pins those outcomes; retune both together.
_SHARED_SCALE = 1.5
_TXN_WEIGHT = 0.6
_REGION_WEIGHT = 2.4
_C_SHARED_DISCOUNT = 0.5


@dataclass(frozen=True)
class DriftKnobs:
    """Drift knobs from the `drift` block of config/data.yaml.

    income_shift is added to income, utilization_shift is added to utilization before it is
    clipped to [0, 1], and segment_mix (A, B, C order) is used in place of the base segment
    proportions. generate_all applies them at full strength to drift_batch.csv; drift.py scales
    them by day. The block's start_date and ramp_days are not stored here.
    """

    income_shift: float
    utilization_shift: float
    segment_mix: tuple[float, float, float]


@dataclass(frozen=True)
class DataConfig:
    """Generator settings from config/data.yaml: seed, split sizes, segment mix and knobs."""

    seed: int
    n_train: int
    n_holdout: int
    n_drift: int
    segment_mix: tuple[float, float, float]
    slice_skew: float
    drift: DriftKnobs

    @classmethod
    def from_yaml(cls, path: Path) -> DataConfig:
        """Load the config from a YAML file and check both segment mixes.

        Raises KeyError if a required key is missing, and ValueError if the file is not a
        mapping or a segment_mix does not have exactly the keys A, B and C summing to 1.0.
        """
        raw = load_yaml(path)
        drift = raw["drift"]
        return cls(
            seed=int(raw["seed"]),
            n_train=int(raw["n_train"]),
            n_holdout=int(raw["n_holdout"]),
            n_drift=int(raw["n_drift"]),
            segment_mix=_mix(raw["segment_mix"]),
            slice_skew=float(raw["slice_skew"]),
            drift=DriftKnobs(
                income_shift=float(drift["income_shift"]),
                utilization_shift=float(drift["utilization_shift"]),
                segment_mix=_mix(drift["segment_mix"]),
            ),
        )


def _mix(raw: dict[str, float]) -> tuple[float, float, float]:
    if set(raw) != set(SEGMENTS):
        raise ValueError(f"segment_mix must have exactly the keys {SEGMENTS}, got {sorted(raw)}")
    values = tuple(float(raw[s]) for s in SEGMENTS)
    if abs(sum(values) - 1.0) > 1e-9:
        raise ValueError(f"segment_mix must sum to 1.0, got {sum(values)}")
    return values  # type: ignore[return-value]


@dataclass(frozen=True)
class Dataset:
    """One split in memory as parallel numpy arrays: row ids, segments, features and label.

    features maps each feature name to its column; label holds the 0/1 default_12m outcome.
    """

    row_id: np.ndarray
    segment: np.ndarray
    features: dict[str, np.ndarray]
    label: np.ndarray

    def __len__(self) -> int:
        return len(self.label)

    def matrix(self, names: tuple[str, ...] | list[str]) -> np.ndarray:
        """Columns in the given order, as a float matrix for sklearn.

        Raises KeyError listing any name that is not a feature of this dataset.
        """
        missing = [n for n in names if n not in self.features]
        if missing:
            raise KeyError(f"unknown feature(s): {missing}; available: {list(self.features)}")
        return np.column_stack([self.features[n] for n in names])


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def generate(
    n: int,
    rng: np.random.Generator,
    segment_mix: tuple[float, float, float],
    slice_skew: float,
    income_shift: float = 0.0,
    utilization_shift: float = 0.0,
) -> Dataset:
    """Draw n rows. Every draw has a fixed size and order, so a given rng state
    consumes the same random numbers regardless of the knob values; a knob changes
    only the values it acts on, not the underlying draws."""
    seg_idx = rng.choice(len(SEGMENTS), size=n, p=segment_mix)
    is_c = seg_idx == SEGMENTS.index("C")

    income = rng.normal(0.0, 1.0, n) + _INCOME_OFFSET[seg_idx] + income_shift
    debt_ratio = rng.beta(2.0, 5.0, n)
    tenure_years = rng.gamma(2.0, 2.5, n)
    late_payments = rng.poisson(0.5 + 1.2 * debt_ratio).astype(float)
    utilization = np.clip(rng.beta(2.0, 3.0, n) + utilization_shift, 0.0, 1.0)
    region_score = np.where(is_c, rng.normal(0.0, 1.0, n), 0.0)
    txn_velocity = rng.normal(0.0, 1.0, n)

    shared_risk = _SHARED_SCALE * (
        -0.9 * income
        + 2.5 * (debt_ratio - 0.29)
        - 0.10 * (tenure_years - 5.0)
        + 0.6 * (late_payments - 0.84)
        + 2.0 * (utilization - 0.4)
        + _TXN_WEIGHT * txn_velocity
    )
    shared_weight = np.where(is_c, 1.0 - _C_SHARED_DISCOUNT * slice_skew, 1.0)
    region_weight = np.where(is_c, _REGION_WEIGHT * slice_skew, 0.0)
    logit = _BASE_LOGIT[seg_idx] + shared_weight * shared_risk + region_weight * region_score
    label = (rng.random(n) < _sigmoid(logit)).astype(int)

    features = {
        "income": income,
        "debt_ratio": debt_ratio,
        "tenure_years": tenure_years,
        "late_payments": late_payments,
        "utilization": utilization,
        "region_score": region_score,
        "txn_velocity": txn_velocity,
    }
    # Round once here so in-memory values equal what is written to disk.
    features = {k: np.round(v, 6) for k, v in features.items()}
    return Dataset(
        row_id=np.arange(n),
        segment=np.array(SEGMENTS)[seg_idx],
        features=features,
        label=label,
    )


def write_csv(ds: Dataset, path: Path) -> None:
    """Write the dataset to a CSV file with a COLUMNS header, replacing any existing file.

    Creates the parent directory if needed. Features are written with six decimal places and
    every line ends in LF, whatever the platform.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(",".join(COLUMNS) + "\n")
        for i in range(len(ds)):
            values = [str(int(ds.row_id[i])), str(ds.segment[i])]
            values += [f"{ds.features[f][i]:.6f}" for f in FEATURES]
            values.append(str(int(ds.label[i])))
            fh.write(",".join(values) + "\n")


def read_csv(path: Path) -> Dataset:
    """Read a CSV in the write_csv layout back into a Dataset.

    Raises ValueError if the header differs from COLUMNS, a row has the wrong number of
    fields, or a numeric field does not parse. A file with only the header gives an empty
    Dataset.
    """
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = tuple(next(reader))
        if header != COLUMNS:
            raise ValueError(f"{path}: unexpected columns {header}")
        rows = list(reader)
    cols = list(zip(*rows, strict=True)) if rows else [()] * len(COLUMNS)
    by_name = dict(zip(COLUMNS, cols, strict=True))
    return Dataset(
        row_id=np.array(by_name["row_id"], dtype=int),
        segment=np.array(by_name["segment"], dtype=str),
        features={f: np.array(by_name[f], dtype=float) for f in FEATURES},
        label=np.array(by_name[LABEL], dtype=int),
    )


def generate_all(cfg: DataConfig, out_dir: Path) -> dict[str, str]:
    """Write train.csv, holdout.csv and drift_batch.csv into out_dir and return their sha256.

    Each split draws from its own child of SeedSequence(cfg.seed), so changing one split's size
    leaves the other files unchanged. Returns hex digests keyed by split name.
    """
    # One independent stream per split: changing n_train never changes the holdout.
    train_ss, holdout_ss, drift_ss = np.random.SeedSequence(cfg.seed).spawn(3)
    splits = {
        "train": generate(
            cfg.n_train, np.random.default_rng(train_ss), cfg.segment_mix, cfg.slice_skew
        ),
        "holdout": generate(
            cfg.n_holdout, np.random.default_rng(holdout_ss), cfg.segment_mix, cfg.slice_skew
        ),
        "drift_batch": generate(
            cfg.n_drift,
            np.random.default_rng(drift_ss),
            cfg.drift.segment_mix,
            cfg.slice_skew,
            income_shift=cfg.drift.income_shift,
            utilization_shift=cfg.drift.utilization_shift,
        ),
    }
    fingerprints: dict[str, str] = {}
    for name, ds in splits.items():
        path = out_dir / f"{name}.csv"
        write_csv(ds, path)
        fingerprints[name] = sha256_file(path)
        logger.info(
            "wrote %s: %d rows, positive rate %.3f, sha256 %s",
            path.name,
            len(ds),
            ds.label.mean(),
            fingerprints[name][:12],
        )
    return fingerprints


def main(paths: Paths) -> dict[str, str]:
    """Generate train, holdout and drift_batch CSVs from config/data.yaml into the data dir.

    Returns the sha256 fingerprint of each written file, keyed by split name.
    """
    cfg = DataConfig.from_yaml(paths.config / "data.yaml")
    return generate_all(cfg, paths.data)
