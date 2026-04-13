"""Poll RSS feeds, summarize new posts, post a digest to GitHub Discussions."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


def load_config(path: Path) -> tuple[list[str], bool]:
    """Return (feed_urls, ai_summary) from feeds.yml."""
    data = yaml.safe_load(path.read_text()) or {}
    feeds = [entry["url"] for entry in data.get("feeds", [])]
    ai_summary = bool(data.get("ai_summary", False))
    return feeds, ai_summary


def load_state(path: Path) -> dict[str, str]:
    """Return last-seen map. Missing file → empty dict (first run)."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict[str, str]) -> None:
    """Write state.json with sorted keys + trailing newline for stable diffs."""
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
