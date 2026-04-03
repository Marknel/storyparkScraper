from __future__ import annotations

from typing import Protocol

from storypark_scraper.models import StoryContent


class Classifier(Protocol):
    def classify(self, story: StoryContent) -> list[str]:
        """Return zero or more semantic labels for future filtering hooks."""


class NoopClassifier:
    def classify(self, story: StoryContent) -> list[str]:
        return []


def build_classifier(enabled: bool, provider: str) -> Classifier:
    if not enabled or provider == "noop":
        return NoopClassifier()
    raise NotImplementedError(
        "Semantic classification is intentionally deferred. Keep classifier.enabled=false for now."
    )
