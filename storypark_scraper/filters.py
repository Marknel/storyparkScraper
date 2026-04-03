from __future__ import annotations

import re

from storypark_scraper.config import FiltersConfig, FilterSet
from storypark_scraper.models import FilterDecision, StoryContent


class StoryFilter:
    def __init__(self, config: FiltersConfig):
        self._config = config

    def decide(self, story: StoryContent) -> FilterDecision:
        effective = _merge_filters(self._config, self._config.per_child.get(story.child_name))
        searchable_text = story.searchable_text.casefold()
        content_type = story.content_type.casefold()
        author = story.author.casefold()

        for keyword in effective.exclude_keywords:
            if keyword and keyword.casefold() in searchable_text:
                return FilterDecision(
                    include=False,
                    reason=f"excluded by keyword match: {keyword}",
                    matched_rule=keyword,
                )

        for pattern in effective.exclude_regexes:
            if pattern and re.search(pattern, story.searchable_text, re.IGNORECASE):
                return FilterDecision(
                    include=False,
                    reason=f"excluded by regex match: {pattern}",
                    matched_rule=pattern,
                )

        for excluded_author in effective.exclude_authors:
            if excluded_author and author == excluded_author.casefold():
                return FilterDecision(
                    include=False,
                    reason=f"excluded by author: {excluded_author}",
                    matched_rule=excluded_author,
                )

        for excluded_type in effective.exclude_content_types:
            if excluded_type and content_type == excluded_type.casefold():
                return FilterDecision(
                    include=False,
                    reason=f"excluded by content type: {excluded_type}",
                    matched_rule=excluded_type,
                )

        return FilterDecision(include=True, reason="accepted by default filters")


def _merge_filters(global_filters: FiltersConfig, child_filters: FilterSet | None) -> FilterSet:
    if child_filters is None:
        return FilterSet(
            exclude_keywords=list(global_filters.exclude_keywords),
            exclude_regexes=list(global_filters.exclude_regexes),
            exclude_authors=list(global_filters.exclude_authors),
            exclude_content_types=list(global_filters.exclude_content_types),
        )
    return FilterSet(
        exclude_keywords=list(global_filters.exclude_keywords) + list(child_filters.exclude_keywords),
        exclude_regexes=list(global_filters.exclude_regexes) + list(child_filters.exclude_regexes),
        exclude_authors=list(global_filters.exclude_authors) + list(child_filters.exclude_authors),
        exclude_content_types=list(global_filters.exclude_content_types)
        + list(child_filters.exclude_content_types),
    )
