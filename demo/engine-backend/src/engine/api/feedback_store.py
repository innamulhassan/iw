"""Feedback store — the operator's verdict, kept apart from the run. 04-data-model §5 / D5.

In-memory here (Mongo `db.feedback` in production). Feeds the learning loop + the autonomy ladder.
"""
from __future__ import annotations

from engine.domain import Feedback


class FeedbackStore:
    def __init__(self) -> None:
        self._items: list[Feedback] = []

    def add(self, feedback: Feedback) -> Feedback:
        self._items.append(feedback)
        return feedback

    def all(self) -> list[Feedback]:
        return list(self._items)

    def for_subject(self, domain: str, subject_id: str) -> list[Feedback]:
        return [f for f in self._items
                if f.subject.domain == domain and f.subject.id == subject_id]
