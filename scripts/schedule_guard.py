#!/usr/bin/env python3
"""Decides whether this workflow trigger should run the collector.

GitHub cron is UTC-only, and scheduled runs can start late or be dropped entirely (this
happened on 2026-11-15: no trigger fired at all). The workflow therefore fires every hour at :50
from 11:50 to 23:50 UTC (winter 06:50-18:50, summer 07:50-19:50 New York time).
This guard lets the FIRST trigger at or after 07:40 New York time run, and skips every later
trigger once state/last_run.json records today's date. There is no late cut-off: a late report
is better than none. Manual runs (workflow_dispatch) always run.
"""
import datetime as dt, json, os, sys
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
WINDOW_START = dt.time(7, 40)
LAST = Path(__file__).resolve().parent.parent / "state" / "last_run.json"

def should_run(now_utc, last_run_date, event):
    if event == "workflow_dispatch":
        return True, "manual run"
    local = now_utc.astimezone(NY)
    if last_run_date == local.date().isoformat():
        return False, f"already ran today ({last_run_date})"
    if local.time() < WINDOW_START:
        return False, f"before 07:40 New York time (now {local:%H:%M %Z})"
    return True, f"scheduled run at {local:%H:%M %Z}"

def mark_done(now_utc=None):
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    LAST.parent.mkdir(exist_ok=True)
    LAST.write_text(json.dumps({"date": now_utc.astimezone(NY).date().isoformat(),
                                "finished_utc": now_utc.isoformat(timespec="seconds")}))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "mark-done":
        mark_done(); sys.exit(0)
    last = json.loads(LAST.read_text()).get("date") if LAST.exists() else None
    ok, why = should_run(dt.datetime.now(dt.timezone.utc), last, os.environ.get("GITHUB_EVENT_NAME", ""))
    print(f"run={'true' if ok else 'false'} ({why})")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"run={'true' if ok else 'false'}\n")
