"""Backward-compatibility shim -- canonical module is jarvis_engine.harvesting.discovery."""

from jarvis_engine.harvesting.discovery import *  # noqa: F401,F403
from jarvis_engine.harvesting.discovery import (  # noqa: F401 -- private names
    _extract_topic_phrases,
    _get_recently_harvested_topics,
    _try_add_candidate,
    _add_phrases,
    _collect_from_recent_memories,
    _collect_from_kg_gaps,
    _collect_from_strong_kg_areas,
    _collect_from_activity_feed,
    _collect_from_learning_missions,
)
