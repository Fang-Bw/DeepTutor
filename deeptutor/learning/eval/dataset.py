"""Strict JSON/JSONL schema for anonymized chronological learning evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from deeptutor.learning.models import KnowledgeType


class EvaluationAttempt(BaseModel):
    """An observed, non-voided assessment, available at ``timestamp``.

    ``outcome`` is the held-out normalized assessment score. ``review_quality``
    may differ when an assisted answer has less retention evidence. Neither is
    exposed to a predictor until after that attempt has been scored.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    id: str
    learner_id: str
    knowledge_point_id: str
    knowledge_type: KnowledgeType
    outcome: float = Field(ge=0.0, le=1.0)
    timestamp: float | None = Field(default=None, ge=0.0)
    review_quality: float | None = Field(default=None, ge=0.0, le=1.0)
    assessment_type: Literal["quiz", "qualitative", "review"] = "quiz"

    @field_validator("id", "learner_id", "knowledge_point_id")
    @classmethod
    def nonempty_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("identifier must not be blank")
        return value

    @field_validator("timestamp", "outcome", "review_quality", mode="before")
    @classmethod
    def require_number(cls, value: object) -> object:
        # Reject string coercion and bool timestamps: these hide common export
        # mistakes. JSON true/false outcomes must be exported as 1/0 explicitly.
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("use a JSON number, not a string or boolean")
        return value


class EvaluationDataset(BaseModel):
    """One evaluation dataset; provenance is always retained in the report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    name: str = "learning-eval"
    provenance: Literal["synthetic", "observed", "unspecified"] = "unspecified"
    attempts: list[EvaluationAttempt] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_timelines(self) -> EvaluationDataset:
        seen_ids: set[str] = set()
        groups: dict[tuple[str, str], list[EvaluationAttempt]] = {}
        for attempt in self.attempts:
            if attempt.id in seen_ids:
                raise ValueError(f"duplicate attempt id: {attempt.id!r}")
            seen_ids.add(attempt.id)
            groups.setdefault((attempt.learner_id, attempt.knowledge_point_id), []).append(attempt)
        for key, attempts in groups.items():
            if len({attempt.knowledge_type for attempt in attempts}) != 1:
                raise ValueError(f"knowledge_type must remain constant within {key!r}")
            timestamps = [attempt.timestamp for attempt in attempts]
            timed = [timestamp for timestamp in timestamps if timestamp is not None]
            if timed and len(timed) != len(timestamps):
                raise ValueError(f"timestamps must be present for every attempt or none in {key!r}")
            if len(timed) != len(set(timed)):
                raise ValueError(f"duplicate timestamps have ambiguous ordering within {key!r}")
        return self


def load_dataset(path: str | Path) -> EvaluationDataset:
    """Load JSON (object/list) or JSONL (optional dataset header + attempts).

    A JSONL header must be the first nonempty line and use ``type=dataset``.
    Only explicit input is read. Validation reports locations, not answer text.
    """
    source = Path(path)
    if source.suffix.lower() not in {".json", ".jsonl", ".ndjson"}:
        raise ValueError("dataset must use .json, .jsonl, or .ndjson")
    try:
        text = source.read_text(encoding="utf-8-sig")
        if source.suffix.lower() == ".json":
            payload = json.loads(text)
            if isinstance(payload, list):
                payload = {"attempts": payload}
        else:
            rows = []
            metadata = {}
            first_line = True
            for line_number, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"line {line_number}: {exc.msg}") from exc
                if first_line and isinstance(row, dict) and row.get("type") == "dataset":
                    metadata = {key: value for key, value in row.items() if key != "type"}
                    if "attempts" in metadata:
                        raise ValueError("JSONL header must not contain attempts")
                else:
                    rows.append(row)
                first_line = False
            payload = {**metadata, "attempts": rows}
        return EvaluationDataset.model_validate(payload)
    except ValidationError as exc:
        error = exc.errors(include_input=False)[0]
        location = ".".join(str(part) for part in error["loc"]) or "dataset"
        raise ValueError(f"{source}: {location}: {error['msg']}") from exc
    except (OSError, ValueError) as exc:
        raise ValueError(f"{source}: {exc}") from exc
