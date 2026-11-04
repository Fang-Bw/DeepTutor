"""Offline evaluator invariants: no future labels, isolation, comparable cohorts."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError
import pytest

from deeptutor.learning.eval import EvaluationDataset, evaluate, load_dataset
from deeptutor.learning.eval.__main__ import main
from deeptutor.learning.eval.metrics import prediction_metrics
from deeptutor.learning.models import KnowledgeType, LearningEvidence
from deeptutor.learning.scheduler import SpacedRepetitionScheduler

BASE = 1_700_000_000.0
DAY = 86400.0


def attempt(id, outcome, *, day=0, learner="learner-a", kp="kp-a", **extra):
    return {
        "id": id,
        "learner_id": learner,
        "knowledge_point_id": kp,
        "knowledge_type": "concept",
        "timestamp": BASE + day * DAY if day is not None else None,
        "outcome": outcome,
        **extra,
    }


def dataset(*attempts):
    return EvaluationDataset(name="synthetic-test", provenance="synthetic", attempts=list(attempts))


def test_predicts_before_observing_target_and_sorts_timestamps():
    data = dataset(attempt("third", 0, day=3), attempt("first", 1), attempt("second", 1, day=1))
    report = evaluate(data)
    assert [row.attempt_id for row in report.predictions] == ["second", "third"]
    assert report.predictions[0].predictions["mastery"] == 0.5  # one earlier correct answer
    assert report.predictions[1].predictions["mastery"] == 0.8  # two earlier correct answers
    # Change both future outcomes: neither can influence the second prediction.
    changed = evaluate(
        dataset(attempt("first", 1), attempt("second", 0, day=1), attempt("third", 1, day=3))
    )
    assert report.predictions[0].predictions == changed.predictions[0].predictions
    assert report.predictions[0].scheduled_due_at == changed.predictions[0].scheduled_due_at


def test_separates_learners_and_knowledge_points():
    report = evaluate(
        dataset(
            attempt("a1", 1),
            attempt("a2", 1, day=1),
            attempt("b1", 0, learner="learner-b"),
            attempt("b2", 1, day=1, learner="learner-b"),
            attempt("c1", 0, kp="kp-b"),
            attempt("c2", 1, day=1, kp="kp-b"),
        )
    )
    predictions = {row.attempt_id: row.predictions for row in report.predictions}
    assert predictions["a2"]["mastery"] == 0.5
    assert predictions["b2"]["mastery"] == predictions["c2"]["mastery"] == 0
    assert predictions["b2"]["retention"] == predictions["c2"]["retention"]


def test_retention_uses_production_state_before_review_and_ignores_debug_env(monkeypatch):
    monkeypatch.setenv("LEARNING_DEBUG", "true")
    report = evaluate(dataset(attempt("a1", 1), attempt("a2", 0, day=2)))
    scheduler = SpacedRepetitionScheduler()
    scheduler.DEBUG_MODE = False
    state = scheduler.get_initial_state(KnowledgeType.CONCEPT, now=BASE)
    scheduler.schedule_review(
        state,
        KnowledgeType.CONCEPT,
        LearningEvidence(
            knowledge_point_id="kp-a",
            timestamp=BASE,
            result="correct",
            quality=1.0,
        ),
        now=BASE,
    )
    prediction = report.predictions[0]
    assert prediction.predictions["retention"] == scheduler.retrievability(
        state, now=BASE + 2 * DAY
    )
    assert prediction.scheduled_due_at == state.next_review_at
    assert prediction.elapsed_days == 2


def test_review_quality_only_changes_later_retention():
    standard = evaluate(dataset(attempt("a1", 1), attempt("a2", 1, day=2)))
    assisted = evaluate(dataset(attempt("a1", 1, review_quality=0.4), attempt("a2", 1, day=2)))
    assert (
        standard.predictions[0].predictions["mastery"]
        == assisted.predictions[0].predictions["mastery"]
    )
    assert (
        standard.predictions[0].predictions["retention"]
        > assisted.predictions[0].predictions["retention"]
    )


def test_untimed_data_has_no_fabricated_retention_or_delayed_metrics():
    report = evaluate(dataset(attempt("a1", 1, day=None), attempt("a2", 1, day=None)))
    row = report.predictions[0]
    assert row.predictions["mastery"] == 0.5
    assert row.predictions["retention"] is None
    assert row.elapsed_days is None
    payload = report.to_dict()
    assert payload["counts"]["untimed_timelines"] == 1
    assert payload["cohorts"]["delayed"]["attempt_count"] == 0
    assert payload["cohorts"]["all"]["models"]["retention"]["metrics"]["mean_squared_error"] is None


def test_delayed_cohort_uses_previous_attempt_and_paired_baselines():
    report = evaluate(
        dataset(
            attempt("a1", 1),
            attempt("a2", 0, day=1 / 24),
            attempt("a3", 1, day=1.5),
            attempt("b1", 0, day=None, learner="b"),
            attempt("b2", 0, day=None, learner="b"),
        )
    ).to_dict()
    assert report["cohorts"]["delayed"]["attempt_count"] == 1
    retention = report["cohorts"]["all"]["models"]["retention"]
    assert retention["metrics"]["count"] == 2
    paired = retention["baseline_comparisons"]["history_mean"]
    assert paired["paired_count"] == 2  # untimed b2 does not enter this comparison
    assert paired["baseline_metrics"]["mean_squared_error"] == 0.625
    assert (
        paired["error_delta"]["mean_squared_error"]
        == retention["metrics"]["mean_squared_error"] - 0.625
    )


def test_warmup_only_dataset_reports_unknown_metrics():
    report = evaluate(
        dataset(attempt("a1", 1), attempt("a2", 1, day=1)), minimum_history=3
    ).to_dict()
    assert report["counts"]["scored_attempts"] == 0
    assert report["counts"]["warmup_attempts"] == 2
    assert report["cohorts"]["all"]["models"]["mastery"]["metrics"]["brier_score"] is None


def test_deterministic_report_does_not_mutate_input(monkeypatch):
    data = dataset(attempt("a1", 1), attempt("a2", 0.4, day=2))
    before = data.model_dump_json()
    monkeypatch.setattr(
        "deeptutor.learning.scheduler.time.time", lambda: pytest.fail("wall clock read")
    )
    first = evaluate(data).to_json()
    assert first == evaluate(data).to_json()
    assert data.model_dump_json() == before
    assert json.loads(first)["dataset"]["provenance"] == "synthetic"


@pytest.mark.parametrize(
    "rows, message",
    [
        ([attempt("a", 1), attempt("a", 0, day=1)], "duplicate attempt"),
        ([attempt("a", 1), attempt("b", 0)], "duplicate timestamps"),
        ([attempt("a", 1), attempt("b", 0, day=None)], "timestamps must be present"),
        (
            [attempt("a", 1), attempt("b", 0, day=1, knowledge_type="memory")],
            "knowledge_type must remain",
        ),
        ([attempt("a", float("nan"))], "finite"),
        ([attempt("a", 1, timestamp=float("inf"))], "finite"),
        ([attempt("a", 2)], "less than or equal"),
        ([attempt("a", 1, timestamp=True)], "JSON number"),
        ([attempt("a", 1, unexpected="field")], "Extra inputs"),
    ],
)
def test_rejects_ambiguous_or_invalid_evidence(rows, message):
    with pytest.raises(ValidationError, match=message):
        dataset(*rows)


@pytest.mark.parametrize(
    "config",
    [
        {"minimum_history": 0},
        {"minimum_history": True},
        {"bins": 0},
        {"bins": 1.5},
        {"delayed_days": 0},
        {"delayed_days": float("nan")},
    ],
)
def test_rejects_invalid_evaluation_options(config):
    with pytest.raises(ValueError):
        evaluate(dataset(attempt("a", 1)), **config)


def test_metric_values_and_boundary_bins():
    metrics = prediction_metrics([0.0, 0.5, 1.0], [1.0, 0.0, 1.0], bins=2)
    assert metrics["mean_squared_error"] == pytest.approx(1.25 / 3)
    assert metrics["brier_score"] == metrics["mean_squared_error"]
    assert metrics["mean_absolute_error"] == 0.5
    assert metrics["expected_calibration_error"] == 0.5
    assert [bucket["count"] for bucket in metrics["calibration_bins"]] == [1, 2]


def test_partial_credit_is_not_mislabeled_as_binary_brier():
    metrics = prediction_metrics([0.4, 0.9], [0.5, 1])
    assert metrics["count"] == 2
    assert metrics["brier_count"] == 1
    assert metrics["brier_score"] == pytest.approx(0.01)
    assert prediction_metrics([0.4], [0.5])["brier_score"] is None
    empty = prediction_metrics([], [])
    assert empty["mean_squared_error"] is None
    assert all(bucket["mean_outcome"] is None for bucket in empty["calibration_bins"])


@pytest.mark.parametrize("predictions, outcomes", [([1], []), ([float("nan")], [1]), ([2], [1])])
def test_metrics_fail_closed_on_invalid_predictions(predictions, outcomes):
    with pytest.raises(ValueError):
        prediction_metrics(predictions, outcomes)


def test_json_jsonl_equivalence_and_utf8_bom(tmp_path):
    data = dataset(attempt("a1", 1), attempt("a2", 0, day=1))
    json_path = tmp_path / "attempts.json"
    jsonl_path = tmp_path / "attempts.jsonl"
    json_path.write_text(data.model_dump_json(), encoding="utf-8-sig")
    payload = data.model_dump(mode="json")
    header = {
        "type": "dataset",
        **{key: value for key, value in payload.items() if key != "attempts"},
    }
    jsonl_path.write_text(
        "\n".join(json.dumps(row) for row in [header, *payload["attempts"]]), encoding="utf-8"
    )
    assert load_dataset(json_path) == load_dataset(jsonl_path) == data
    assert (
        evaluate(load_dataset(json_path)).to_json() == evaluate(load_dataset(jsonl_path)).to_json()
    )


def test_jsonl_error_has_physical_line_number(tmp_path):
    path = tmp_path / "invalid.jsonl"
    path.write_text("\n{broken\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_dataset(path)


def test_cli_reports_synthetic_fixture_and_creates_report(tmp_path, capsys):
    fixture = Path(__file__).parents[1] / "eval" / "fixtures" / "synthetic_attempts.json"
    output = tmp_path / "report.json"
    assert main([str(fixture), "--save", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["dataset"]["provenance"] == "synthetic"
    assert report["counts"] == {
        "attempts_total": 13,
        "timelines_total": 4,
        "untimed_timelines": 1,
        "scored_attempts": 9,
        "warmup_attempts": 4,
        "delayed_attempts": 7,
    }
    assert "synthetic" in capsys.readouterr().out
    before = output.read_bytes()
    with pytest.raises(SystemExit) as error:
        main([str(fixture), "--save", str(output)])
    assert error.value.code == 2
    assert output.read_bytes() == before


def test_cli_cannot_overwrite_input(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(dataset(attempt("a", 1)).model_dump_json(), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        main([str(path), "--save", str(path)])
    assert path.read_bytes() == before
