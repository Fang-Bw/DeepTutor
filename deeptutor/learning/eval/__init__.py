"""Offline, read-only evaluation of learning predictions against later attempts.

This package does not load application settings, open a learning database, or
call an LLM. The production mastery and retention algorithms remain unchanged.
"""

from .dataset import EvaluationAttempt, EvaluationDataset, load_dataset
from .runner import evaluate

__all__ = ["EvaluationAttempt", "EvaluationDataset", "evaluate", "load_dataset"]
