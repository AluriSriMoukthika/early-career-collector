#!/usr/bin/env python3
"""Merge collector verification results into US_Early_Careers_300_Companies.csv.

Sources of truth (most recent wins):
  ats_sources.csv       - configured feeds with live_status WORKING / FAILED / UNTESTED
  source_status.csv     - written by `ats_collector.py verify` (optional; overrides live_status)
  state/sources_state.json - last successful daily run per feed (optional)
  visualping_pilot.csv  - pages proposed for Visualping
Never marks anything as working unless a check actually succeeded.
"""
import csv, json, sys
from pathlib import Path

HERE = Path(__file__).parent
MASTER = HERE / "US_Early_Careers_300_Companies.csv"

def rows(p):
    return list(csv.DictReader(open(p, newline="", encoding="utf-8"))) if p.exists() else []

def main():
    master = rows(MASTER)
    fields = list(master[0].keys())
    feeds = {}
    for r in rows(HERE / "ats_sources.csv"):
        feeds.setdefault(r["company"], []).append(r)
    status = {(r["ats"].lower(), r["slug"]): r for r in rows(HERE / "source_status.csv")}
    sstate = json.loads((HERE / "state/sources_state.json").read_text()) if (HERE / "state/sources_state.json").exists() else {}
    vp = {}
    for r in rows(HERE / "visualping_pilot.csv"):
        vp.setdefault(r["company"].split(" (")[0], r)
    counts = {"feed_working": 0, "feed_failed": 0, "visualping": 0, "unverified": 0}
    for m in master:
        name = m["Company_Name"]
        fr = feeds.get(name, [])
        working, failed = [], []
        for f in fr:
            key = (f["ats"].lower(), f["slug"])
            st = status.get(key, {}).get("status") or f.get("live_status", "UNTESTED")
            checked = status.get(key, {}).get("checked") or f.get("last_checked", "")
            (working if st.startswith("WORKING") else failed if st == "FAILED" else []).append((f, checked, st))
        if working:
            f, checked, st = working[0]
            url = {"greenhouse": f"https://job-boards.greenhouse.io/{f['slug']}", "lever": f"https://jobs.lever.co/{f['slug']}",
                   "ashby": f"https://jobs.ashbyhq.com/{f['slug']}"}.get(f["ats"].lower(), f"https://{f['wd_host']}/{f['wd_site']}")
            m["Direct_Job_Listings_URL"] = m["Direct_Job_Listings_URL"] or url
            m["ATS_Platform"] = f"{f['ats'].capitalize()} (board: {f['slug']}) - VERIFIED LIVE"
            m["Monitoring_Method"] = "Collector: public ATS feed" + (" + Visualping" if name in vp else "")
            m["URL_Verification_Status"] = f"VERIFIED - feed returned jobs ({checked})" + ("" if st == "WORKING" else " but 0 jobs")
            m["Last_Checked_Date"] = checked.split(" ")[0]
            ss = sstate.get(f"{f['ats'].lower()}:{f['slug']}", {})
            m["Last_Successful_Check"] = ss.get("last_ok", m["Last_Checked_Date"])
            m["Monitoring_Status"] = "Active - collector" if ss.get("baselined") else "Ready - collector (awaiting first GitHub run)"
            m["Monitoring_Suitability"] = "High"
            counts["feed_working"] += 1
        elif failed:
            f, checked, _ = failed[0]
            m["ATS_Platform"] = f"{f['ats'].capitalize()} (board: {f['slug']}) - FAILED live check"
            m["Monitoring_Status"] = "Feed failed - needs new URL"
            m["Last_Checked_Date"] = checked.split(" ")[0]
            m["Notes"] = (f.get("notes", "") + " | " + m["Notes"]).strip(" |")
            counts["feed_failed"] += 1
        if name in vp:
            v = vp[name]
            if not working:
                m["Monitoring_Method"] = "Visualping (no public structured feed found)"
                m["Monitoring_Status"] = "Visualping pilot - proposed, NOT created"
                m["Direct_Job_Listings_URL"] = v["url"]
                m["URL_Verification_Status"] = "VERIFIED - page checked in browser 2026-11-10" if "NOT YET" not in v["page_verified"] else "PARTIAL - link from official page"
                m["Last_Checked_Date"] = "2026-11-10"
            counts["visualping"] += 1
        elif not working and m["Monitoring_Status"].startswith(("PILOT", "Not started")):
            # earlier Visualping pilot proposals now superseded by feeds, or still unverified
            if m["Monitoring_Status"].startswith("PILOT"):
                m["Monitoring_Status"] = "Not started - needs verification"
        if not working and name not in vp and m["Monitoring_Status"] == "Not started - ready for collector":
            m["Monitoring_Status"] = "Page verified - no feed configured yet (Workday / Visualping candidate)"
        if not working and name not in vp and not m["URL_Verification_Status"].startswith(("VERIFIED", "PARTIAL")):
            counts["unverified"] += 1
    with open(MASTER, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields); w.writeheader(); w.writerows(master)
    print(counts)

if __name__ == "__main__":
    sys.exit(main())
