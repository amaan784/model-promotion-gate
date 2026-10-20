"""Metric arithmetic on tiny inputs with hand-computed answers."""

from __future__ import annotations

import numpy as np
import pytest

from modelgate.evaluate import compute_metrics, expected_calibration_error


def test_overall_metrics_by_hand() -> None:
    y = np.array([0, 0, 1, 1])
    p = np.array([0.1, 0.4, 0.35, 0.8])
    seg = np.array(["A", "B", "B", "A"])
    m = compute_metrics(y, p, seg)
    o = m["overall"]
    # 4 positive/negative pairs: (0.35>0.1) (0.35<0.4) (0.8>0.1) (0.8>0.4) -> 3/4
    assert o["auc"] == 0.75
    # predictions at 0.5: [0, 0, 0, 1] -> 3 of 4 correct
    assert o["accuracy"] == 0.75
    # tp=1 fp=0 fn=1 -> precision 1, recall 0.5, F1 = 2/3
    assert o["f1"] == 0.6667
    # (0.01 + 0.16 + 0.4225 + 0.04) / 4 = 0.158125
    assert o["brier"] == 0.1581
    # segment A: pairs (0.8 > 0.1) -> 1.0; segment B: (0.35 < 0.4) -> 0.0
    assert m["segments"]["A"]["auc"] == 1.0
    assert m["segments"]["B"]["auc"] == 0.0
    assert m["segment_auc_gap"] == 1.0


def test_segment_with_one_class_has_no_auc() -> None:
    y = np.array([0, 1, 0, 1, 0, 0])
    p = np.array([0.2, 0.7, 0.3, 0.6, 0.1, 0.4])
    seg = np.array(["A", "A", "B", "B", "C", "C"])
    m = compute_metrics(y, p, seg)
    assert m["segments"]["C"]["auc"] is None
    assert m["segment_auc_gap"] == 0.0  # A and B both rank perfectly


def test_ece_by_hand() -> None:
    # 0.2 lands in bin 2 (observed 0), 0.9 in bin 9 (observed 1):
    # 0.5 * |0 - 0.2| + 0.5 * |1 - 0.9| = 0.15
    assert expected_calibration_error(np.array([0, 1]), np.array([0.2, 0.9])) == pytest.approx(0.15)


def test_ece_puts_probability_one_in_last_bin() -> None:
    assert expected_calibration_error(np.array([1]), np.array([1.0])) == pytest.approx(0.0)
