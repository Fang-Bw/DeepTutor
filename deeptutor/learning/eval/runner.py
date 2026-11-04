"""Chronological predict-then-update replay, isolated per learner and objective."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any

from deeptutor.learning.mastery import compute_mastery
from deeptutor.learning.models import LearningEvidence
from deeptutor.learning.scheduler import RetentionScheduler, SpacedRepetitionScheduler

from .dataset import EvaluationAttempt, EvaluationDataset
from .metrics import prediction_metrics

MODELS = ("mastery", "retention", "history_mean", "constant_0_5")
BASELINES = ("history_mean", "constant_0_5")
ERROR_METRICS = ("mean_squared_error", "mean_absolute_error", "brier_score")


@dataclass(frozen=True)
class AttemptPrediction:
    attempt_id: str
    learner_id: str
    knowledge_point_id: str
    knowledge_type: str
    timestamp: float | None
    prior_attempts: int
    elapsed_days: float | None
    scheduled_due_at: float | None
    outcome: float
    predictions: dict[str, float | None]


@dataclass(frozen=True)
class EvaluationReport:
    dataset_name: str
    provenance: str
    dataset_sha256: str
    attempts_total: int
    timelines_total: int
    untimed_timelines: int
    minimum_history: int
    delayed_days: float
    calibration_bins: int
    retention_model: str
    predictions: tuple[AttemptPrediction, ...]

    def _cohort(self, rows: list[AttemptPrediction]) -> dict[str, Any]:
        models = {}
        for model in MODELS:
            eligible = [row for row in rows if row.predictions[model] is not None]
            outcomes = [row.outcome for row in eligible]
            metrics = prediction_metrics(
                [float(row.predictions[model]) for row in eligible],
                outcomes,
                bins=self.calibration_bins,
            )
            comparisons = {}
            if model not in BASELINES:
                for baseline in BASELINES:
                    baseline_metrics = prediction_metrics(
                        [float(row.predictions[baseline]) for row in eligible],
                        outcomes,
                        bins=self.calibration_bins,
                    )
                    comparisons[baseline] = {
                        "paired_count": len(eligible),
                        "baseline_metrics": baseline_metrics,
                        "error_delta": {
                            metric: (
                                metrics[metric] - baseline_metrics[metric]
                                if metrics[metric] is not None
                                else None
                            )
                            for metric in ERROR_METRICS
                        },
                    }
            models[model] = {"metrics": metrics, "baseline_comparisons": comparisons}
        return {"attempt_count": len(rows), "models": models}

    def to_dict(self) -> dict[str, Any]:
        rows = list(self.predictions)
        delayed = [
            row
            for row in rows
            if row.elapsed_days is not None and row.elapsed_days >= self.delayed_days
        ]
        return {
            "schema_version": 1,
            "dataset": {
                "name": self.dataset_name,
                "provenance": self.provenance,
                "sha256": self.dataset_sha256,
            },
            "configuration": {
                "minimum_history": self.minimum_history,
                "delayed_days": self.delayed_days,
                "calibration_bins": self.calibration_bins,
                "mastery_model": "deeptutor.learning.mastery.compute_mastery",
                "retention_model": self.retention_model,
                "scheduler_time_unit": "days (86400 seconds)",
                "baseline_comparison": "same eligible attempts; negative error_delta is better",
            },
            "counts": {
                "attempts_total": self.attempts_total,
                "timelines_total": self.timelines_total,
                "untimed_timelines": self.untimed_timelines,
                "scored_attempts": len(rows),
                "warmup_attempts": self.attempts_total - len(rows),
                "delayed_attempts": len(delayed),
            },
            "cohorts": {"all": self._cohort(rows), "delayed": self._cohort(delayed)},
            "by_knowledge_type": {
                knowledge_type: self._cohort(
                    [row for row in rows if row.knowledge_type == knowledge_type]
                )
                for knowledge_type in sorted({row.knowledge_type for row in rows})
            },
            "predictions": [asdict(row) for row in rows],
            "limitations": [
                "This measures next-assessment prediction, not causal learning benefit.",
                "Mastery scores are treated as expected scores for evaluation; they are not fitted probabilities.",
                "Brier score uses only binary outcomes; MSE/MAE and calibration bins also include partial credit.",
                "Each timeline's warmup attempts update state but are not scored.",
                "Untimed timelines use file order and cannot evaluate retention or delayed recall.",
                "Delayed means time since the previous recorded attempt, not proof of no intervening study.",
                "Metrics are attempt-weighted; frequent learners contribute more observations.",
                "Synthetic data validates the evaluator only, not production learning effectiveness.",
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, allow_nan=False)

    def summary_lines(self) -> list[str]:
        report = self.to_dict()
        counts = report["counts"]
        lines = [
            f"Dataset: {self.dataset_name} ({self.provenance})",
            f"Attempts: {self.attempts_total}; scored: {counts['scored_attempts']}; "
            f"warmup: {counts['warmup_attempts']}; delayed: {counts['delayed_attempts']}",
            "Model               Cohort    N      MSE      MAE    Brier    Delta MSE vs history",
        ]
        for cohort_name, cohort in report["cohorts"].items():
            for name, result in cohort["models"].items():
                metrics = result["metrics"]
                comparison = result["baseline_comparisons"].get("history_mean")
                delta = comparison["error_delta"]["mean_squared_error"] if comparison else None
                values = [
                    metrics["mean_squared_error"],
                    metrics["mean_absolute_error"],
                    metrics["brier_score"],
                    delta,
                ]
                formatted = " ".join(
                    f"{value:8.4f}" if value is not None else "     n/a" for value in values
                )
                lines.append(f"{name:19} {cohort_name:7} {metrics['count']:4} {formatted}")
        lines.append(
            "Prediction validation only; synthetic fixtures do not establish learning benefit."
        )
        return lines


def _evidence(attempt: EvaluationAttempt) -> LearningEvidence:
    return LearningEvidence(
        evidence_id=attempt.id,
        knowledge_point_id=attempt.knowledge_point_id,
        timestamp=attempt.timestamp,
        source="import",
        assessment_type=attempt.assessment_type,
        result=(
            "correct"
            if attempt.outcome == 1
            else "incorrect"
            if attempt.outcome == 0
            else "partial"
        ),
        quality=attempt.outcome if attempt.review_quality is None else attempt.review_quality,
    )


def evaluate(
    dataset: EvaluationDataset,
    *,
    minimum_history: int = 1,
    delayed_days: float = 1.0,
    bins: int = 10,
    scheduler: RetentionScheduler | None = None,
) -> EvaluationReport:
    """Score the next attempt before revealing its outcome to either model.

    Every observed result subsequently becomes history, including previously
    scored attempts (prequential evaluation). There is no train/test random
    split or fitting on future data. Timed and untimed timelines may coexist
    but must not be mixed within a learner/objective pair.

    An injected scheduler must be deterministic, use seconds for timestamps,
    and keep all mutable state inside its RepetitionState arguments.
    """
    if (
        isinstance(minimum_history, bool)
        or not isinstance(minimum_history, int)
        or minimum_history < 1
    ):
        raise ValueError("minimum_history must be a positive integer")
    if not math.isfinite(delayed_days) or delayed_days <= 0:
        raise ValueError("delayed_days must be finite and greater than zero")
    if isinstance(bins, bool) or not isinstance(bins, int) or not 1 <= bins <= 100:
        raise ValueError("bins must be an integer between 1 and 100")
    if scheduler is None:
        scheduler = SpacedRepetitionScheduler()
        # Production debug mode compresses days into seconds. Offline datasets
        # always use actual Unix seconds regardless of the caller's environment.
        scheduler.DEBUG_MODE = False
    groups: dict[tuple[str, str], list[EvaluationAttempt]] = {}
    for attempt in dataset.attempts:
        groups.setdefault((attempt.learner_id, attempt.knowledge_point_id), []).append(attempt)
    predictions = []
    untimed = 0
    for key in sorted(groups):
        attempts = groups[key]
        timed = attempts[0].timestamp is not None
        if timed:
            attempts = sorted(attempts, key=lambda attempt: attempt.timestamp)
            state = scheduler.get_initial_state(
                attempts[0].knowledge_type, now=attempts[0].timestamp
            )
        else:
            state = None
            untimed += 1
        history: list[float] = []
        for index, attempt in enumerate(attempts):
            if len(history) >= minimum_history:
                previous_timestamp = attempts[index - 1].timestamp
                elapsed = (attempt.timestamp - previous_timestamp) / 86400.0 if timed else None
                predictions.append(
                    AttemptPrediction(
                        attempt_id=attempt.id,
                        learner_id=attempt.learner_id,
                        knowledge_point_id=attempt.knowledge_point_id,
                        knowledge_type=attempt.knowledge_type.value,
                        timestamp=attempt.timestamp,
                        prior_attempts=len(history),
                        elapsed_days=elapsed,
                        scheduled_due_at=state.next_review_at if state is not None else None,
                        outcome=attempt.outcome,
                        predictions={
                            "mastery": compute_mastery(history),
                            "retention": (
                                scheduler.retrievability(state, now=attempt.timestamp)
                                if state is not None
                                else None
                            ),
                            "history_mean": sum(history) / len(history),
                            "constant_0_5": 0.5,
                        },
                    )
                )
            # Revealing this label earlier would leak the held-out outcome.
            history.append(attempt.outcome)
            if state is not None:
                state = scheduler.schedule_review(
                    state, attempt.knowledge_type, _evidence(attempt), now=attempt.timestamp
                )
    canonical = json.dumps(dataset.model_dump(mode="json"), sort_keys=True, allow_nan=False)
    return EvaluationReport(
        dataset_name=dataset.name,
        provenance=dataset.provenance,
        dataset_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        attempts_total=len(dataset.attempts),
        timelines_total=len(groups),
        untimed_timelines=untimed,
        minimum_history=minimum_history,
        delayed_days=delayed_days,
        calibration_bins=bins,
        retention_model=f"{type(scheduler).__module__}.{type(scheduler).__name__}",
        predictions=tuple(predictions),
    )
