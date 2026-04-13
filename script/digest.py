"""Poll RSS feeds, summarize new posts, post a digest to GitHub Discussions."""
from __future__ import annotations

import calendar
import html
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from time import struct_time
from urllib.parse import urlparse

import feedparser
import yaml


def load_config(path: Path) -> tuple[list[dict], bool]:
    """Return (feeds, ai_overview) from feeds.yml.

    Each feed is a dict {"url": str, "max": int}. `max` defaults to
    DEFAULT_MAX_PER_FEED when absent in the YAML.
    """
    data = yaml.safe_load(path.read_text()) or {}
    feeds = [
        {"url": entry["url"], "max": int(entry.get("max", DEFAULT_MAX_PER_FEED))}
        for entry in data.get("feeds", [])
    ]
    ai_overview = bool(data.get("ai_overview", False))
    return feeds, ai_overview


def load_state(path: Path) -> dict[str, str]:
    """Return last-seen map. Missing file → empty dict (first run)."""
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict[str, str]) -> None:
    """Write state.json with sorted keys + trailing newline for stable diffs."""
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


DEFAULT_MAX_PER_FEED = 15


def _struct_to_iso(t: struct_time | None) -> str:
    """Convert feedparser's time.struct_time (UTC) to ISO-8601 UTC. Fallback: now."""
    if t is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc).isoformat(timespec="seconds")


def _blurb(text: str | None, max_len: int = 300) -> str:
    """Turn RSS content into a short plain-text blurb.

    Strips HTML tags, decodes entities, collapses whitespace,
    truncates at the last word boundary within max_len and appends '…'.
    Returns '' for empty/None input.
    """
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    cut = text.rfind(" ", 0, max_len)
    if cut == -1:
        cut = max_len
    return text[:cut] + "…"


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


def filter_new_entries(
    entries: list[dict], last_seen: str | None, max_entries: int
) -> list[dict]:
    """Return entries newer than last_seen, newest-first, deduped by link,
    capped at max_entries. First run (last_seen is None) returns the newest
    max_entries. Incremental runs return entries with published > last_seen,
    then capped.
    """
    seen_links: set[str] = set()
    unique: list[dict] = []
    for e in entries:
        if e["link"] in seen_links:
            continue
        seen_links.add(e["link"])
        unique.append(e)

    unique.sort(key=lambda e: e["published"], reverse=True)

    if last_seen is not None:
        unique = [e for e in unique if e["published"] > last_seen]
    return unique[:max_entries]


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
OVERVIEW_MODEL = "openai/gpt-4o-mini"
OVERVIEW_SYSTEM = (
    "Write a 2-3 sentence overview of this week's developer news. "
    "Name the 2-3 biggest themes. No fluff."
)


def generate_overview(entries: list[dict]) -> str | None:
    """Return a short AI overview of the week's posts, or None on failure.

    Makes a single call to GitHub Models regardless of entry count.
    Silently returns None for empty input or any error.
    """
    if not entries:
        return None
    bullet_list = "\n".join(
        f"- {e['title']!r} — {e['source_domain']}" for e in entries
    )
    body = json.dumps({
        "model": OVERVIEW_MODEL,
        "messages": [
            {"role": "system", "content": OVERVIEW_SYSTEM},
            {"role": "user", "content": f"Posts this week:\n{bullet_list}"},
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
        print(f"[warn] overview failed: {e}")
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


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "feeds.yml"
STATE_PATH = ROOT / "state.json"
SUMMARY_SLEEP_SECONDS = 0.5


def fetch_feed(url: str) -> feedparser.FeedParserDict | None:
    """Parse a feed URL. Return None on failure."""
    try:
        parsed = feedparser.parse(url, request_headers={"User-Agent": "feeds-digest"})
        if parsed.bozo and not parsed.entries:
            print(f"[warn] feed failed: {url} ({parsed.bozo_exception})")
            return None
        return parsed
    except Exception as e:
        print(f"[warn] feed failed: {url} ({e})")
        return None


def main() -> int:
    feeds, ai_summary = load_config(CONFIG_PATH)
    state = load_state(STATE_PATH)

    all_new: list[dict] = []
    next_state = dict(state)

    for url in feeds:
        parsed = fetch_feed(url)
        if parsed is None:
            continue
        entries = parse_entries(parsed, feed_url=url)
        new = filter_new_entries(entries, last_seen=state.get(url))
        if not new:
            print(f"[info] {url}: no new entries")
            continue
        print(f"[info] {url}: {len(new)} new entries")
        all_new.extend(new)
        next_state[url] = newest_timestamp(entries)

    if not all_new:
        print("[info] no new posts across all feeds — exiting cleanly")
        return 0

    if ai_summary:
        for entry in all_new:
            entry["summary"] = summarize(entry["title"], entry["content"]) or None
            time.sleep(SUMMARY_SLEEP_SECONDS)
    else:
        for entry in all_new:
            entry["summary"] = None

    repo_slug = os.environ["GH_REPO"]  # "owner/name"
    owner, name = repo_slug.split("/")
    repo_id, cat_id = get_repo_and_category(owner, name)

    body = render_digest(all_new)
    title = render_title(count=len(all_new), today=datetime.now(timezone.utc))
    url = create_discussion(repo_id, cat_id, title, body)
    print(f"[info] posted discussion: {url}")

    save_state(STATE_PATH, next_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
