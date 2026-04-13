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


import calendar
from datetime import datetime, timezone
from time import struct_time
from urllib.parse import urlparse

import feedparser

MAX_FIRST_RUN_PER_FEED = 5


def _struct_to_iso(t: struct_time | None) -> str:
    """Convert feedparser's time.struct_time (UTC) to ISO-8601 UTC. Fallback: now."""
    if t is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc).isoformat(timespec="seconds")


def parse_entries(parsed: feedparser.FeedParserDict, feed_url: str) -> list[dict]:
    """Extract {title, link, published, source_domain, content} per entry."""
    domain = urlparse(feed_url).netloc or "unknown"
    out = []
    for e in parsed.entries:
        published_raw = e.get("published_parsed") or e.get("updated_parsed")
        content = ""
        if e.get("content"):
            content = e.content[0].get("value", "")
        elif e.get("summary"):
            content = e.summary
        out.append({
            "title": e.get("title", "(untitled)"),
            "link": e.get("link", ""),
            "published": _struct_to_iso(published_raw),
            "source_domain": domain,
            "content": content,
        })
    return out


def filter_new_entries(entries: list[dict], last_seen: str | None) -> list[dict]:
    """Return entries newer than last_seen, newest-first, deduped by link.

    First run (last_seen is None) caps at MAX_FIRST_RUN_PER_FEED.
    """
    seen_links: set[str] = set()
    unique: list[dict] = []
    for e in entries:
        if e["link"] in seen_links:
            continue
        seen_links.add(e["link"])
        unique.append(e)

    unique.sort(key=lambda e: e["published"], reverse=True)

    if last_seen is None:
        return unique[:MAX_FIRST_RUN_PER_FEED]
    return [e for e in unique if e["published"] > last_seen]


def newest_timestamp(entries: list[dict]) -> str:
    """Return max published timestamp among entries."""
    return max(e["published"] for e in entries)
