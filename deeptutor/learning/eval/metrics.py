"""Dependency-free prediction metrics; empty cohorts remain unknown, not zero."""

from __future__ import annotations

import math
from typing import Any, Sequence


def prediction_metrics(
    predictions: Sequence[float], outcomes: Sequence[float], *, bins: int = 10
) -> dict[str, Any]:
    """Score bounded predictions. Brier applies only to binary outcome rows.

    For partial-credit outcomes MSE and bin calibration describe expected
    normalized score, not a calibrated binary recall probability.
    """
    if len(predictions) != len(outcomes):
        raise ValueError("predictions and outcomes must have equal lengths")
    if isinstance(bins, bool) or not isinstance(bins, int) or not 1 <= bins <= 100:
        raise ValueError("bins must be an integer between 1 and 100")
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in [*predictions, *outcomes]):
        raise ValueError("predictions and outcomes must be finite numbers between 0 and 1")
    count = len(predictions)
    errors = [
        prediction - outcome for prediction, outcome in zip(predictions, outcomes, strict=True)
    ]
    binary_errors = [
        error for error, outcome in zip(errors, outcomes, strict=True) if outcome in (0.0, 1.0)
    ]
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for prediction, outcome in zip(predictions, outcomes, strict=True):
        buckets[min(int(prediction * bins), bins - 1)].append((prediction, outcome))
    calibration = []
    weighted_gap = 0.0
    for index, bucket in enumerate(buckets):
        mean_prediction = sum(pred for pred, _ in bucket) / len(bucket) if bucket else None
        mean_outcome = sum(value for _, value in bucket) / len(bucket) if bucket else None
        gap = abs(mean_prediction - mean_outcome) if bucket else None
        weighted_gap += (gap or 0.0) * len(bucket)
        calibration.append(
            {
                "lower": index / bins,
                "upper": (index + 1) / bins,
                "count": len(bucket),
                "mean_prediction": mean_prediction,
                "mean_outcome": mean_outcome,
                "absolute_gap": gap,
            }
        )
    return {
        "count": count,
        "mean_squared_error": sum(error**2 for error in errors) / count if count else None,
        "mean_absolute_error": sum(abs(error) for error in errors) / count if count else None,
        "brier_count": len(binary_errors),
        "brier_score": (
            sum(error**2 for error in binary_errors) / len(binary_errors) if binary_errors else None
        ),
        "expected_calibration_error": weighted_gap / count if count else None,
        "calibration_bins": calibration,
    }
