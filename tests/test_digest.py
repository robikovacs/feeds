"""Unit tests for digest.py pure functions."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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


def test_render_digest_groups_by_source_and_includes_summary():
    entries = [
        {
            "title": "Post A",
            "link": "https://a.com/1",
            "published": "2026-04-10T12:00:00+00:00",
            "source_domain": "a.com",
            "summary": "AI summary A.",
        },
        {
            "title": "Post B",
            "link": "https://b.com/2",
            "published": "2026-04-11T12:00:00+00:00",
            "source_domain": "b.com",
            "summary": None,
        },
    ]
    md = digest.render_digest(entries)
    assert "### [Post A](https://a.com/1)" in md
    assert "### [Post B](https://b.com/2)" in md
    assert "**a.com** · Apr 10" in md
    assert "**b.com** · Apr 11" in md
    assert "> AI summary A." in md
    # No blockquote when summary missing
    b_section = md.split("### [Post B]")[1]
    assert ">" not in b_section.split("---")[0]


def test_render_title_uses_weekly_range():
    title = digest.render_title(count=7, today=datetime(2026, 4, 13, tzinfo=timezone.utc))
    assert title == "Week of Apr 13 — 7 new posts"


def test_render_title_singular():
    title = digest.render_title(count=1, today=datetime(2026, 4, 13, tzinfo=timezone.utc))
    assert title == "Week of Apr 13 — 1 new post"


def test_render_digest_within_domain_newest_first():
    entries = [
        {
            "title": "Older A",
            "link": "https://a.com/old",
            "published": "2026-04-05T00:00:00+00:00",
            "source_domain": "a.com",
            "summary": None,
        },
        {
            "title": "Newer A",
            "link": "https://a.com/new",
            "published": "2026-04-12T00:00:00+00:00",
            "source_domain": "a.com",
            "summary": None,
        },
    ]
    md = digest.render_digest(entries)
    # Newer A should appear before Older A in the output.
    assert md.index("Newer A") < md.index("Older A")


def _fake_response(payload: dict):
    class _Resp:
        def __enter__(self_inner):
            return self_inner
        def __exit__(self_inner, *a):
            return False
        def read(self_inner):
            return json.dumps(payload).encode()
    return _Resp()


def test_summarize_returns_content_on_success(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    payload = {"choices": [{"message": {"content": "Short summary."}}]}
    with patch("script.digest.urllib.request.urlopen", return_value=_fake_response(payload)):
        result = digest.summarize("Title", "Some content here.")
    assert result == "Short summary."


def test_summarize_returns_none_on_http_error(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    def boom(*a, **kw):
        raise OSError("network down")
    with patch("script.digest.urllib.request.urlopen", side_effect=boom):
        assert digest.summarize("Title", "content") is None
