# Singapore Tech Job Tracker 🇸🇬

**Free cloud AI agent:** See [AGENT.md](AGENT.md) for Gemini free-tier matching,
Telegram conversations, application tracking, and private cover letter drafts on
GitHub Actions. Your laptop can be off. Start with `python agent.py init` and follow
the cloud activation steps. No OpenAI credits are required.

Automated, no-login tracker for Singapore-based tech **internships, new-graduate and entry-level jobs**.

- **First run:** imports existing matching positions into a database and sends a single Telegram roundup of up to 15 highlights.
- **Later runs:** alerts only when a new (or reopened) requisition is discovered. A daily summary is sent even when nothing changed (configurable with `summary_interval_hours`; set to `0` to disable).
- **Browsable archive:** `docs/index.html` is a searchable GitHub Pages dashboard. Downloadable `docs/jobs.csv` retains current and historical jobs.
- **Discovery sources:** 62 employer boards using Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Amazon careers and Google public career search pages, plus **optional Adzuna Singapore** employer searches. See [COVERAGE.md](COVERAGE.md) for direct coverage and gaps.
- **Schedule:** every 2 hours, at 01:17, 03:17, 05:17, 07:17, 09:17, 11:17, 13:17, 15:17, 17:17, 19:17, 21:17 and 23:17 **Singapore time**. New/reopened jobs trigger alerts; a summary is delivered on the first successful scan at least 24 hours after the last summary. GitHub scheduling may be delayed.
- **Storage:** `data/jobs.json` is committed automatically to your GitHub repository. It survives scheduled runs and lets the app avoid duplicate notifications.

Direct feeds include Google, Amazon/AWS, NVIDIA, Salesforce, Adobe, Intel, HP, HPE, PayPal, Cisco, Samsung, Micron and other employers. **Microsoft, Meta, Apple, TikTok, Shopee and several others currently depend on optional Adzuna searches and are not guaranteed coverage.** See [the full coverage list](COVERAGE.md), [all 63 requested employers](EMPLOYERS.md), and [LinkedIn alert setup](LINKEDIN.md).

## Setup (about 10 minutes of configuration)

### 1. Make a Telegram bot

1. Open Telegram and message **@BotFather**. Use `/newbot` and follow its instructions. Save the bot token securely.
2. Open your newly created bot and send `/start` to it.
3. Find your chat ID. You can do this locally with Python after installing dependencies:

   ```python
   import os, requests
   token = os.environ["TELEGRAM_BOT_TOKEN"]
   updates = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
   for update in updates.get("result", []):
       message = update.get("message") or {}
       print(message.get("chat", {}).get("id"))
   ```

   Before running the snippet, set `TELEGRAM_BOT_TOKEN` in your terminal environment. For PowerShell: `$env:TELEGRAM_BOT_TOKEN="your-token"`. The printed ID is the `TELEGRAM_CHAT_ID`. If nothing prints, send `/start` again and retry. Do **not** commit tokens or chat IDs.

### 2. Create a GitHub repository

Create an **empty public** repository named `sg-tech-job-tracker` on GitHub (do not initialize with a README). Download/unzip this project and from its folder run:

```bash
git init
git add .
git commit -m "Initial Singapore job tracker"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/sg-tech-job-tracker.git
git push -u origin main
```

A public repository makes GitHub Pages easiest to use. It contains only public job links, metadata and tracker configuration; your secrets remain in GitHub's secret store.

### 3. Add GitHub Actions secrets

Go to your GitHub repository → **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Required? | Value |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Yes for alerts | The token from BotFather |
| `TELEGRAM_CHAT_ID` | Yes for alerts | Your chat ID from the snippet |
| `ADZUNA_APP_ID` | Optional | Your Adzuna API ID |
| `ADZUNA_APP_KEY` | Optional | Your Adzuna API key |

**Adzuna:** Register at <https://developer.adzuna.com/>. It searches broader Singapore postings than the named companies. The script still works without it, but company coverage will be much narrower. API quotas and data availability depend on your account. Adzuna listings are attributed in the web dashboard and Telegram alerts as required by its published terms; do not remove that attribution.

### 4. Enable automatic runs

Go to **Actions → Singapore tech job scan → Run workflow** to trigger the first scan manually. This is the initial baseline, so pre-existing roles appear in the dashboard and you get **one** Telegram roundup rather than hundreds of alerts.

Then, in repository **Settings → Actions → General → Workflow permissions**, enable **Read and write permissions** if the workflow cannot push the updated job database. The workflow requests `contents: write`, but repository/organisation restrictions may override it.

The workflow will run on the cron schedule. GitHub may delay/drop scheduled jobs; check **Actions** periodically. In public repos, GitHub can disable scheduled workflows after 60 days without qualifying repository activity. For time-critical alerts, use dedicated hosting rather than assuming GitHub Actions is real-time.

### 5. Publish the dashboard

Under **Settings → Pages**, select **Deploy from a branch**, choose **main** and **/docs**, then **Save**.

After publishing, your dashboard should be available at:

```
https://YOUR_USERNAME.github.io/sg-tech-job-tracker/
```

Edit `config.json` and set `dashboard_url` to the actual URL. Commit and push. Future Telegram messages will include a direct dashboard link.

The dashboard lets you search, filter by internship vs graduate, see discovered-in-the-last-7-days, sort, and export CSV. "Open" means still present in an authoritative company feed or recently seen by an aggregator; verify on the application page before applying.

