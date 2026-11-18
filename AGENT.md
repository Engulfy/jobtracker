# Free cloud job agent

This setup uses a public GitHub repository, standard GitHub Actions runners,
Telegram, and Gemini's free API tier. Your laptop can be off. No OpenAI key,
paid server, paid database, Actions artifact storage, or paid fallback is used.
The local `serve` command remains available but is not needed for cloud hosting.

## Activate once

1. Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/api-keys).
   Use a project on the **Free tier**, without a linked billing account. Do not
   enable paid billing or upgrade the project. The app cannot inspect your billing
   status: a key from a paid project could incur charges despite these limits.
2. Install requirements and generate your private encryption key:

   ```powershell
   pip install -r requirements.txt
   python agent.py init
   python cloud.py keygen
   ```

   `keygen` writes `private/cloud-key.txt` and refuses to overwrite it. Keep a secure
   backup: losing this key means losing access to the encrypted cloud history.
3. Edit `private/profile.json`. Put your skills, education, experience, preferences,
   graduation date and work eligibility there. Use structured career facts; omit
   contact details, identity numbers, addresses and other sensitive information.
   Keep `profile.example.json` as a blank public template.
4. In your **public** GitHub repository, open **Settings > Secrets and variables >
   Actions > Secrets**. Add:

   | Secret | Contents |
   | --- | --- |
   | `GEMINI_API_KEY` | The free-tier project API key |
   | `AGENT_STATE_KEY` | Contents of `private/cloud-key.txt` |
   | `AGENT_PROFILE_JSON` | Contents of `private/profile.json` |
   | `TELEGRAM_BOT_TOKEN` | Existing Telegram bot token |
   | `TELEGRAM_CHAT_ID` | Your private chat ID, not a group ID |

   Send `/start` to the bot first. See README.md for creating a bot and finding
   your chat ID. Secrets belong in GitHub's secret store, never in config.json,
   the public example profile, commits, or chat messages here.
5. Under **Variables**, create `AGENT_ENABLED` with value `true`. The new workflow
   will then run and the older scan workflow will skip its job, avoiding duplicate
   alerts. Optionally set `GEMINI_MODEL`; the default is `gemini-3.5-flash-lite`.
   Check that any replacement model is supported on your project's free tier.
6. Commit and push the implementation files, then open **Actions > Free cloud job
   agent > Run workflow**. It must be on your default branch for scheduling.
   Allow Actions **Read and write permissions** under repository Actions settings
   if the workflow cannot push state. Branch rules may also need to allow the bot.
7. Check that the run succeeds and `data/agent-state.enc` appears in the repository.
   You should receive a daily digest on the first run. The bot is now hosted by
   GitHub; your laptop does not need to stay on.

If using the GitHub CLI, these commands read secrets from local files without
putting the values directly into command arguments:

```powershell
Get-Content -Raw private/cloud-key.txt | gh secret set AGENT_STATE_KEY
Get-Content -Raw private/profile.json | gh secret set AGENT_PROFILE_JSON
gh secret set GEMINI_API_KEY
gh variable set AGENT_ENABLED --body true
```

The API-key command prompts for the value. Add Telegram secrets if they are not
already configured. Running `keygen` and editing local files does not itself
configure GitHub or activate the hosted agent.

## What runs automatically

- Telegram messages are checked around every 15 minutes (`:07`, `:22`, `:37`, `:52`).
  GitHub can delay scheduled runs; replies are periodic rather than instant.
- Employer feeds are scanned when at least two hours have passed since the last
  successful cloud scan. Failed scans preserve listings and retry next run.
- A daily digest compares the top five keyword candidates with your profile.
  It batches uncached assessments into one Gemini request and ranks their scores.
- Between digests, newly entering shortlist candidates get keyword alerts, up to
  five per successful scan. These are shortlist notifications, not a promise to
  alert on every newly posted or reopened role.
- At most ten queued Telegram updates are processed in one run. Other messages
  remain queued. Telegram generally retains updates for no longer than 24 hours,
  so a prolonged hosting outage may lose unanswered messages.

The default AI limits are **15 requests per UTC day** and **six per run**. Calls
are spaced at least 13 seconds apart in a process. A quota response pauses AI for
one hour; nothing switches to a paid provider or retries in a loop. Requests that
fail or time out still count against the local allowance. Quotas provided by Google
can be lower than this app's limits and may change. A natural-language question
can use multiple requests; cached assessments and explicit commands conserve them.

