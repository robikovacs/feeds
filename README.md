# feeds

AI-powered RSS digests via GitHub Discussions. Zero config.

[![Use this template](https://img.shields.io/badge/Use%20this%20template-238636?style=for-the-badge&logo=github&logoColor=white)](https://github.com/robikovacs/feeds/generate)

Every Monday, this repo checks your RSS feeds, summarizes new posts with AI, and posts a digest to Discussions. Watch the repo to get email notifications.

## Setup

1. Click the green button above to create your copy
2. Enable Discussions in repo settings (Settings → General → Features)
3. Edit `feeds.yml` — add your RSS feeds
4. Go to **Actions → Weekly digest → Run workflow** to test

That's it. Digests arrive every Monday.

## Configuration

Edit `feeds.yml`:

```yaml
feeds:
  - url: https://simonwillison.net/atom/everything/
  - url: https://hnrss.org/frontpage?points=100
    max: 5
  - url: https://github.blog/feed/
  - url: https://blog.cloudflare.com/rss

ai_overview: false   # set true for a one-paragraph AI overview at the top
```

## How it works

- GitHub Actions runs on a weekly cron
- Polls your RSS/Atom feeds for new posts
- Renders each post with its own RSS description as a blurb
- Optionally generates a single AI overview paragraph at the top (free via GitHub Models)
- Posts an Announcement Discussion with the weekly digest
- GitHub emails everyone watching the repo
- State tracked in `state.json` (auto-committed)

## Email notifications

Watch this repo → **Custom** → check **Discussions** only.

## Change the schedule

Edit `.github/workflows/digest.yml` — update the cron expression. Examples: daily = `0 8 * * *`, Mon + Thu = `0 8 * * 1,4`.

## Want an AI overview?

Set `ai_overview: true` in `feeds.yml`. Makes a single call per run to GitHub Models (free) and prepends a short "this week" paragraph above the posts. One call per digest — no rate-limit issues, and if the call fails the digest still posts.

## Cost

$0 for public repos. Private repos use your GitHub Actions free tier minutes (2000 min/month — this job uses ~2 min/month).

Details: [Actions billing](https://docs.github.com/en/billing/managing-billing-for-your-products/about-billing-for-github-actions) · [GitHub Models billing](https://docs.github.com/en/billing/managing-billing-for-your-products/about-billing-for-github-models).

## Privacy

Your feed list is visible if the repo is public. Make it private if you prefer — everything still works within the free tier.
