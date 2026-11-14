#!/usr/bin/env python3
"""Decides whether this workflow trigger should run the collector.

GitHub cron is UTC-only and scheduled runs can start late or be dropped. The workflow fires at
11:50, 12:50 and 13:50 UTC every day. In New York that is
  winter (EST, UTC-5): 06:50 / 07:50 / 08:50     summer (EDT, UTC-4): 07:50 / 08:50 / 09:50
This guard lets the FIRST trigger that lands inside the New York window [07:40, 12:00) run,
and skips the rest once state/last_run.json records today's date. A late or dropped trigger
is covered by the next one. Manual runs (workflow_dispatch) always run.
"""
import datetime as dt, json, os, sys
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
WINDOW_START = dt.time(7, 40)
WINDOW_END = dt.time(12, 0)
LAST = Path(__file__).resolve().parent.parent / "state" / "last_run.json"

def should_run(now_utc, last_run_date, event):
    if event == "workflow_dispatch":
        return True, "manual run"
    local = now_utc.astimezone(NY)
    if last_run_date == local.date().isoformat():
        return False, f"already ran today ({last_run_date})"
    if not (WINDOW_START <= local.time() < WINDOW_END):
        return False, f"outside 07:40-12:00 New York window (now {local:%H:%M %Z})"
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
