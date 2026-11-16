#!/usr/bin/env python3
"""Decides whether this workflow trigger should run the collector.

Target check times (America/New_York): 08:00, 11:00, 14:00, 17:00, 20:00.
GitHub cron is UTC-only and runs can be delayed or dropped, so the workflow fires at :07 past
BOTH the winter (UTC-5) and summer (UTC-4) conversions of every slot:
    cron "7 0,1,12,13,15,16,18,19,21,22 * * *"
In any season one trigger of each pair lands ~7 minutes after the slot; the other acts as a
backup about 1 hour later (summer) or about 2 hours later (winter, just before the next slot). The guard maps the trigger time to the most recent slot
and runs only if that slot has not run yet (state/last_run.json). Times are approximate.
Manual runs (workflow_dispatch) always run.

Because GitHub's own schedule can fail to fire (it never fired for this repo in Nov 2026), an outside
scheduler (e.g. cron-job.org) can start the workflow through the API with inputs
{"mode": "run", "source": "external"}. Those calls are treated exactly like scheduled triggers,
so each check slot still runs at most once.
"""
import datetime as dt, json, os, sys
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SLOTS = (8, 11, 14, 17, 20)
LAST = Path(__file__).resolve().parent.parent / "state" / "last_run.json"

def current_slot(now_utc):
    """Most recent slot at or before now, as 'YYYY-MM-DDTHH' in New York time."""
    local = now_utc.astimezone(NY)
    past = [h for h in SLOTS if local.hour >= h]
    if past:
        return f"{local.date().isoformat()}T{past[-1]:02d}"
    return f"{(local.date() - dt.timedelta(days=1)).isoformat()}T{SLOTS[-1]:02d}"   # before 08:00 -> yesterday 20:00

def should_run(now_utc, last_slot, event):
    slot = current_slot(now_utc)
    if event == "workflow_dispatch":
        return True, slot, "manual run"
    if last_slot == slot:
        return False, slot, f"slot {slot} already ran"
    if last_slot and last_slot > slot:
        return False, slot, f"a later slot ({last_slot}) already ran"
    local = now_utc.astimezone(NY)
    return True, slot, f"running slot {slot} at {local:%H:%M %Z}"

def read_last():
    if not LAST.exists():
        return None
    d = json.loads(LAST.read_text())
    return d.get("slot")            # older files only had "date" -> treated as no slot

def mark_done(slot, now_utc=None):
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    LAST.parent.mkdir(exist_ok=True)
    LAST.write_text(json.dumps({"slot": slot, "date": slot[:10], "finished_utc": now_utc.isoformat(timespec="seconds")}))

def main(argv=None, env=None, now_utc=None):
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    if len(argv) > 1 and argv[1] == "mark-done":
        mark_done(argv[2] if len(argv) > 2 else current_slot(now_utc), now_utc)
        return 0
    event = env.get("GITHUB_EVENT_NAME", "")
    if event == "workflow_dispatch" and env.get("GUARD_TRIGGER") == "external":
        event = "schedule"                  # outside scheduler: same once-per-slot rule as GitHub's schedule
    ok, slot, why = should_run(now_utc, read_last(), event)
    print(f"run={'true' if ok else 'false'} slot={slot} ({why})")
    out = env.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"run={'true' if ok else 'false'}\nslot={slot}\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
