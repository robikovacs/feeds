"""Unit tests for digest.py pure functions."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from script import digest


def test_load_config_reads_feeds_with_default_max(tmp_path):
    cfg = tmp_path / "feeds.yml"
    cfg.write_text(
        "feeds:\n"
        "  - url: https://example.com/a.xml\n"
        "  - url: https://example.com/b.xml\n"
        "ai_overview: true\n"
    )
    feeds, ai_overview = digest.load_config(cfg)
    assert feeds == [
        {"url": "https://example.com/a.xml", "max": digest.DEFAULT_MAX_PER_FEED},
        {"url": "https://example.com/b.xml", "max": digest.DEFAULT_MAX_PER_FEED},
    ]
    assert ai_overview is True


def test_load_config_per_feed_max_overrides_default(tmp_path):
    cfg = tmp_path / "feeds.yml"
    cfg.write_text(
        "feeds:\n"
        "  - url: https://example.com/a.xml\n"
        "    max: 3\n"
        "  - url: https://example.com/b.xml\n"
    )
    feeds, _ = digest.load_config(cfg)
    assert feeds[0]["max"] == 3
    assert feeds[1]["max"] == digest.DEFAULT_MAX_PER_FEED


def test_load_config_defaults_ai_overview_false(tmp_path):
    cfg = tmp_path / "feeds.yml"
    cfg.write_text("feeds:\n  - url: https://example.com/a.xml\n")
    _, ai_overview = digest.load_config(cfg)
    assert ai_overview is False


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
    new = digest.filter_new_entries(
        entries, last_seen="2026-04-07T00:00:00+00:00", max_entries=10
    )
    assert [e["title"] for e in new] == ["A"]


def test_filter_new_entries_caps_at_max_entries_on_first_run():
    entries = [
        {
            "title": f"E{i}",
            "link": f"https://x/{i}",
            "published": f"2026-04-{10 + i:02d}T00:00:00+00:00",
        }
        for i in range(10)
    ]
    new = digest.filter_new_entries(entries, last_seen=None, max_entries=3)
    assert len(new) == 3
    assert [e["title"] for e in new] == ["E9", "E8", "E7"]


def test_filter_new_entries_caps_at_max_entries_on_incremental_run():
    entries = [
        {
            "title": f"E{i}",
            "link": f"https://x/{i}",
            "published": f"2026-04-{10 + i:02d}T00:00:00+00:00",
        }
        for i in range(10)
    ]
    # All 10 are newer than last_seen, but the cap kicks in.
    new = digest.filter_new_entries(
        entries, last_seen="2026-04-01T00:00:00+00:00", max_entries=4
    )
    assert len(new) == 4
    assert [e["title"] for e in new] == ["E9", "E8", "E7", "E6"]


def test_filter_new_entries_dedupes_by_link():
    entries = [
        {"title": "Dup", "link": "https://x/a", "published": "2026-04-10T00:00:00+00:00"},
        {"title": "Dup copy", "link": "https://x/a", "published": "2026-04-10T00:00:00+00:00"},
    ]
    new = digest.filter_new_entries(entries, last_seen=None, max_entries=10)
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


def test_get_repo_and_category_picks_announcements(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    payload = {"data": {"repository": {
        "id": "REPO1",
        "discussionCategories": {"nodes": [
            {"id": "C1", "name": "General", "slug": "general"},
            {"id": "C2", "name": "Announcements", "slug": "announcements"},
        ]},
    }}}
    with patch("script.digest.urllib.request.urlopen", return_value=_fake_response(payload)):
        repo_id, cat_id = digest.get_repo_and_category("owner", "name")
    assert (repo_id, cat_id) == ("REPO1", "C2")


def test_get_repo_and_category_raises_when_missing(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    payload = {"data": {"repository": {
        "id": "REPO1",
        "discussionCategories": {"nodes": [
            {"id": "C1", "name": "General", "slug": "general"},
        ]},
    }}}
    with patch("script.digest.urllib.request.urlopen", return_value=_fake_response(payload)):
        with pytest.raises(RuntimeError, match="Announcements"):
            digest.get_repo_and_category("owner", "name")


def test_create_discussion_returns_url(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    payload = {"data": {"createDiscussion": {"discussion": {"url": "https://github.com/o/r/discussions/1"}}}}
    with patch("script.digest.urllib.request.urlopen", return_value=_fake_response(payload)):
        url = digest.create_discussion("REPO1", "C2", "Title", "Body")
    assert url == "https://github.com/o/r/discussions/1"


def test_blurb_empty_returns_empty():
    assert digest._blurb("") == ""
    assert digest._blurb(None) == ""


def test_blurb_strips_html_tags():
    assert digest._blurb("<p>Hello <b>world</b></p>") == "Hello world"


def test_blurb_decodes_entities():
    assert digest._blurb("foo &amp; bar &lt;baz&gt;") == "foo & bar <baz>"


def test_blurb_collapses_whitespace():
    assert digest._blurb("foo\n\n  \tbar") == "foo bar"


def test_blurb_truncates_at_word_boundary():
    text = "hello world " * 50  # 600 chars
    result = digest._blurb(text)
    assert result.endswith("…")
    assert len(result) <= 301
    clean = ("hello world " * 50).strip()
    trimmed = result.rstrip("…").rstrip()
    assert clean.startswith(trimmed)


def test_blurb_no_truncation_when_short():
    assert digest._blurb("short text") == "short text"
