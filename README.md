# Early-career collector (v3)

Checks the public job feeds of the companies in `ats_sources.csv` (Greenhouse, Lever, Ashby, Workday) **five times a day**, at about 08:00, 11:00, 14:00, 17:00 and 20:00 New York time. It reports **new, relevant, early-career US postings** as a GitHub issue, and GitHub emails it to you.

## How a posting is judged
1. **Career level** (unchanged): internships, new grad, entry-level, junior/associate, SWE I / SDE I, graduate and rotational programs, and roles requiring 0-2 years are kept. **Co-ops are always excluded**, as are senior/staff/lead/principal/manager/director roles, roles requiring 3+ years, and military transition programs (SkillBridge etc.).
2. **Relevance to your profile** (`relevance.py`), judged from the title and the **full description**:
   - **Target families:** Analytics/BI, Data Science, ML/AI/GenAI/LLM/agentic, Computer Vision, Software Engineering/SDE, Data Engineering, AI Automation, Digital Transformation, Research (BS/MS accepted), IT/Technical/Systems Analyst.
   - **Skill evidence:** Python, SQL, PyTorch, scikit-learn, OpenCV, LangChain/LangGraph, LLMs, Spark/Airflow/dbt, Tableau/Power BI, Git, Linux, Docker/Kubernetes, cloud, MLOps, statistics and more. No job has to list them all.
   - **Unrelated work is rejected:** welding, assembly, manufacturing or mechanical technicians, warehouse, drivers, sales, retail, customer service, admin, HR/recruiting, accounting, legal and clinical roles. Industry doesn't matter: a data role at a factory or hospital is kept.
   - **PhD-only roles are excluded**, unless the posting accepts a BS/MS or equivalent experience.
   - **Work authorization** (`relevance.auth_assessment`):

     | Posting says | Result |
     |---|---|
     | "Must be a U.S. citizen", "U.S. citizenship required", "U.S. citizens/nationals only" | **Excluded — Citizenship/Clearance** |
     | "Active Secret/TS/TS-SCI clearance required", "must be eligible to obtain a … clearance" (U.S. clearances require citizenship) | **Excluded — Citizenship/Clearance** |
     | "No visa sponsorship", "unable to sponsor", "sponsorship not available" | Kept: **Included — No Sponsorship** |
     | "Must be authorized to work in the U.S.", "existing U.S. work authorization" | Kept: **Included — Existing U.S. work authorization required** |
     | "U.S. person", export control, ITAR, "permanent work authorization", "security screening", "background check", "citizen **or** permanent resident", clearance only "preferred" | Kept, moved to **Needs review** |
     | Nothing stated | Kept: "Not stated (unverified)" |

     "Background check" is very common. To stop it moving roles to review, set `REVIEW_ON_BACKGROUND_CHECK = False` in `relevance.py`.
3. **Result:**
   - **Confirmed** = early-career and relevant.
   - **Needs review** = relevant, but the level or fit is uncertain, e.g. "2 yrs" or no level stated.
   - Everything else isn't reported. Postings with no level information and weak relevance stay in the run's unclassified CSV.

## The report (GitHub issue)
Sections, in this order:
1. Atlanta area (Atlanta, Alpharetta, Sandy Springs, Dunwoody, Roswell, Marietta, Smyrna, Kennesaw, Duluth, Norcross, Peachtree Corners, Johns Creek, Decatur …)
2. Elsewhere in Georgia
3. Elsewhere in the US
4. Needs review

Each job's own location decides its section, not the company's headquarters.

Each entry shows the title, company, location, employment type, category, career level, experience required, education, posting date, work-authorization note, why it matches, and the direct apply link. There are up to 10 roles per company per section; the rest are listed in the run's CSV.

**No issue is created when a check finds nothing new and relevant.** Failed sources, a crash, or a feed suddenly returning 0 jobs *always* create an issue. Report issues older than 7 days are closed automatically.

**History:** every posting is remembered in `state/seen_jobs.json`, shared across all five daily checks, so nothing is reported twice. A source's first check records its existing openings as a baseline (`reports/baseline-*.md`) and never reports them as new. Only postings published within the last 24 hours are reported; the window widens automatically after a missed check. History older than 120 days is pruned; such postings are far too old to pass the 24-hour rule anyway.

