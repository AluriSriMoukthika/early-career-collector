#!/usr/bin/env python3
"""Post the daily report as a GitHub issue in this repository, assigned to the repo owner.

GitHub then emails the owner (notifications@github.com), so no SMTP credentials are needed.
It uses the workflow's built-in GITHUB_TOKEN (needs `issues: write`).

Usage (inside GitHub Actions):
  python scripts/post_issue.py report            # today's report (or a 'collector crashed' notice)
  python scripts/post_issue.py sample            # test issue for checking notifications
Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_REPOSITORY_OWNER, GITHUB_SERVER_URL, GITHUB_RUN_ID
"""
import datetime as dt, json, os, sys, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
NY = ZoneInfo("America/New_York")
TITLE_PREFIX = "Early-career report"
MAX_BODY = 60000          # GitHub's hard limit is 65,536 characters

def api(method, path, payload=None, token=None):
    url = "https://api.github.com" + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token or os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "early-career-collector"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
        return json.loads(body) if body else {}

def truncate(body, link):
    if len(body) <= MAX_BODY:
        return body
    cut = body[:MAX_BODY].rsplit("\n", 1)[0]
    return cut + f"\n\n*Report shortened to fit GitHub's issue size limit. Full report: {link}*\n"

def build_report_issue(date, repo, owner, run_url, server="https://github.com"):
    blob = f"{server}/{repo}/blob/main/reports"
    md_path, sm_path = REPORTS / f"{date}.md", REPORTS / f"{date}-summary.json"
    if not md_path.exists() or not sm_path.exists():
        title = f"{TITLE_PREFIX} {date} - COLLECTOR DID NOT FINISH"
        body = (f"@{owner} the collector did not produce a report today, so this is **not** a statement that "
                f"nothing new was posted.\n\nOpen the run log to see what went wrong: {run_url}\n")
        return title, body
    sm = json.loads(sm_path.read_text())
    tag = ""
    if sm["ok"] == 0:
        tag = " [RUN FAILED]"
    elif sm["failed"]:
        tag = f" [{sm['failed']} sources failed]"
    title = f"{TITLE_PREFIX} {date} - {sm['new']} new, {sm['review']} to review{tag}"
    links = [f"Full list (CSV): {blob}/{date}.csv"]
    if (REPORTS / f"{date}-unclassified.csv").exists():
        links.append(f"Unclassified postings: {blob}/{date}-unclassified.csv")
    if (REPORTS / f"baseline-{date}.md").exists():
        links.append(f"Existing openings recorded today (not new): {blob}/baseline-{date}.md")
    links.append(f"Run log: {run_url}")
    body = md_path.read_text() + "\n---\n" + "\n".join(f"- {l}" for l in links) + f"\n\ncc @{owner}\n"
    return title, truncate(body, f"{blob}/{date}.md")

def build_sample_issue(owner, run_url):
    title = f"{TITLE_PREFIX} - TEST NOTIFICATION (sample data, not real jobs)"
    body = "\n".join([
        f"@{owner} this is a **test** of the daily report notification. The rows below are **sample data**, not real postings.",
        "", "**Run status: OK - all 53 sources checked.**", "",
        "## New - Internship (1)", "",
        "| Company | Opportunity | Type | Location | Posted | Date Detected | Application Link |",
        "|---|---|---|---|---|---|---|",
        "| ExampleCo | Data Science Intern (Summer 2027) - SAMPLE | Internship | New York, NY | today | today | https://example.com/sample |",
        "", "## Needs your review (1) - level uncertain", "",
        "| Company | Opportunity | Type | Location | Posted | Date Detected | Application Link | Why |",
        "|---|---|---|---|---|---|---|---|",
        "| ExampleCo | Data Analyst - SAMPLE | Entry-Level | Remote - US | today | today | https://example.com/sample2 | requires 2 yrs experience |",
        "", f"Run log: {run_url}", "",
        "If this arrived by email, notifications are working. You can close this issue."])
    return title, body

def close_previous(repo, keep_number):
    """Close earlier open report issues so the Issues tab only shows the latest one."""
    closed = 0
    for it in api("GET", f"/repos/{repo}/issues?state=open&per_page=100&creator=github-actions%5Bbot%5D"):
        if it.get("pull_request") or it["number"] == keep_number or not it["title"].startswith(TITLE_PREFIX):
            continue
        if "TEST NOTIFICATION" in it["title"]:
            continue
        api("PATCH", f"/repos/{repo}/issues/{it['number']}", {"state": "closed", "state_reason": "completed"})
        closed += 1
    return closed

def main(argv):
    mode = argv[1] if len(argv) > 1 else "report"
    repo, owner = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_REPOSITORY_OWNER"]
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server}/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    if mode == "sample":
        title, body = build_sample_issue(owner, run_url)
    else:
        date = dt.datetime.now(NY).date().isoformat()
        title, body = build_report_issue(date, repo, owner, run_url, server)
    issue = api("POST", f"/repos/{repo}/issues", {"title": title, "body": body, "assignees": [owner]})
    print(f"Created issue #{issue['number']}: {issue.get('html_url')}")
    if mode != "sample":
        print(f"Closed {close_previous(repo, issue['number'])} earlier report issue(s)")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
