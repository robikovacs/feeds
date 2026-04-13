"""Poll RSS feeds, summarize new posts, post a digest to GitHub Discussions."""
from __future__ import annotations

import json
import os
import urllib.request
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


def render_digest(entries: list[dict]) -> str:
    """Render markdown body. Domains alphabetical, newest-first within each domain."""
    # Stable sort: first by published desc, then by domain asc.
    # Python's sort is stable, so within-domain order from the first pass is preserved.
    ordered = sorted(entries, key=lambda e: e["published"], reverse=True)
    ordered = sorted(ordered, key=lambda e: e["source_domain"])
    parts: list[str] = []
    for e in ordered:
        date = datetime.fromisoformat(e["published"]).strftime("%b %-d")
        parts.append(f"### [{e['title']}]({e['link']})")
        parts.append(f"**{e['source_domain']}** · {date}")
        if e.get("summary"):
            parts.append("")
            parts.append(f"> {e['summary']}")
        parts.append("")
        parts.append("---")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_title(count: int, today: datetime) -> str:
    date = today.strftime("%b %-d")
    noun = "post" if count == 1 else "posts"
    return f"Week of {date} \u2014 {count} new {noun}"


GITHUB_MODELS_URL = "https://models.github.ai/inference/chat/completions"
SUMMARY_MODEL = "openai/gpt-4o-mini"
SUMMARY_SYSTEM = "Summarize blog posts in 2-3 concise sentences. No fluff."


def summarize(title: str, content: str) -> str | None:
    """Return a short AI summary, or None on any failure."""
    body = json.dumps({
        "model": SUMMARY_MODEL,
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM},
            {"role": "user", "content": f"Title: {title}\nContent: {content[:2000]}"},
        ],
        "max_tokens": 200,
    }).encode()
    req = urllib.request.Request(
        GITHUB_MODELS_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[warn] summary failed for {title!r}: {e}")
        return None


GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


def _graphql(query: str, variables: dict) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        GITHUB_GRAPHQL_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Content-Type": "application/json",
            "User-Agent": "feeds-digest",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


def get_repo_and_category(owner: str, name: str) -> tuple[str, str]:
    """Return (repo_id, announcements_category_id). Raises if Announcements missing."""
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        id
        discussionCategories(first: 25) {
          nodes { id name slug }
        }
      }
    }
    """
    data = _graphql(query, {"owner": owner, "name": name})
    repo = data["repository"]
    for node in repo["discussionCategories"]["nodes"]:
        if node["slug"] == "announcements":
            return repo["id"], node["id"]
    raise RuntimeError(
        "Announcements category not found. Enable Discussions in repo settings."
    )


def create_discussion(repo_id: str, category_id: str, title: str, body: str) -> str:
    """Create an Announcement Discussion. Returns its URL."""
    mutation = """
    mutation($repoId: ID!, $catId: ID!, $title: String!, $body: String!) {
      createDiscussion(input: {
        repositoryId: $repoId, categoryId: $catId,
        title: $title, body: $body
      }) { discussion { url } }
    }
    """
    data = _graphql(mutation, {
        "repoId": repo_id, "catId": category_id,
        "title": title, "body": body,
    })
    return data["createDiscussion"]["discussion"]["url"]
