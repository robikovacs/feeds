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
  - url: https://github.blog/feed/
  - url: https://blog.cloudflare.com/rss

ai_summary: false   # set to true for AI-powered summaries (free via GitHub Models)
```

## How it works

- GitHub Actions runs on a weekly cron
- Polls your RSS/Atom feeds for new posts
- Optionally summarizes each post using GitHub Models (free, built into Actions)
- Creates an Announcement Discussion with the weekly digest
- GitHub emails everyone watching the repo
- State tracked in `state.json` (auto-committed)

## Email notifications

Watch this repo → **Custom** → check **Discussions** only.

## Change the schedule

Edit `.github/workflows/digest.yml` — update the cron expression. Examples: daily = `0 8 * * *`, Mon + Thu = `0 8 * * 1,4`.

## Want AI summaries?

Set `ai_summary: true` in `feeds.yml`. Uses GitHub Models (free for all accounts).

## Cost

$0 for public repos. Private repos use your GitHub Actions free tier minutes (2000 min/month — this job uses ~2 min/month).

Details: [Actions billing](https://docs.github.com/en/billing/managing-billing-for-your-products/about-billing-for-github-actions) · [GitHub Models billing](https://docs.github.com/en/billing/managing-billing-for-your-products/about-billing-for-github-models).

## Privacy

Your feed list is visible if the repo is public. Make it private if you prefer — everything still works within the free tier.