These controls limit usage; **remaining on an unbilled free-tier project is what
prevents API charges**. The workflow skips private repositories to avoid private
runner-minute charges. It uses no Actions cache or artifacts. GitHub may disable
public scheduled workflows after 60 days without qualifying repository activity.
The state commits generally create activity, but check Actions periodically.
Free services have no uptime or capacity guarantee.

## Telegram commands

```text
/top backend
/job JOB_ID
/assess JOB_ID
/digest
/status JOB_ID shortlisted
/status JOB_ID applied Applied through the employer website
/status JOB_ID interviewing
/status JOB_ID hidden No longer interested
/applications
/draft JOB_ID
/forget
/help
```

Use the exact ID from `/top`. You can also ask questions naturally, such as
"Compare these two jobs against my skills". The model can search jobs, retrieve
cached details, assess fit, and read your application pipeline. Status updates and
draft creation require `/status` and `/draft` commands so untrusted job descriptions
cannot authorize writes. Applications are never automatically submitted.

When AI is unavailable, natural-language chat and daily digests return keyword
matches. `/assess` and `/draft` report unavailable instead of inventing results.
`/top`, `/job`, `/applications`, `/status`, and `/forget` work without AI.

AI scores are judgments of evidenced fit, not hiring probabilities. The digest
assesses the shortlist rather than every job. Missing descriptions and unknown
eligibility should be reported as uncertainties. Existing tracker eligibility rules
still control which early-career Singapore jobs enter the database. Always check
last-seen dates and employer application pages.

## Private state and recovery

Profile JSON stays in GitHub Secrets. The workflow encrypts application notes,
conversation history, model-request counters, drafts, cached descriptions and
assessments into `data/agent-state.enc` using authenticated Fernet encryption.
Only ciphertext is committed. Public `docs/jobs.json` and CSV keep their original
allowlist of job fields. Plaintext local files stay in Git-ignored `private/`.

The encrypted state persists across runs without a database service. It is rewritten
only when its contents change. Keep `AGENT_STATE_KEY` private; repository history
retains previous encrypted snapshots. Someone with both the key and repository
access could read those snapshots. This is not protection against a compromised
GitHub account or malicious code changes in the workflow.

A bad key or missing initialized state fails rather than silently resetting your
applications. Restore the original key or restore `data/agent-state.enc` from Git.
Do not delete `data/agent-state.initialized` to bypass recovery unless you deliberately
want a fresh agent. There is an 8 MiB uncompressed state limit; disposable assessments
are capped at 250 and closed-job description caches are pruned. Applications and
drafts are retained. If the size limit is reached, export/trim old data before running.

Only one workflow uses the state at a time. Do not run a local bot against the same
Telegram token while cloud mode is enabled. A webhook must not be active for the
bot: remove an existing webhook if polling fails. Failures after sending a reply but
before saving/pushing state can cause duplicate replies or drafts on a later run.
Push failures appear as failed Actions runs; resolve them before relying on the bot.

The free Gemini service may use submitted content to improve Google products.
The app excludes top-level name, contact fields and raw `cv_text` from structured
profile requests, but sends your selected career facts, relevant jobs and recent
conversation. Avoid including sensitive data in those fields or chat messages.
It retains the last 20 conversation messages; `/forget` clears active history,
not old encrypted Git snapshots. Telegram also processes messages sent there.

## Local use and testing

```powershell
$env:GEMINI_API_KEY = "your-free-tier-key"
python agent.py chat
python agent.py scan
python agent.py digest
python agent.py draft-export 1
python -m unittest -v
```

`draft-export` writes `private/draft-1.md` for an existing local draft ID. Cloud
drafts are returned in Telegram; they are not automatically downloaded to the
local database. Local and cloud databases are separate unless explicitly restored.
Set `JOB_AGENT_HOME` or `--home` to change local storage; ignore any custom directory
inside the repository before using it.

The test suite mocks API and Telegram requests. It covers tool signatures, request
limits, quota fallback, batch caching, private/public boundaries, encrypted state
round trips, wrong-key recovery, scheduling and Telegram authorization. Live API
and hosted operation still require your accounts and secrets.

References: [Gemini free-tier pricing](https://ai.google.dev/gemini-api/docs/pricing),
[function calling](https://ai.google.dev/gemini-api/docs/function-calling),
[GitHub Actions billing](https://docs.github.com/en/actions/concepts/billing-and-usage),
and [scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
