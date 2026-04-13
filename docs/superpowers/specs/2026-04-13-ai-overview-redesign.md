# Digest redesign: RSS descriptions + single AI overview

## Problem

The first production run of the weekly digest hit two failures at once (see run on `robikovacs/feeds-demo` Discussion #2):

1. **Entry flood.** Vercel's feed returned 56 new entries because `filter_new_entries` only caps at 5 on the first run (`last_seen is None`). Every subsequent run is unbounded.
2. **Rate limit.** With `ai_summary: true` and 56 posts, the per-article summarize loop hit HTTP 429 on GitHub Models after ~30 calls at 0.5s spacing.

The script recovered gracefully — the Discussion posted with 26 missing summaries — but the digest was noisy, summaries were incomplete, and the week was dominated by Vercel product-update micro-announcements.

## Root cause framing

Rate limiting is a symptom. The disease is **"AI summarize N articles, where N can be arbitrary."** Instead of fixing rate handling (retry, backoff, batching), we remove the per-article AI path entirely.

## Design

Three changes. They are independent but ship together.

### 1. Per-article blurb comes from the RSS feed itself

The RSS entry already contains a `summary` (Atom `<summary>`) or `content` (Atom `<content>`, RSS `description`) field. `parse_entries` already collects this into the `content` key — we just don't render it. Now we do.

Render each post as:

```markdown
### [Post title](link)
**domain.com** · Apr 11

> First ~300 characters of the feed description, HTML stripped, entity decoded.

---
```

Implementation: a small `_blurb(text) -> str` helper that:
- Strips HTML tags via `re.sub(r'<[^>]+>', '', text)`
- Decodes entities via `html.unescape`
- Collapses whitespace
- Truncates to 300 chars at a word boundary, appending `…` if truncated

When the content is empty or becomes empty after cleaning, the blockquote is omitted (same pattern as today's missing-summary handling).

**Zero API calls, zero rate limit, zero model dependency.** Works for every post in every feed, always.

### 2. Single AI overview at the top (optional)

Replace the `ai_summary` per-article flag with `ai_overview` (single-call). When enabled, `main()` makes **one** call to GitHub Models with the titles + domains of all new posts and asks for a 2-3 sentence "this week" paragraph. On success, prepend to the digest body. On failure (any exception, including 429), skip silently — the digest still posts without an overview.

Prompt structure:

```
System: Write a 2-3 sentence overview of this week's developer news. Name the 2-3 biggest themes. No fluff.
User: Posts this week:
- "Introducing the AI Gateway" — vercel.com
- "CVE-2026-23869 disclosed" — github.blog
- ...
```

One API call, no matter the entry count. Cannot rate-limit itself.

**Function:** `generate_overview(entries: list[dict]) -> str | None`. Returns the paragraph or `None` on failure. `summarize()` is removed.

### 3. Per-feed configurable cap

`filter_new_entries` gains a required `max_entries` parameter and always applies it (drops the first-run-only branch). Feeds can override the default cap in `feeds.yml`:

```yaml
feeds:
  - url: https://simonwillison.net/atom/everything/
  - url: https://hnrss.org/frontpage?points=100
    max: 5
```

`max` is optional. When absent, `DEFAULT_MAX_PER_FEED = 15` applies. The `MAX_FIRST_RUN_PER_FEED` constant is removed.

## Config format changes

`feeds.yml` before:

```yaml
feeds:
  - url: https://example.com/a.xml
ai_summary: false
```

`feeds.yml` after:

```yaml
feeds:
  - url: https://example.com/a.xml
  - url: https://example.com/b.xml
    max: 5
ai_overview: false
```

- `ai_summary` → `ai_overview`
- Feed entries can carry an optional `max: int`
- Vercel is removed from the template feed list (noise — 50+ product updates per week)

**Backward compatibility:** Not a concern. This is a template repo; users haven't forked yet. Migration is "edit your file."

## Function signatures (contract summary)

```python
DEFAULT_MAX_PER_FEED = 15

def load_config(path: Path) -> tuple[list[dict], bool]:
    """Return (feeds, ai_overview). Each feed is {"url": str, "max": int}."""

def filter_new_entries(entries: list[dict], last_seen: str | None, max_entries: int) -> list[dict]:
    """Return entries newer than last_seen, capped at max_entries, deduped by link, newest-first."""

def generate_overview(entries: list[dict]) -> str | None:
    """Return a 2-3 sentence weekly overview, or None on failure."""

def render_digest(entries: list[dict], overview: str | None = None) -> str:
    """Render markdown body. If overview is provided, prepend it above the entries."""

# Removed:
# - summarize(title, content)
# - MAX_FIRST_RUN_PER_FEED
```

`main()` replaces the per-entry summarize loop with a single `generate_overview()` call gated on `ai_overview`, passes result to `render_digest`.

## Testing

**Remove:** `test_summarize_returns_content_on_success`, `test_summarize_returns_none_on_http_error`.

**Add:**
- `test_generate_overview_returns_paragraph_on_success` — mock urlopen, assert paragraph extracted
- `test_generate_overview_returns_none_on_http_error` — mock urlopen raises, assert None
- `test_filter_new_entries_caps_at_max_entries` — always caps, both first-run and incremental paths
- `test_render_digest_includes_blurb_from_content` — blurb blockquote present
- `test_render_digest_strips_html_from_blurb` — entity decoding + tag stripping
- `test_render_digest_prepends_overview_when_provided` — overview appears above entries

**Update:**
- `test_load_config_*` — assert new shape: feeds as list of dicts with `url`/`max`, `ai_overview` flag
- `test_filter_new_entries_*` — pass `max_entries`, remove first-run-specific assertions

## README

One edit to the "Configuration" section and one to "How it works." Rename "Want AI summaries?" section to match. Update the example:

```yaml
feeds:
  - url: https://simonwillison.net/atom/everything/
  - url: https://hnrss.org/frontpage?points=100
    max: 5
  - url: https://github.blog/feed/
  - url: https://blog.cloudflare.com/rss

ai_overview: false   # set true for a one-paragraph AI overview at the top
```

## Non-goals

- **No retry/backoff/batching on the summary path.** We're removing the path, not fixing it.
- **No rate-limit handling.** With one overview call per run, we can't rate-limit ourselves.
- **No HTML-to-markdown library** (`html2text`, `beautifulsoup`, etc.). Regex + `html.unescape` is sufficient for blurb text.
- **No migration shim for `ai_summary`.** It was never in a released template; anyone on pre-release can edit their file.

## Edge cases

| Case | Handling |
|---|---|
| Content is empty after strip | Skip blockquote for that entry |
| Content is pure HTML (no text) | Strip → empty → skip blockquote |
| Content longer than 300 chars | Truncate at word boundary, append `…` |
| Overview API call fails (429, timeout, anything) | Digest posts without overview, `[warn]` line in logs |
| Feed's `max` is zero or negative | Treat as 0 → feed contributes nothing (documented behavior) |
| `feeds.yml` missing `ai_overview` | Defaults to `False` (same as `ai_summary` today) |
| Old `ai_summary` key still in user's file | Ignored silently. Not an error. |