## Schedule and GitHub usage
- **Triggers:** cron `7 0,1,12,13,15,16,18,19,21,22 * * *`, i.e. both the winter and summer UTC conversions of each slot. `scripts/schedule_guard.py` runs each slot at most once.
- **Backup:** the second trigger of each pair acts as a backup about 1 hour (summer) or 2 hours (winter) later. GitHub can delay or drop scheduled runs, so times are approximate.
- **Estimated usage:** about 5 real checks × 4–6 min plus 5 skipped triggers × about 1 billed minute each. That's roughly **25–35 min/day, or 750–1,100 min/month**, within the 2,000 free minutes/month for private repos on GitHub Free. Check it under **Settings → Billing**.
- **Requests:** about 150 public API requests per check, most of them Workday pages.
- **Repo growth:** up to 5 small history commits a day.

## Files
- `ats_collector.py`: the collector (`run`, `verify`, `discover` modes)
- `ats_sources.csv`: configured feeds, with the result of the last live check
- `US_Early_Careers_300_Companies.csv` + `update_master.py`: the master database and the script that syncs it with check results
- `visualping_pilot.csv`: pages proposed for Visualping (companies with no public feed)
- `relevance.py`: the job-description relevance filter (edit its lists to tune it)
- `atlanta_candidates.csv`: Atlanta-area employers with Workday tenant guesses for the discover step
- `scripts/schedule_guard.py`: runs each of the five daily check slots once (handles daylight saving time)
- `scripts/post_issue.py`: posts each daily report as an issue assigned to you, and closes the previous day's report issue
- `scripts/save_history.sh`: commits history and reports back to the repo, retrying if needed
- `tests/`: 26 offline tests, including real Greenhouse postings captured on 2026-11-10

## Deploy to a private GitHub repo (about 15 minutes)

1. **Create the repo.** On github.com, click **New repository**, name it e.g. `early-career-collector`, choose **Private**, and leave "Add a README" unticked. Click **Create repository**.
2. **Upload the files.** Unzip `early_career_collector_v2.zip`. On the empty repo page, click **uploading an existing file** and drag in *everything inside* the unzipped folder. That includes the hidden `.github` folder: on a Mac, press **Cmd+Shift+.** in Finder to show hidden files; on Windows, View → Show → Hidden items. Click **Commit changes**.
   Check that `.github/workflows/daily.yml` appears in the repo. If it doesn't, click **Add file → Create new file**, type `.github/workflows/daily.yml` as the name, and paste in the file's contents.
3. **Allow the workflow to save history.** Go to **Settings → Actions → General → Workflow permissions**, select **Read and write permissions**, and click **Save**.
4. **Daily report delivery (no passwords).** Each run posts the report as an issue in this repo, assigned to you. GitHub emails it to you from notifications@github.com. To make sure the emails arrive, check these settings:
   - **github.com/settings/notifications:** your **Default notification email** is an address you read. Under **Participating, @mentions and custom**, tick **Email**.
   - **This repo page → Watch:** set it to **All Activity**, or to **Custom** with **Issues** ticked.
   - To check it all works, go to Actions → Run workflow → mode **test-notification**. This posts a clearly labelled sample issue.
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
- **Discover:** go to Actions → Run workflow → mode **discover**. It also tries to find Workday career sites for the companies in `atlanta_candidates.csv`. Atlanta-area employers are probed first. Results are written as each company finishes, and a run stops starting new companies after 40 minutes (`DISCOVER_MAX_MINUTES`). Run **discover** again to continue: `state/discover_progress.json` remembers which companies are done. It tries likely board names for every company in the master CSV that isn't configured yet, and writes `discovered_sources.csv`. Only rows with `name_match = yes` are safe to add. Copy them into `ats_sources.csv` with `enabled=1` and `live_status=UNTESTED`, then run **verify**.
- **Workday companies:** find the company's careers site URL in the form `https://<tenant>.wd<N>.myworkdayjobs.com/<site>`, then add a row `Company,workday,<tenant>,<tenant>.wd<N>.myworkdayjobs.com,<site>,1,UNTESTED,,,`.
- **No public feed** (e.g. Google, Amazon, JPMorgan): use Visualping. See `visualping_pilot.csv`.

## Settings (environment variables)
`MAX_AGE_HOURS` (default 24): only postings published within this many hours are emailed. If a daily run was missed, the window widens to cover the gap. Older postings detected for the first time stay in the daily CSV, marked `OLDER_THAN_WINDOW`.
`EMAIL_CAP_PER_COMPANY` (default 10): rows per company in the email; the rest are counted ("+N more … in today's CSV") and listed in the CSV.
`WORKDAY_DETAIL_CAP` (default 150): description lookups per Workday company per run. Postings over the cap are deferred to the next run, not dropped.
`FAIL_EXIT_RATIO` (default 0.25): share of failed sources that marks the run as failed.

## Running the tests locally
`python -m unittest discover -s tests -v` (Python 3.9+, no extra packages needed)
