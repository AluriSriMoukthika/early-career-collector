# Early-career collector (v2)

Every morning it checks the public job feeds of the companies in `ats_sources.csv` (Greenhouse, Lever, Ashby, Workday). It reports **new US** postings in four buckets:

| Bucket | Meaning | Where it appears |
|---|---|---|
| **Confirmed** | Early-career according to the company's own career-level data (e.g. `Employment Type = Intern`, `Req Type = University - New Grad`, Workday `workerSubType = Intern`), the title, or a stated requirement of 0–1 years | Email + report, grouped by type |
| **Needs review** | Plausibly early-career but uncertain (level IC2/3, "2+ years", "recent graduates welcome") | Email + report, with the reason |
| **Unclassified** | No level information at all | Count in email; full list in `reports/<date>-unclassified.csv` |
| **Excluded** | Co-op (always), senior/staff/lead/manager/director titles or levels, or 3+ years required | Counts only |

Types: Internship, New Graduate, Entry-Level, Early-Career Full-Time, Graduate Program, Rotational Program.

**History.** Every posting is remembered in `state/seen_jobs.json`, so nothing is reported twice. The first time a source is checked, its current postings go into `reports/baseline-<date>.md` as *existing* roles for you to review. They are never reported as new.

**Failures.** If any source fails, the report says **INCOMPLETE** or **RUN FAILED** and lists the failed sources. It never says "no new opportunities" for sources it couldn't check. If more than 25% of sources fail, the GitHub run is marked failed, and GitHub emails you about failed runs.

## Files
- `ats_collector.py`: the collector (`run`, `verify`, `discover` modes)
- `ats_sources.csv`: configured feeds, with the result of the last live check
- `US_Early_Careers_300_Companies.csv` + `update_master.py`: the master database and the script that syncs it with check results
- `visualping_pilot.csv`: pages proposed for Visualping (companies with no public feed)
- `scripts/schedule_guard.py`: makes the daily run start at about 8 AM New York time all year
- `scripts/save_history.sh`: commits history and reports back to the repo, retrying if needed
- `tests/`: 26 offline tests, including real Greenhouse postings captured on 2026-11-10

## Deploy to a private GitHub repo (about 15 minutes)

1. **Create the repo.** On github.com, click **New repository**, name it e.g. `early-career-collector`, choose **Private**, and leave "Add a README" unticked. Click **Create repository**.
2. **Upload the files.** Unzip `early_career_collector_v2.zip`. On the empty repo page, click **uploading an existing file** and drag in *everything inside* the unzipped folder. That includes the hidden `.github` folder: on a Mac, press **Cmd+Shift+.** in Finder to show hidden files; on Windows, View → Show → Hidden items. Click **Commit changes**.
   Check that `.github/workflows/daily.yml` appears in the repo. If it doesn't, click **Add file → Create new file**, type `.github/workflows/daily.yml` as the name, and paste in the file's contents.
3. **Allow the workflow to save history.** Go to **Settings → Actions → General → Workflow permissions**, select **Read and write permissions**, and click **Save**.
4. **Email (optional; skip for the first test).** Go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `SMTP_HOST` = `smtp.gmail.com`
   - `SMTP_USER` = your Gmail address
   - `SMTP_PASS` = a Gmail **App Password**, not your normal password. Create one at Google Account → Security → 2-Step Verification → App passwords.
   - `REPORT_TO` = the address that should receive reports

   Secrets are encrypted and never appear in files or logs.
5. **First live verification (safe, read-only).** Go to **Actions** → **early-career-daily** → **Run workflow** → mode **verify** → **Run workflow**. This:
   - runs the unit tests,
   - calls each feed once,
   - writes `source_status.csv`, where each source is `WORKING`, `WORKING_EMPTY`, `FAILED` or `UNTESTED`,
   - updates the master CSV and commits both files.

   It sends no email and does not touch job history. Open `source_status.csv` in the repo. For any `FAILED` row, set `enabled` to `0` in `ats_sources.csv`, or fix its board name.
6. **First collection (baseline).** Run the workflow again with mode **run**. Review `reports/baseline-<date>.md`; if email is set up, it arrives with a subject starting "Baseline". Nothing in it is labelled new.
7. **Automatic daily runs.** Nothing to do. The workflow fires at 11:50, 12:50 and 13:50 UTC. The schedule guard lets only the first one that lands between 07:40 and 12:00 New York time run, so it starts at about 07:50 New York time all year. If GitHub starts a run late or drops one, the next trigger covers it.
8. **Check the first scheduled run** the next morning: go to **Actions**. A green run means it worked. The run's **Summary** page shows the report, and a new `reports/<date>.md` file appears in the repo.

GitHub pauses scheduled workflows in repos with no activity for 60 days. The daily history commit counts as activity, so this won't happen as long as runs keep succeeding.

## Adding companies (towards all 300)
- **Discover:** go to Actions → Run workflow → mode **discover**. It tries likely board names for every company in the master CSV that isn't configured yet, and writes `discovered_sources.csv`. Only rows with `name_match = yes` are safe to add. Copy them into `ats_sources.csv` with `enabled=1` and `live_status=UNTESTED`, then run **verify**.
- **Workday companies:** find the company's careers site URL in the form `https://<tenant>.wd<N>.myworkdayjobs.com/<site>`, then add a row `Company,workday,<tenant>,<tenant>.wd<N>.myworkdayjobs.com,<site>,1,UNTESTED,,,`.
- **No public feed** (e.g. Google, Amazon, JPMorgan): use Visualping. See `visualping_pilot.csv`.

## Settings (environment variables)
`WORKDAY_DETAIL_CAP` (default 150): description lookups per Workday company per run. Postings over the cap are deferred to the next run, not dropped.
`FAIL_EXIT_RATIO` (default 0.25): share of failed sources that marks the run as failed.
`EMAIL_EMPTY_STATUS` (default 1): send a short status email even when nothing is new.

## Running the tests locally
`python -m unittest discover -s tests -v` (Python 3.9+, no extra packages needed)
