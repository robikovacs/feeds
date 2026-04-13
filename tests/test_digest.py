"""Unit tests for digest.py pure functions."""
import json
from pathlib import Path

import pytest

from script import digest


def test_load_config_reads_feeds_and_ai_flag(tmp_path):
    cfg = tmp_path / "feeds.yml"
    cfg.write_text(
        "feeds:\n"
        "  - url: https://example.com/a.xml\n"
        "  - url: https://example.com/b.xml\n"
        "ai_summary: true\n"
    )
    feeds, ai_summary = digest.load_config(cfg)
    assert feeds == ["https://example.com/a.xml", "https://example.com/b.xml"]
    assert ai_summary is True


def test_load_config_defaults_ai_summary_false(tmp_path):
    cfg = tmp_path / "feeds.yml"
    cfg.write_text("feeds:\n  - url: https://example.com/a.xml\n")
    feeds, ai_summary = digest.load_config(cfg)
    assert ai_summary is False


def test_load_state_missing_file_returns_empty(tmp_path):
    assert digest.load_state(tmp_path / "nope.json") == {}


def test_load_state_reads_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"https://x/": "2026-04-01T00:00:00Z"}))
    assert digest.load_state(path) == {"https://x/": "2026-04-01T00:00:00Z"}


def test_save_state_writes_sorted_json(tmp_path):
    path = tmp_path / "state.json"
    digest.save_state(path, {"b": "2026-01-01T00:00:00Z", "a": "2026-01-02T00:00:00Z"})
    # Sorted keys for stable diffs
    content = path.read_text()
    assert content.index('"a"') < content.index('"b"')
    assert content.endswith("\n")


import feedparser

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


def test_parse_entries_extracts_core_fields():
    parsed = feedparser.parse(str(FIXTURE))
    entries = digest.parse_entries(parsed, feed_url="https://example.com/feed")
    assert len(entries) == 2
    assert entries[0]["title"] == "Newer post"
    assert entries[0]["link"] == "https://example.com/newer"
    assert entries[0]["published"] == "2026-04-12T10:00:00+00:00"
    assert entries[0]["source_domain"] == "example.com"
    assert "Newer post body" in entries[0]["content"]


def test_filter_new_entries_returns_only_newer():
    entries = [
        {"title": "A", "link": "https://x/a", "published": "2026-04-10T00:00:00+00:00"},
        {"title": "B", "link": "https://x/b", "published": "2026-04-05T00:00:00+00:00"},
    ]
    new = digest.filter_new_entries(entries, last_seen="2026-04-07T00:00:00+00:00")
    assert [e["title"] for e in new] == ["A"]


def test_filter_new_entries_first_run_caps_at_five():
    entries = [
        {"title": f"E{i}", "link": f"https://x/{i}", "published": f"2026-04-{10 + i:02d}T00:00:00+00:00"}
        for i in range(10)
    ]
    new = digest.filter_new_entries(entries, last_seen=None)
    # newest-first, capped at 5
    assert len(new) == 5
    assert new[0]["title"] == "E9"


def test_filter_new_entries_dedupes_by_link():
    entries = [
        {"title": "Dup", "link": "https://x/a", "published": "2026-04-10T00:00:00+00:00"},
        {"title": "Dup copy", "link": "https://x/a", "published": "2026-04-10T00:00:00+00:00"},
    ]
    new = digest.filter_new_entries(entries, last_seen=None)
    assert len(new) == 1


def test_newest_timestamp_returns_max():
    entries = [
        {"published": "2026-04-10T00:00:00+00:00"},
        {"published": "2026-04-12T00:00:00+00:00"},
        {"published": "2026-04-11T00:00:00+00:00"},
    ]
    assert digest.newest_timestamp(entries) == "2026-04-12T00:00:00+00:00"