## Local development (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m unittest -v
python tracker.py --no-notify
```

This writes `data/jobs.json`, `docs/jobs.json` and `docs/jobs.csv`. To view the dashboard locally:

```powershell
python -m http.server 8000 --directory docs
```

Open <http://localhost:8000>.

To test Telegram locally:

```powershell
$env:TELEGRAM_BOT_TOKEN = "your-token"
$env:TELEGRAM_CHAT_ID = "your-chat-id"
# Optional:
$env:ADZUNA_APP_ID = "your-adzuna-id"
$env:ADZUNA_APP_KEY = "your-adzuna-key"
python tracker.py
```

Never upload `.env` to a public repository. For GitHub runs, use repository secrets, not environment files in the repository.

## Adding companies

The value of `slug` is the actual ATS board name in the employer's official careers URL. **Do not guess company names as slugs**: verify the company uses that provider, and check the listing URL first.

Examples in `config.json`:

```json
{"type": "lever", "slug": "palantir", "company": "Palantir"}
{"type": "ashby", "slug": "airwallex", "company": "Airwallex"}
{"type": "greenhouse", "slug": "simplifynext", "company": "SimplifyNext"}
{"type": "smartrecruiters", "slug": "Grab", "company": "Grab"}
```

Board URL → slug:

| Provider | Employer board URL | API base used by tracker |
| --- | --- | --- |
| Greenhouse | `job-boards.greenhouse.io/{slug}` | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` |
| Lever | `jobs.lever.co/{slug}` | `api.lever.co/v0/postings/{slug}` |
| Ashby | `jobs.ashbyhq.com/{slug}` | `api.ashbyhq.com/posting-api/job-board/{slug}` |
| SmartRecruiters | `jobs.smartrecruiters.com/{slug}/…` | `api.smartrecruiters.com/v1/companies/{slug}/postings` |

Workday boards use `type: "workday"` with the verified `slug` (tenant), `cluster` (such as `wd5`) and `site` fields. Other ATS providers may require a custom connector. The optional Adzuna query covers some of these jobs without scraping their portals, but does **not** guarantee complete coverage.

## Customizing alerts

In `config.json`:

- `priority_keywords`: Boost relevant technical role titles in alerts.
- `priority_companies`: Boost companies you particularly want to target.
- `summary_interval_hours`: Hours between periodic Telegram summaries (default 24; 0 disables). Summaries include open-role totals and employer feed health.
- `max_alerts_per_scan`: Limits new alerts per run (the rest remain pending, not dropped).
- `adzuna_companies`: Employer-filtered searches for major tech companies; requires Adzuna credentials and uses one request per company per scan.
- `adzuna_queries`: Broad queries sent to Adzuna, one page of up to 50 newest matches per query.
- `sources`: Add/remove employers' official job boards.

The tracker intentionally excludes senior/staff/principal/management-level roles, non-Singapore locations, and generic job titles without early-career evidence. Descriptions with explicit early experience requirements (such as 1-3 years or 2 years) also qualify. SmartRecruiters descriptions are fetched for Singapore technical roles to improve matching. This is **precision-oriented**, so some ambiguously titled junior roles will be missed. You can relax the `eligible()` rules if you want wider coverage.

## Data semantics and limitations

- **`first_seen`** is when this tracker discovered the job; it is *not* necessarily the date the employer first posted it.
- **`posted_at`** is a source-provided creation/release timestamp, which **may not equal** the day the employer first publicly posted the role (especially for aggregator results). Greenhouse `updated_at` is stored as an **update** timestamp, not misrepresented as a posting date.
- A role absent from an official company feed in **two consecutive successful scans** is marked `Closed`. Failed source requests do not close openings.
- Adzuna is a rolling search result, not a complete per-company roster. If an Adzuna posting hasn't appeared for 14 days, it becomes `Unverified`, **not** `Closed`.
- A first-time matching job triggers an alert. If that same official requisition closes and reappears, it can alert again as reopened.
- Storing a newly delivered notification requires a successful GitHub commit. An unexpected runner/commit failure after Telegram delivery could cause a duplicate alert on retry (at-least-once behavior).
- Some employers have robots restrictions, privacy settings, dynamic websites, or job portals that cannot be queried through these feeds. There is no guarantee of seeing *every* Singapore tech role.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| No Telegram message | Have you sent `/start` to your bot? Are the two Telegram secrets correctly set? Check Actions logs. |
| No listings | Run `python tracker.py` and inspect warnings. Confirm each board's slug exists. |
| Few jobs | Set up Adzuna and add more verified employer boards. |
| New jobs not notified | If Telegram was off, first successful notification is a one-time initial digest. After that, only unseen jobs trigger alerts. |
| Workflow can't commit | Grant Actions `contents: write` and check branch protection. |
| Dashboard says loading | Run the workflow once. Publish Pages from `/docs`. Verify `docs/jobs.json` exists. |
| GitHub scheduler stops | Re-enable Actions / check the 60-day public-repo rule. |

## API documentation

- Greenhouse: <https://docs.greenhouse.io/job-board>
- Lever: <https://github.com/lever/postings-api>
- Ashby: <https://developers.ashbyhq.com/docs/public-job-posting-api>
- SmartRecruiters: <https://developers.smartrecruiters.com/docs/posting-api>
- Adzuna: <https://developer.adzuna.com/overview>
- GitHub Actions schedules: <https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows>
