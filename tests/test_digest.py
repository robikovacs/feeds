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
