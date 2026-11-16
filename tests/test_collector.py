import contextlib, csv, datetime as dt, io, json, re, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import ats_collector as c
import relevance as rel
import schedule_guard as g
import post_issue as pi

FIX = json.loads((ROOT / "tests/fixtures/greenhouse_real_sample.json").read_text())["jobs"]
NY = ZoneInfo("America/New_York")

def gh_job(j):
    """Convert a raw Greenhouse posting the same way fetch_greenhouse does."""
    meta = {m["name"]: m.get("value") for m in j.get("metadata") or []}
    return dict(id=j["id"], title=j["title"], url="u", location=j["location"]["name"], country="",
                posted=j["first_published"][:10], meta=meta, desc=c.strip_html(j.get("content", "")))

TECH = ("You will analyze data with Python and SQL, build machine learning models with scikit-learn and PyTorch, "
        "create dashboards in Tableau and apply statistics. Bachelor's or Master's in a quantitative field.")

def J(title, meta=None, desc=None, loc="New York, NY", jid=None):
    """Test posting. desc=None -> a realistic technical description (relevant to the profile)."""
    return dict(id=jid or title, title=title, url="https://x/" + title, location=loc, country="", posted="2026-11-09",
                meta=meta or {}, desc=TECH if desc is None else desc)

class TestRealGreenhouseSample(unittest.TestCase):
    """Expected outcomes for real postings captured live on 2026-11-10."""
    EXPECT = {
        8329280: ("CONFIRMED", "Early-Career Full-Time"),  # Robinhood 'Customer Experience Associate, Pathway' - no keyword, metadata EARLY TALENT
        8198241: ("CONFIRMED", "Internship"),
        8287392: ("EXCLUDED", ""),                        # Program Manager
        8334884: ("CONFIRMED", "Internship"),
        8119896: ("EXCLUDED", ""),                        # Lead, 10 yrs
        8881612002: ("CONFIRMED", "New Graduate"),
        8933791002: ("CONFIRMED", "Internship"),
        8350832: ("CONFIRMED", "Internship"),
        8044534: ("EXCLUDED", ""),
        7752395: ("REVIEW", "Entry-Level"),               # Dropbox IC2 + '2+ years' -> uncertain
        7172839: ("EXCLUDED", ""),                        # IC3, 5+ yrs
        8052050: ("CONFIRMED", "New Graduate"),           # HRT 'Full-Time: New Grad'
        7964062: ("CONFIRMED", "Internship"),
        8161770: ("EXCLUDED", ""),                        # 'Full-Time: Experienced'
        8209134: ("CONFIRMED", "Internship"),
        7217699: ("CONFIRMED", "Entry-Level"),            # 'Associate Recruiter' - title says associate; dept 'Early Talent Recruiting' ignored
        8192369: ("EXCLUDED", ""),                        # IC3 / 3-5 yrs
        8175471: ("CONFIRMED", "Internship"),
        8129321: ("EXCLUDED", ""),
        8257324: ("REVIEW", "Entry-Level"),               # 'Analyst' with 2-4 yrs
        6572669: ("UNCLASSIFIED", ""),                    # no level info at all
    }
    def test_expected(self):
        byid = {j["id"]: j for j in FIX}
        for jid, (st, typ) in self.EXPECT.items():
            got = c.classify(gh_job(byid[jid]))
            self.assertEqual((got[0], got[1]), (st, typ), f"{byid[jid]['title']}: {got}")

    def test_recruiter_not_tagged_by_department(self):
        j = gh_job(next(x for x in FIX if x["id"] == 7217699))
        self.assertNotIn("Early Talent Recruiting", c.classify(j)[2])

    def test_locations(self):
        byid = {j["id"]: j for j in FIX}
        self.assertTrue(c.is_us(byid[8334884]["location"]["name"]))      # San Francisco, CA, US
        self.assertFalse(c.is_us(byid[8138080]["location"]["name"]))     # Toronto, ON, CA  (CA = Canada)
        self.assertTrue(c.is_us(byid[8209134]["location"]["name"]))      # Austin; New York City; San Francisco
        self.assertFalse(c.is_us(byid[7172839]["location"]["name"]))     # Remote - Mexico
        self.assertTrue(c.is_us(byid[8052050]["location"]["name"]))      # London...; New York, NY, United States; Singapore
        self.assertFalse(c.is_us(byid[7217699]["location"]["name"]))     # Gurugram
        self.assertTrue(c.is_us(byid[8192369]["location"]["name"]))      # Remote - United States
        # Atlanta-area places written without a state (bug found 2026-11-15: Flock Safety showed 0 US jobs)
        for loc in ("Atlanta", "Alpharetta", "Sandy Springs", "Kennesaw; Remote", "Peachtree Corners", "Marietta, Georgia", "Savannah, GA"):
            self.assertTrue(c.is_us(loc), loc)
        self.assertTrue(c.is_us("Marietta, OH"))
        self.assertFalse(c.is_us("Toronto, ON, CA"))

class TestClassification(unittest.TestCase):
    def test_coop_always_excluded(self):
        for job in (J("Software Engineer Co-op (Spring 2027)", desc=""), J("Data Science Coop", desc=""),
                    J("Engineering Student", {"Employment Type": "Co-op"}, desc=""),
                    J("Engineering Intern", {"facet": "co-op"}, desc=""),
                    J("Cooperative Education Student - IT", desc="")):
            self.assertEqual(c.classify(job)[0], "EXCLUDED", job["title"])

    def test_coop_mention_in_description_is_flag_not_exclusion(self):
        st, typ, why = c.classify(J("Software Engineering Intern", desc="Co-ops are only available to students in a co-op program."))
        self.assertEqual((st, typ), ("CONFIRMED", "Internship")); self.assertIn("co-op", why)

    def test_keywordless_entry_roles_found(self):
        self.assertEqual(c.classify(J("Data Analyst", desc="0-1 years of relevant experience preferred"))[:2], ("CONFIRMED", "Entry-Level"))
        self.assertEqual(c.classify(J("Software Engineer", {"Job Level": "IC1"}, desc=""))[:2], ("CONFIRMED", "Entry-Level"))
        self.assertEqual(c.classify(J("Software Engineer", {"employment type": "Intern"}, desc=""))[:2], ("CONFIRMED", "Internship"))
        self.assertEqual(c.classify(J("Machine Learning Engineer", desc="1+ years of industry experience with Python"))[0], "CONFIRMED")
        self.assertEqual(c.classify(J("Business Analyst", desc="2+ years of experience in analytics"))[0], "REVIEW")
        self.assertEqual(c.classify(J("Software Engineer", desc="Open to recent graduates with a BS in CS."))[0], "REVIEW")
        self.assertEqual(c.classify(J("Software Engineer", desc="Build great things."))[0], "UNCLASSIFIED")

    def test_all_requested_types(self):
        cases = {"Technology Development Program Associate": "Graduate Program", "Finance Rotational Program Analyst": "Rotational Program",
                 "Summer Analyst 2027": "Internship", "Software Engineer, New Grad (2027)": "New Graduate",
                 "Junior Data Scientist": "Entry-Level", "Software Engineer - Early Career": "Early-Career Full-Time",
                 "Software Engineer I": "Entry-Level", "Graduate Development Program - Data": "Graduate Program", "Associate Product Manager": "Graduate Program"}
        for t, typ in cases.items():
            self.assertEqual(c.classify(J(t, desc=""))[:2], ("CONFIRMED", typ), t)

    def test_senior_excluded(self):
        for t in ("Senior Data Scientist", "Staff Software Engineer, New Grad Programs", "Engineering Manager, University Recruiting",
                  "Principal ML Engineer", "Product Manager", "Software Engineer III", "Lead Analyst", "Data Engineer 2"):
            self.assertEqual(c.classify(J(t, desc=""))[0], "EXCLUDED", t)
        self.assertEqual(c.classify(J("Associate", desc="5+ years of experience in consulting"))[0], "EXCLUDED")
        self.assertEqual(c.classify(J("Senior Associate", desc=""))[0], "EXCLUDED")

    def test_min_years(self):
        self.assertEqual(c.min_years("Requires 3-5 years of professional experience"), 3)
        self.assertEqual(c.min_years("0 to 2 years of experience"), 0)
        self.assertEqual(c.min_years("a 4-year degree; 1+ years experience"), 1)
        self.assertIsNone(c.min_years("Join our 10 year old company"))

    def test_required_years_uses_largest_required_and_ignores_preferred(self):
        # all required items must be met -> the largest one is the real requirement
        self.assertEqual(c.min_years("5+ years of experience in embedded systems. 1+ year of experience with Python."), 5)
        # SpaceX-style posting: basic 1 year, preferred 3+ -> requirement is 1
        spacex = ("BASIC QUALIFICATIONS: Bachelor's degree in computer science; 1+ years of professional experience "
                  "programming in C++. PREFERRED SKILLS AND EXPERIENCE: 3+ years of experience developing software.")
        self.assertEqual(c.min_years(spacex), 1)
        self.assertEqual(c.classify(J("Application Software Engineer", desc=spacex))[:2], ("CONFIRMED", "Entry-Level"))
        mixed = "Requirements: 1+ years of SQL experience and 4+ years of professional analytics experience."
        self.assertEqual(c.classify(J("Data Analyst", desc=mixed))[0], "EXCLUDED")
        self.assertIsNone(c.min_years("Preferred: 2+ years of experience with Kubernetes"))   # only a preference

    def test_apprenticeship_is_entry_level_not_internship(self):
        self.assertEqual(c.classify(J("Apprentice Welder", desc=""))[:2], ("CONFIRMED", "Entry-Level"))
        self.assertEqual(c.classify(J("Apprentice Controls Technician", desc=""))[:2], ("CONFIRMED", "Entry-Level"))

class TempState(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # never write test reports into the real GitHub run summary
        e = mock.patch.dict(os.environ); e.start(); self.addCleanup(e.stop)
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        for name, val in (("SEEN", self.tmp / "state/seen.json"), ("SRC_STATE", self.tmp / "state/src.json"), ("REPORTS", self.tmp / "reports")):
            p = mock.patch.object(c, name, val); p.start(); self.addCleanup(p.stop)
        self.now = dt.datetime(2026, 11, 10, 8, 0, tzinfo=NY)

class TestRunBehaviour(TempState):
    SRC = [dict(company="A", ats="greenhouse", slug="a"), dict(company="B", ats="lever", slug="b")]

    def test_first_run_is_baseline_then_new_only(self):
        jobs = {"a": [J("SWE Intern 2027", jid=1), J("Senior SWE", jid=2), J("Data Analyst", desc="0-1 years experience with SQL and Python", jid=3)],
                "b": [J("Software Engineer, New Grad", jid=9, loc="Toronto, ON, CA")]}
        r1 = c.run(self.SRC, self.now, fetch=lambda s, seen: jobs[s["slug"]])
        self.assertEqual(r1["new"], []); self.assertEqual(len(r1["baseline"]), 2)    # Canada role skipped
        md = c.write_report(r1)
        self.assertTrue((self.tmp / f"reports/baseline-{r1['stamp']}.md").exists())
        self.assertIn("recorded as baseline (not emailed): 2", md)
        self.assertNotIn("SWE Intern 2027", md)                                   # existing openings never in the email body
        self.assertEqual(c.email_subject(r1), "[Early careers] 0 new, 0 to review - 2026-11-10")
        jobs["a"].append(J("Machine Learning Intern", jid=4))
        r2 = c.run(self.SRC, self.now + dt.timedelta(days=1), fetch=lambda s, seen: jobs[s["slug"]])
        self.assertEqual([n["title"] for n in r2["new"]], ["Machine Learning Intern"]); self.assertEqual(r2["baseline"], [])
        r3 = c.run(self.SRC, self.now + dt.timedelta(days=2), fetch=lambda s, seen: jobs[s["slug"]])
        self.assertEqual(r3["new"], [])                                                # de-duplicated

    def test_only_postings_from_last_24h_are_emailed(self):
        c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [])                    # baseline (empty)
        now = self.now + dt.timedelta(days=1)
        def at(h): return (now - dt.timedelta(hours=h)).isoformat()
        jobs = [dict(J("Fresh Intern", jid=1), posted_ts=at(3)), dict(J("Day-old Intern", jid=2), posted_ts=at(23.5)),
                dict(J("Old Intern", jid=3), posted_ts=at(30)), dict(J("Undated Intern", jid=4)),
                dict(J("WD Today Intern", jid=5), posted_rel="Posted Today"), dict(J("WD Old Intern", jid=6), posted_rel="Posted 5 Days Ago")]
        r = c.run(self.SRC[:1], now, fetch=lambda s, seen: jobs)
        self.assertEqual(sorted(n["title"] for n in r["new"]), ["Day-old Intern", "Fresh Intern", "Undated Intern", "WD Today Intern"])
        self.assertEqual(sorted(n["title"] for n in r["older"]), ["Old Intern", "WD Old Intern"])
        self.assertIn("no posting date", next(n for n in r["new"] if n["title"] == "Undated Intern")["reason"])
        md = c.write_report(r)
        self.assertNotIn("**Old Intern**", md)                                       # older postings are not in the email body
        rows = list(csv.DictReader(open(self.tmp / f"reports/{r['stamp']}.csv")))
        self.assertEqual(sum(1 for x in rows if x["status"] == "OLDER_THAN_WINDOW"), 2)  # ...but are kept in the CSV

    def test_missed_day_widens_window(self):
        c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [])
        now = self.now + dt.timedelta(days=2)                                        # one daily run was missed
        job = dict(J("Gap Intern", jid=1), posted_ts=(now - dt.timedelta(hours=40)).isoformat())
        r = c.run(self.SRC[:1], now, fetch=lambda s, seen: [job])
        self.assertEqual([n["title"] for n in r["new"]], ["Gap Intern"])

    def test_email_capped_at_10_per_company(self):
        c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [])
        jobs = [J(f"Junior Data Analyst {i:02d}", jid=i) for i in range(25)]
        r = c.run(self.SRC[:1], self.now, fetch=lambda s, seen: jobs)
        self.assertEqual(len(r["new"]), 25)
        md = c.write_report(r)
        self.assertEqual(md.count("- **Junior Data Analyst"), 10)
        self.assertIn("+15 more A roles in today's CSV", md)
        self.assertEqual(len(list(csv.DictReader(open(self.tmp / f"reports/{r['stamp']}.csv")))), 25)   # nothing dropped

    def test_new_source_added_later_gets_its_own_baseline(self):
        c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [J("Intern A", jid=1)])
        r = c.run(self.SRC, self.now, fetch=lambda s, seen: [J("Intern " + s["slug"], jid=7)])
        self.assertEqual([n["company"] for n in r["new"]], ["A"]); self.assertEqual([n["company"] for n in r["baseline"]], ["B"])

    def test_failed_sources_never_look_like_no_opportunities(self):
        def boom(s, seen): raise ConnectionError("timeout")
        r = c.run(self.SRC, self.now, fetch=boom)
        md = c.write_report(r)
        self.assertIn("RUN FAILED", md); self.assertNotIn("No new early-career opportunities", md)
        self.assertIn("RUN FAILED", c.email_subject(r))
        def half(s, seen):
            if s["slug"] == "b": raise ConnectionError("x")
            return []
        r = c.run(self.SRC, self.now, fetch=half)
        md = c.write_report(r)
        self.assertIn("INCOMPLETE - 1 of 2 sources failed", md); self.assertIn("Sources that FAILED", md)

    def test_failed_source_is_not_baselined(self):
        def boom(s, seen): raise ConnectionError("x")
        c.run(self.SRC[:1], self.now, fetch=boom)
        r = c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [J("Intern", jid=1)])
        self.assertEqual(len(r["baseline"]), 1); self.assertEqual(r["new"], [])
        st = json.loads((self.tmp / "state/src.json").read_text())
        self.assertEqual(st["greenhouse:a"]["consecutive_failures"], 0)

    def test_zero_jobs_after_nonzero_warns(self):
        c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [J("Intern", jid=1)])
        r = c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [])
        self.assertTrue(any("returned 0 jobs" in w for w in r["warnings"]))

    def test_review_and_unclassified_routing(self):
        c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [])
        r = c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [
            J("Business Analyst", desc="2+ years of experience with SQL and Python", jid=1),   # level uncertain, relevant -> review
            J("Software Engineer", jid=2),                                                    # relevant, no level stated -> review
            J("Software Engineer", desc="Build great things for customers.", loc="", jid=3),  # no level, weak relevance -> unclassified
            J("TIG Welder", desc="Entry level. TIG and MIG welding; read blueprints.", jid=4)])  # unrelated -> excluded
        self.assertEqual(sorted(n["reason"].split(";")[0] for n in r["review"]), ["relevant role, experience level not stated", "requires 2 yrs experience"])
        self.assertEqual(len(r["unclassified"]), 1)
        self.assertEqual(r["excluded"].get("not relevant to profile"), 1)
        c.write_report(r)
        self.assertTrue((self.tmp / f"reports/{r['stamp']}-unclassified.csv").exists())
        self.assertIn("[US? unverified]", r["unclassified"][0]["location"])

    def test_deferred_postings_in_first_run_stay_baseline(self):
        def batch(n_deferred):
            jobs = [J("Engineering Intern", jid="a")]
            for i in range(n_deferred):
                d = J("Data Analyst " + "abc"[i], jid=f"d{i}"); d["meta"]["_detail_skipped"] = "yes"; jobs.append(d)
            return jobs
        r1 = c.run(self.SRC[:1], self.now, fetch=lambda s, seen: batch(3))           # day 1: 3 deferred
        self.assertEqual(len(r1["baseline"]), 1)
        self.assertTrue(any("stays in baseline mode" in w for w in r1["warnings"]))
        later = [J("Engineering Intern", jid="a"), J("Junior Analyst", jid="d0"), J("Junior Data Analyst", jid="d1")]
        r2 = c.run(self.SRC[:1], self.now, fetch=lambda s, seen: later)              # day 2: deferred ones processed
        self.assertEqual(r2["new"], []); self.assertEqual(len(r2["baseline"]), 2)   # still baseline, NOT new
        r3 = c.run(self.SRC[:1], self.now, fetch=lambda s, seen: later + [J("Data Intern", jid="z")])
        self.assertEqual([n["title"] for n in r3["new"]], ["Data Intern"])          # now genuinely new

    def test_deferred_detail_not_marked_seen(self):
        c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [])
        j = J("Analyst", jid="p1"); j["meta"]["_detail_skipped"] = "yes"
        c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [j])
        self.assertNotIn("greenhouse:a|p1", json.loads((self.tmp / "state/seen.json").read_text()))

    def test_main_exit_code_on_many_failures(self):
        src = self.tmp / "src.csv"
        src.write_text("company,ats,slug,wd_host,wd_site,enabled\nA,greenhouse,a,,,1\nB,lever,b,,,1\n")
        with mock.patch.dict(c.FETCHERS, {"greenhouse": mock.Mock(side_effect=ConnectionError("x")),
                                          "lever": mock.Mock(return_value=iter([]))}), \
             mock.patch.object(c, "maybe_email", return_value=False), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(c.main(["run", "--sources", str(src)]), 2)

class TestEndToEndCLI(TempState):
    def test_cli_run_with_real_greenhouse_payload(self):
        payload = json.dumps({"jobs": [{k: v for k, v in j.items() if k != "board"} | {"absolute_url": f"https://x/{j['id']}"}
                                       for j in FIX if j["board"] == "coinbase"]}).encode()
        class Resp:
            def __init__(s, b): s.b = b
            def read(s, *a): return s.b
            def __enter__(s): return s
            def __exit__(s, *a): pass
        calls = []
        def fake_urlopen(req, timeout=0):
            calls.append(req.full_url); return Resp(payload)
        src = self.tmp / "src.csv"
        src.write_text("company,ats,slug,wd_host,wd_site,enabled\nCoinbase,greenhouse,coinbase,,,1\nOff,greenhouse,off,,,0\n")
        with mock.patch.object(c.urllib.request, "urlopen", fake_urlopen), mock.patch.object(c, "maybe_email", return_value=False), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(c.main(["run", "--sources", str(src)]), 0)
        self.assertEqual(calls, ["https://boards-api.greenhouse.io/v1/boards/coinbase/jobs?content=true"])
        base = list((self.tmp / "reports").glob("baseline-*.md"))[0].read_text()
        self.assertIn("Analytics Engineer Intern", base)
        self.assertNotIn("Legal Operations Manager", base)
        latest = json.loads((self.tmp / "reports/latest.json").read_text())
        self.assertTrue((self.tmp / f"reports/{latest['stamp']}-summary.json").exists())

class TestVerifyLabels(unittest.TestCase):
    def test_disabled_rows_not_called_untested(self):
        t = Path(tempfile.mkdtemp())
        with mock.patch.object(c, "HERE", t), contextlib.redirect_stdout(io.StringIO()):
            out = c.verify([dict(company="A", ats="greenhouse", slug="a")],
                           [dict(company="A", ats="greenhouse", slug="a", enabled="1"),
                            dict(company="B", ats="greenhouse", slug="b", enabled="0", live_status="FAILED", notes="404", last_checked="2026-11-10"),
                            dict(company="C", ats="lever", slug="c", enabled="1")],
                           fetch=lambda s, seen: [J("Intern", jid=1)])
        self.assertEqual([o["status"] for o in out], ["WORKING", "DISABLED", "UNTESTED"])
        self.assertIn("FAILED", out[1]["error"])

class TestPostIssue(TempState):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(pi, "REPORTS", self.tmp / "reports"); p.start(); self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "1"}); e.start(); self.addCleanup(e.stop)

    def _report(self, fail=False):
        src = [dict(company="SpaceX", ats="greenhouse", slug="s"), dict(company="B", ats="lever", slug="b")]
        c.run(src, self.now, fetch=lambda s, seen: [])
        def f(s, seen):
            if fail and s["slug"] == "b": raise ConnectionError("down")
            atl = [J("Data Science Intern", jid=500, loc="Alpharetta, GA"), J("Software Engineer I", jid=501, loc="Savannah, GA")]
            return [J(f"Junior Data Analyst {i:02d}", jid=i) for i in range(12)] + atl + \
                   [J("Business Analyst", desc="2+ years of experience with SQL and Python", jid=99)]
        r = c.run(src, self.now, fetch=f)
        c.write_report(r)
        return r

    def test_report_issue_contents(self):
        self._report()
        title, body = pi.build_report_issue("2026-11-10", "me/repo", "me", "RUNURL")
        self.assertEqual(title, "Early-career report 2026-11-10 08:00 - 28 new, 2 to review")
        self.assertIn("- **Junior Data Analyst 00** - SpaceX", body)       # title + company
        self.assertIn("Apply: https://x/Junior Data Analyst 00", body)       # direct application link
        for field in ("Experience:", "Education:", "Work authorization:", "Why it matches:", "Posted:"):
            self.assertIn(field, body)
        self.assertIn("+2 more SpaceX roles", body)                          # 10-per-company cap (12 new)
        self.assertIn("Needs your review", body)
        idx = [body.index(h) for h in ("## 1. Atlanta area", "## 2. Elsewhere in Georgia",
                                       "## 3. Elsewhere in the United States", "## 4. Needs your review")]
        self.assertEqual(idx, sorted(idx))                                   # Atlanta, Georgia, US, then review
        self.assertIn("https://github.com/me/repo/blob/main/reports/2026-11-10_0800.csv", body)
        self.assertIn("cc @me", body)

    def test_failed_sources_in_title_and_body(self):
        self._report(fail=True)
        title, body = pi.build_report_issue("2026-11-10", "me/repo", "me", "RUNURL")
        self.assertIn("[1 sources failed]", title)
        self.assertIn("Sources that FAILED", body); self.assertIn("INCOMPLETE", body)

    def test_missing_report_is_not_silent(self):
        title, body = pi.build_report_issue("2026-11-10", "me/repo", "me", "RUNURL")
        self.assertIn("DID NOT FINISH", title); self.assertIn("not** a statement that nothing new", body)

    def test_truncation(self):
        long = "x\n" * 40000
        out = pi.truncate(long, "LINK")
        self.assertLess(len(out), 65536); self.assertIn("Full report: LINK", out)

    def test_main_creates_assigned_issue_and_closes_old_reports(self):
        self._report()
        calls = []
        def fake_api(method, path, payload=None, token=None):
            calls.append((method, path, payload))
            if method == "POST": return {"number": 7, "html_url": "u"}
            if method == "GET": return [{"number": 5, "title": "Early-career report 2026-11-09 - 0 new, 0 to review"},
                                        {"number": 6, "title": "Early-career report - TEST NOTIFICATION (sample data, not real jobs)"},
                                        {"number": 4, "title": "Unrelated issue"}, {"number": 3, "title": "PR", "pull_request": {}}]
            return {}
        env = {"GITHUB_REPOSITORY": "me/repo", "GITHUB_REPOSITORY_OWNER": "me", "GITHUB_RUN_ID": "1"}
        with mock.patch.object(pi, "api", fake_api), mock.patch.dict(os.environ, env), \
             mock.patch.object(pi.dt, "datetime", mock.Mock(now=lambda tz=None: self.now)), contextlib.redirect_stdout(io.StringIO()):
            pi.main(["x", "report"])
        post = calls[0]
        self.assertEqual((post[0], post[1]), ("POST", "/repos/me/repo/issues")); self.assertEqual(post[2]["assignees"], ["me"])
        self.assertEqual([c_[1] for c_ in calls if c_[0] == "PATCH"], ["/repos/me/repo/issues/5"])   # only the old report closed

    def test_sample_issue(self):
        calls = []
        env = {"GITHUB_REPOSITORY": "me/repo", "GITHUB_REPOSITORY_OWNER": "me", "GITHUB_RUN_ID": "1"}
        with mock.patch.object(pi, "api", lambda m, p, payload=None, token=None: calls.append((m, payload)) or {"number": 1}), \
             mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
            pi.main(["x", "sample"])
        self.assertEqual(len(calls), 1)
        self.assertIn("TEST NOTIFICATION", calls[0][1]["title"]); self.assertIn("sample data", calls[0][1]["body"])

class TestRelevanceRealisticDescriptions(TempState):
    """Realistic postings (paraphrased from typical ATS listings)."""
    ACCEPT = [
        ("Data Scientist I", "Develop predictive models in Python and SQL; use scikit-learn, pandas and PyTorch; communicate insights. "
                             "Bachelor's or Master's in Statistics, CS or related field; 0-2 years of experience.", "Data Science"),
        ("Software Development Engineer I", "Design, build and test scalable services in Java and Python; data structures and algorithms; "
                                            "REST APIs; Git and Linux. BS in Computer Science.", "Software Engineering"),
        ("Machine Learning Engineer - New Grad", "Train and deploy deep learning models with PyTorch; MLOps with Docker and Kubernetes; "
                                                 "Python. Master's degree in CS or related.", "Machine Learning / AI"),
        ("Computer Vision Engineer, Early Career", "Build image recognition and perception pipelines with OpenCV and PyTorch; "
                                                   "deep learning; Python and C++.", "Computer Vision"),
        ("Associate Data Engineer", "Build ETL data pipelines using Spark, Airflow and dbt on Snowflake; SQL and Python; "
                                    "1+ years of experience.", "Data Engineering"),
        ("Technical Analyst", "Translate business needs into technical requirements, write SQL queries against our databases, "
                              "build Power BI dashboards and support data analysis.", "IT / Technical Analyst"),
        ("Generative AI Engineer", "Build LLM applications and AI agents using LangChain and LangGraph; retrieval-augmented generation; "
                                   "Python; cloud deployment on AWS.", "Machine Learning / AI"),
        ("Business Intelligence Analyst", "Own dashboards in Tableau, write SQL, analyze trends and present insights; Excel.", "Analytics"),
        ("Research Engineer", "Implement ML research ideas in PyTorch. BS/MS in CS or equivalent practical experience; Python.", "Research"),
    ]
    REJECT = [
        ("TIG Welder - Entry Level", "Perform TIG and MIG welding on aluminum and stainless steel assemblies; read blueprints and weld "
                                     "symbols; use hand tools. High school diploma; 0-1 years of experience."),
        ("Mechanical Assembly Technician", "Assemble mechanical components using torque tools; follow work instructions; 0-1 years."),
        ("Manufacturing Technician I", "Operate CNC and production equipment; perform quality checks; entry level."),
        ("Warehouse Associate", "Pick, pack and ship orders; operate forklift; entry level, no experience required."),
        ("Sales Development Representative", "Prospect and cold call; book meetings; CRM; quota; new grads welcome."),
        ("Retail Sales Associate", "Assist customers in store; cashier duties; merchandising."),
        ("Customer Service Representative", "Answer customer calls and chats; resolve issues; entry level."),
        ("Administrative Assistant", "Schedule meetings, manage calendars, prepare documents; entry level."),
        ("Antenna Engineer", "Design RF antennas and PCB layouts; run thermal and structural analysis in CAD; MATLAB; 1+ years."),
    ]
    def test_accepts_relevant_roles(self):
        for title, desc, fam in self.ACCEPT:
            a = rel.assess(dict(title=title, desc=desc))
            self.assertEqual((a["relevance"], a["family"]), ("MATCH", fam), title)
            self.assertTrue(a["skills"], title)

    def test_rejects_unrelated_roles(self):
        for title, desc in self.REJECT:
            self.assertEqual(rel.assess(dict(title=title, desc=desc))["relevance"], "UNRELATED", title)

    def test_end_to_end_tig_welding_rejected_relevant_roles_reported(self):
        c.run([dict(company="SpaceX", ats="greenhouse", slug="s")], self.now, fetch=lambda s, seen: [])
        jobs = [J(t, desc=d, jid=i) for i, (t, d, _) in enumerate(self.ACCEPT)] + \
               [J(t, desc=d, jid=100 + i) for i, (t, d) in enumerate(self.REJECT)]
        r = c.run([dict(company="SpaceX", ats="greenhouse", slug="s")], self.now, fetch=lambda s, seen: jobs)
        reported = {n["title"] for n in r["new"] + r["review"]}
        self.assertNotIn("TIG Welder - Entry Level", reported)
        self.assertTrue(reported.isdisjoint({t for t, _ in self.REJECT}))
        for title, _, _ in self.ACCEPT:
            self.assertIn(title, reported)
        self.assertEqual(r["excluded"]["not relevant to profile"], len(self.REJECT))

    def test_phd_only_excluded_bs_ms_accepted(self):
        phd = rel.assess(dict(title="Research Scientist", desc="PhD in machine learning is required. Publish at NeurIPS. Python, PyTorch."))
        self.assertTrue(phd["phd_only"]); self.assertEqual(phd["education"], "PhD required")
        ok = rel.assess(dict(title="Applied Scientist", desc="MS or PhD in CS, or equivalent practical experience. Python, PyTorch, statistics."))
        self.assertFalse(ok["phd_only"])
        self.assertTrue(rel.assess(dict(title="AI Research Intern (PhD)", desc="Research LLMs in Python."))["phd_only"])
        c.run(TestRunBehaviour.SRC[:1], self.now, fetch=lambda s, seen: [])
        r = c.run(TestRunBehaviour.SRC[:1], self.now, fetch=lambda s, seen: [
            J("Research Scientist Intern", desc="Currently pursuing a PhD in CS. Python, PyTorch, deep learning.", jid=1),
            J("Research Engineer Intern", desc="Pursuing a BS or MS in CS. Python, PyTorch, deep learning.", jid=2)])
        self.assertEqual([n["title"] for n in r["new"]], ["Research Engineer Intern"])
        self.assertEqual(r["excluded"]["PhD required"], 1)

    def test_military_transition_programs_excluded(self):
        c.run(TestRunBehaviour.SRC[:1], self.now, fetch=lambda s, seen: [])
        r = c.run(TestRunBehaviour.SRC[:1], self.now, fetch=lambda s, seen: [
            J("Software Engineer Intern", {"facet": "Military Fellowship Program"}, jid=1),
            J("Data Analyst Intern - SkillBridge", desc="DoD SkillBridge program for transitioning service members. SQL, Python.", jid=2),
            J("Software Engineer Intern", jid=3)])
        self.assertEqual([n["id"] if "id" in n else n["url"] for n in r["new"]], ["https://x/Software Engineer Intern"])
        self.assertEqual(r["excluded"]["military transition program"], 2)

    def test_work_authorization_flags(self):
        A = rel.auth_assessment
        # explicit citizenship -> excluded
        for d in ("Must be a U.S. citizen.", "U.S. citizenship required.", "This role is open to U.S. citizens only.",
                  "Requires United States citizenship due to contract requirements.", "U.S. nationals only."):
            st, label = A(d)
            self.assertEqual(st, "EXCLUDE", d); self.assertTrue(label.startswith("Excluded — Citizenship/Clearance"), d)
        # explicit clearance -> excluded
        for d in ("Active Secret clearance required.", "Must be eligible to obtain a Top Secret clearance.",
                  "Candidates must hold an active TS/SCI.", "Ability to obtain and maintain a DoD security clearance."):
            self.assertEqual(A(d)[0], "EXCLUDE", d)
        # no sponsorship / existing work authorization -> retained and flagged
        for d in ("We do not offer visa sponsorship for this position.", "Sponsorship is not available for this position.",
                  "We are unable to sponsor employment visas.", "No immigration sponsorship now or in the future."):
            st, label = A(d)
            self.assertEqual((st, label), ("NO_SPONSORSHIP", "Included — No Sponsorship (does not offer visa sponsorship)"), d)
        for d in ("Must be authorized to work in the United States.", "Must have existing U.S. work authorization."):
            self.assertEqual(A(d), ("WORK_AUTH", "Included — Existing U.S. work authorization required"), d)
        # ambiguous -> needs review (never excluded)
        for d in ("Applicants must be a U.S. person as defined by ITAR.", "Export control regulations apply to this role.",
                  "ITAR compliance required.", "Permanent work authorization required.", "Security screening required.",
                  "Offer contingent on a background check.", "Must be a U.S. citizen or permanent resident.",
                  "Secret clearance is a plus."):
            self.assertEqual(A(d)[0], "AMBIGUOUS", d)
            self.assertIn("Needs review — work-authorization wording", A(d)[1])
        # nothing stated -> retained
        self.assertEqual(A("Python and SQL. Bachelor's degree."), ("NOT_STATED", "Not stated (unverified)"))

    def test_work_authorization_in_full_runs(self):
        c.run(TestRunBehaviour.SRC[:1], self.now, fetch=lambda s, seen: [])
        r = c.run(TestRunBehaviour.SRC[:1], self.now, fetch=lambda s, seen: [
            J("Data Science Intern", desc=TECH + " Must be a U.S. citizen.", jid=1),                     # excluded
            J("Software Engineer Intern", desc=TECH + " Active Secret clearance required.", jid=2),      # excluded
            J("Machine Learning Intern", desc=TECH + " We do not offer visa sponsorship.", jid=3),       # kept, flagged
            J("Data Analyst Intern", desc=TECH + " Must be authorized to work in the United States.", jid=4),  # kept
            J("Data Engineer Intern", desc=TECH + " This role is subject to ITAR; applicants must be U.S. persons.", jid=5),  # review
            J("AI Engineer Intern", jid=6)])                                                          # nothing stated, kept
        self.assertEqual(r["excluded"]["Excluded — Citizenship/Clearance"], 2)
        new = {n["title"]: n for n in r["new"]}
        self.assertEqual(sorted(new), ["AI Engineer Intern", "Data Analyst Intern", "Machine Learning Intern"])
        self.assertEqual(new["Machine Learning Intern"]["work_auth"], "Included — No Sponsorship (does not offer visa sponsorship)")
        self.assertEqual(new["AI Engineer Intern"]["work_auth"], "Not stated (unverified)")
        self.assertEqual([n["title"] for n in r["review"]], ["Data Engineer Intern"])
        self.assertIn("work-authorization wording needs review", r["review"][0]["reason"])
        md = c.write_report(r)
        self.assertIn("Excluded — Citizenship/Clearance: 2", md)
        self.assertIn("Included — No Sponsorship: 1 posting(s)", md)

    def test_background_check_setting(self):
        self.assertEqual(rel.auth_assessment("Background check required.")[0], "AMBIGUOUS")
        with mock.patch.object(rel, "REVIEW_ON_BACKGROUND_CHECK", False):
            self.assertEqual(rel.auth_assessment("Background check required.")[0], "NOT_STATED")

    def test_regions(self):
        self.assertEqual(rel.region("Alpharetta, GA"), "Atlanta metro")
        self.assertEqual(rel.region("Remote - US; Atlanta, Georgia"), "Atlanta metro")
        self.assertEqual(rel.region("Savannah, GA"), "Georgia")
        self.assertEqual(rel.region("Decatur, AL"), "United States")
        self.assertEqual(rel.region("Brookhaven, NY"), "United States")
        self.assertEqual(rel.region("Marietta, GA"), "Atlanta metro")

class TestQuietRunsAndHousekeeping(TempState):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(pi, "REPORTS", self.tmp / "reports"); p.start(); self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "9"}); e.start(); self.addCleanup(e.stop)

    def test_quiet_run_creates_no_issue_but_failures_do(self):
        src = TestRunBehaviour.SRC
        c.run(src, self.now, fetch=lambda s, seen: [])
        r = c.run(src, self.now, fetch=lambda s, seen: [J("TIG Welder", desc="welding", jid=1)])   # only unrelated
        c.write_report(r)
        self.assertEqual(pi.build_report_issue(r["date"], "me/repo", "me", "U"), (None, None))
        def half(s, seen):
            if s["slug"] == "b": raise ConnectionError("down")
            return []
        r = c.run(src, self.now, fetch=half); c.write_report(r)
        title, body = pi.build_report_issue(r["date"], "me/repo", "me", "U")
        self.assertIn("[1 sources failed]", title)

    def test_stale_report_from_earlier_run_is_not_reposted(self):
        r = c.run(TestRunBehaviour.SRC[:1], self.now, fetch=lambda s, seen: []); c.write_report(r)
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "10"}):         # a later run that crashed before writing
            title, body = pi.build_report_issue(r["date"], "me/repo", "me", "U")
        self.assertIn("DID NOT FINISH", title)

    def test_only_week_old_reports_are_closed(self):
        now = dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc)
        issues = [{"number": 1, "title": "Early-career report 2026-11-10 08:00 - 1 new, 0 to review", "created_at": "2026-11-10T13:10:00Z"},
                  {"number": 2, "title": "Early-career report 2026-11-19 08:00 - 2 new, 0 to review", "created_at": "2026-11-19T13:10:00Z"}]
        calls = []
        def fake(m, p, payload=None, token=None):
            calls.append((m, p)); return issues if m == "GET" else {}
        with mock.patch.object(pi, "api", fake):
            self.assertEqual(pi.close_previous("me/repo", 99, now=now), 1)
        self.assertIn(("PATCH", "/repos/me/repo/issues/1"), calls)

    def test_old_history_pruned(self):
        c.SEEN.parent.mkdir(parents=True, exist_ok=True)
        c.SEEN.write_text(json.dumps({"greenhouse:a|old": "2026-01-01", "greenhouse:a|recent": "2026-11-01"}))
        c.run(TestRunBehaviour.SRC[:1], self.now, fetch=lambda s, seen: [])
        seen = json.loads(c.SEEN.read_text())
        self.assertNotIn("greenhouse:a|old", seen); self.assertIn("greenhouse:a|recent", seen)

    def test_two_runs_same_day_keep_separate_reports(self):
        src = TestRunBehaviour.SRC[:1]
        c.run(src, self.now, fetch=lambda s, seen: [])
        r1 = c.run(src, self.now + dt.timedelta(hours=3), fetch=lambda s, seen: [J("Data Science Intern", jid=1)]); c.write_report(r1)
        r2 = c.run(src, self.now + dt.timedelta(hours=6), fetch=lambda s, seen: [J("Data Science Intern", jid=1), J("ML Intern", jid=2)])
        c.write_report(r2)
        self.assertNotEqual(r1["stamp"], r2["stamp"])
        self.assertEqual([n["title"] for n in r2["new"]], ["ML Intern"])           # no repeat across checks
        self.assertTrue((self.tmp / f"reports/{r1['stamp']}.csv").exists() and (self.tmp / f"reports/{r2['stamp']}.csv").exists())

    def test_discover_writes_incrementally_and_resumes(self):
        prog = self.tmp / "state/discover_progress.json"
        out = self.tmp / "discovered.csv"
        companies = [f"Co{i}" for i in range(6)]
        probe = lambda name: [dict(company=name, ats="greenhouse", slug=name.lower(), board_name=name, name_match="yes")]
        clock = iter([0, 0, 0, 0, 10_000] + [10_000] * 50)            # time budget runs out after the first few starts
        with mock.patch.object(c, "PROGRESS", prog), contextlib.redirect_stdout(io.StringIO()):
            fin1, _, rem1 = c.discover_all(companies, {}, out, budget_minutes=1, workers=1, probe=probe, now=lambda: next(clock))
            self.assertGreater(rem1, 0)
            rows1 = list(csv.DictReader(open(out)))
            self.assertEqual(len(rows1), fin1)                          # results saved even though the run stopped early
            fin2, _, rem2 = c.discover_all(companies, {}, out, budget_minutes=60, workers=3, probe=probe)
        self.assertEqual(rem2, 0); self.assertEqual(fin1 + fin2, 6)    # second run resumed, no company probed twice
        self.assertEqual(sorted(r["company"] for r in csv.DictReader(open(out))), companies)

    def test_failed_request_without_retry_does_not_sleep(self):
        with mock.patch.object(c.urllib.request, "urlopen", side_effect=ConnectionError("x")), \
             mock.patch.object(c.time, "sleep") as sl:
            with self.assertRaises(ConnectionError):
                c.http_json("https://example.invalid", retries=0)
        sl.assert_not_called()

    def test_discover_name_check_rejects_real_false_positives(self):
        # board names returned by the 2026-11-15 discover run on GitHub
        reject = [("Northside Hospital", "Northside Veterinary Clinic"), ("General Mills", "General Interest "),
                  ("Lowe's", "Lowe Mechanical Services"), ("Oliver Wyman", "OLIVER"), ("Charles Schwab", "charles"),
                  ("Genuine Parts Company", "Genuine"), ("Chicago Trading Company", "CTC Lateral - Website & LinkedIn")]
        accept = [("Calendly", "Calendly"), ("OneTrust", "OneTrust"), ("Salesloft", "Salesloft"),
                  ("DoorDash", "DoorDash USA"), ("Appian", "Appian Corporation "), ("Samsung Semiconductor (US)", "Samsung Semiconductor")]
        for co, board in reject:
            self.assertFalse(c.board_name_matches(co, board), (co, board))
        for co, board in accept:
            self.assertTrue(c.board_name_matches(co, board), (co, board))
        self.assertFalse(c.text_names_company("Applied Materials", "At Applied Intuition we build autonomy software"))
        self.assertTrue(c.text_names_company("Applied Intuition", "At Applied Intuition we build autonomy software"))
        self.assertFalse(c.text_names_company("Capital One", "Capital Rx is a health tech company"))

    def test_workday_skips_detail_for_unrelated_titles(self):
        posts = [{"title": t, "externalPath": f"/job/{i}", "locationsText": "Atlanta, GA", "postedOn": "Posted Today"}
                 for i, t in enumerate(["Retail Store Associate", "Delivery Driver", "Warehouse Associate", "Data Analyst I"])]
        detail_calls = []
        def fake(url, payload=None):
            if payload is None:
                detail_calls.append(url)
                return {"jobPostingInfo": {"jobDescription": "SQL, Python, Tableau; 0-1 years", "country": {"descriptor": "United States of America"}}}
            return {"total": 4, "facets": [], "jobPostings": posts}
        with mock.patch.object(c, "http_json", side_effect=fake), mock.patch.object(c.time, "sleep"):
            jobs = list(c.fetch_workday({"wd_host": "g.wd1.myworkdayjobs.com", "slug": "g", "wd_site": "S"}, today=dt.date(2026, 11, 15)))
        self.assertEqual(len(detail_calls), 1)                                   # only the Data Analyst I posting
        self.assertTrue(detail_calls[0].endswith("/job/3"))
        self.assertFalse(any(j["meta"].get("_detail_skipped") for j in jobs))     # skipped ones are not deferred either
        self.assertEqual(rel.assess(jobs[0])["relevance"], "UNRELATED")

    def test_workday_baseline_pass_needs_no_detail_calls(self):
        posts = [{"title": f"Data Analyst {i}", "externalPath": f"/job/{i}", "locationsText": "Atlanta, GA", "postedOn": "Posted Today"}
                 for i in range(300)]
        detail = []
        def fake(url, payload=None):
            if payload is None:
                detail.append(url); return {"jobPostingInfo": {}}
            o = payload["offset"]
            return {"total": 300, "facets": [], "jobPostings": posts[o:o + 20]}
        with mock.patch.object(c, "http_json", side_effect=fake), mock.patch.object(c.time, "sleep"):
            jobs = list(c.fetch_workday({"wd_host": "t.wd1.myworkdayjobs.com", "slug": "t", "wd_site": "S"},
                                        today=dt.date(2026, 11, 15), baseline=True))
        self.assertEqual(len(jobs), 300); self.assertEqual(detail, [])
        self.assertFalse(any(j["meta"].get("_detail_skipped") for j in jobs))   # nothing deferred -> baseline done in one check

    def test_tests_do_not_write_to_github_summary(self):
        self.assertNotIn("GITHUB_STEP_SUMMARY", os.environ)

    def test_discover_workday_finds_site(self):
        def poster(url):
            if url == "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/jobs": return {"total": 42}
            raise ConnectionError()
        rows = c.discover_workday("Acme", ["acme"], poster=poster,
                                  root_site=lambda host: "AcmeCareers" if host.startswith("acme.wd5") else None)
        self.assertEqual((rows[0]["wd_host"], rows[0]["wd_site"]), ("acme.wd5.myworkdayjobs.com", "AcmeCareers"))
        self.assertEqual(c.discover_workday("Nope", ["nope"], poster=poster, root_site=lambda h: None), [])

class TestUpdateMaster(unittest.TestCase):
    def test_untested_never_marked_verified(self):
        import update_master as um
        t = Path(tempfile.mkdtemp())
        (t / "US_Early_Careers_300_Companies.csv").write_text(
            "Company_ID,Company_Name,Industry,Official_Careers_URL,Early_Careers_URL,Internship_URL,New_Graduate_URL,Direct_Job_Listings_URL,ATS_Platform,"
            "Monitoring_Suitability,Monitoring_Method,URL_Verification_Status,Last_Checked_Date,Last_Successful_Check,Next_Check_Date,Monitoring_Status,"
            "Last_Reported_Opportunity_IDs,Notes\nC1,A,x,,,,,,,,,UNVERIFIED,,,,Not started - needs verification,,\n"
            "C2,B,x,,,,,,,,,UNVERIFIED,,,,Not started - needs verification,,\n")
        (t / "ats_sources.csv").write_text("company,ats,slug,wd_host,wd_site,enabled,live_status,jobs_at_check,last_checked,notes\n"
                                           "A,greenhouse,a,,,1,UNTESTED,,,\nB,greenhouse,b,,,1,WORKING,5,2026-11-10,\n")
        with mock.patch.object(um, "HERE", t), mock.patch.object(um, "MASTER", t / "US_Early_Careers_300_Companies.csv"):
            with contextlib.redirect_stdout(io.StringIO()):
                um.main()
        rows = {r["Company_Name"]: r for r in csv.DictReader(open(t / "US_Early_Careers_300_Companies.csv"))}
        self.assertTrue(rows["A"]["URL_Verification_Status"].startswith("UNVERIFIED"))
        self.assertTrue(rows["B"]["URL_Verification_Status"].startswith("VERIFIED"))

class TestFetchers(unittest.TestCase):
    def test_lever_parsing(self):
        data = [{"id": "x1", "text": "Software Engineer, Internship", "hostedUrl": "https://jobs.lever.co/p/x1", "country": "US",
                 "categories": {"commitment": "Internship", "location": "New York, NY", "team": "Dev"}, "createdAt": 1762700000000,
                 "descriptionPlain": "About", "lists": [{"text": "Req", "content": "<li>1+ years of experience</li>"}]}]
        with mock.patch.object(c, "http_json", return_value=data):
            j = list(c.fetch_lever({"slug": "p"}))[0]
        self.assertEqual(j["country"], "United States"); self.assertEqual(c.classify(j)[:2], ("CONFIRMED", "Internship"))

    def test_ashby_parsing_and_unlisted_skipped(self):
        data = {"jobs": [{"id": "a", "title": "Product Analyst", "jobUrl": "u", "employmentType": "Intern", "location": "San Francisco",
                          "address": {"postalAddress": {"addressCountry": "United States"}}, "publishedAt": "2026-11-01T00:00:00Z",
                          "descriptionPlain": "", "isListed": True},
                         {"id": "b", "title": "Hidden", "isListed": False}]}
        with mock.patch.object(c, "http_json", return_value=data):
            jobs = list(c.fetch_ashby({"slug": "s"}))
        self.assertEqual(len(jobs), 1); self.assertEqual(c.classify(jobs[0])[:2], ("CONFIRMED", "Internship"))
        self.assertTrue(c.is_us(jobs[0]["location"], jobs[0]["country"]))

    def test_workday_facets_paging_detail_cap(self):
        facets = [{"facetParameter": "workerSubType", "values": [{"id": "i1", "descriptor": "Intern (Trainee)", "count": 1},
                                                                  {"id": "c1", "descriptor": "Co-op", "count": 1},
                                                                  {"id": "r1", "descriptor": "Regular", "count": 40}]}]
        allp = [{"title": f"Job {n}", "externalPath": f"/job/{n}", "locationsText": "Lone Tree, CO", "postedOn": "Posted 3 Days Ago",
                 "timeType": "Full time"} for n in range(42)]
        def fake(url, payload=None):
            if payload is None:
                return {"jobPostingInfo": {"jobDescription": "<p>0-1 years of experience</p>", "country": {"descriptor": "United States of America"}}}
            f = payload["appliedFacets"]
            if f.get("workerSubType") == ["i1"]: return {"total": 1, "jobPostings": [allp[0]]}
            if f.get("workerSubType") == ["c1"]: return {"total": 1, "jobPostings": [allp[1]]}
            o = payload["offset"]
            return {"total": 42, "facets": facets, "jobPostings": allp[o:o + 20]}
        with mock.patch.object(c, "http_json", side_effect=fake), mock.patch.object(c, "WORKDAY_DETAIL_CAP", 5), mock.patch.object(c.time, "sleep"):
            jobs = list(c.fetch_workday({"wd_host": "t.wd1.myworkdayjobs.com", "slug": "t", "wd_site": "S"}, today=dt.date(2026, 11, 10)))
        self.assertEqual(len(jobs), 42)
        self.assertEqual(c.classify(jobs[0])[:2], ("CONFIRMED", "Internship"))
        self.assertEqual(c.classify(jobs[1])[0], "EXCLUDED")
        self.assertEqual(sum(1 for j in jobs if j["desc"]), 5)
        self.assertEqual(sum(1 for j in jobs if j["meta"].get("_detail_skipped")), 42 - 2 - 5)
        self.assertEqual(jobs[2]["posted"], "2026-11-07")

    def test_discover_name_check(self):
        def getter(url):
            if "greenhouse.io/v1/boards/acme" in url: return {"name": "Acme Corp"}
            if "greenhouse.io/v1/boards/globex" in url: return {"name": "Sgt. Pepper's Band"}
            raise ConnectionError()
        with mock.patch.object(c.time, "sleep"):
            rows = c.discover(["Acme", "Globex"], getter=getter)
        self.assertEqual({r["company"]: r["name_match"] for r in rows}, {"Acme": "yes", "Globex": "NO - check"})

class TestScheduleGuard(unittest.TestCase):
    U = dt.timezone.utc
    CRON_HOURS_UTC = (0, 1, 12, 13, 15, 16, 18, 19, 21, 22)          # must match daily.yml

    def simulate(self, day, dropped=()):
        """Fire every cron trigger of one New York day in order; return the NY times that actually ran."""
        start = dt.datetime(day.year, day.month, day.day, 6, 0, tzinfo=NY).astimezone(self.U)
        trig = []
        for d in (0, 1):
            for h in self.CRON_HOURS_UTC:
                t = dt.datetime(start.year, start.month, start.day, h, 7, tzinfo=self.U) + dt.timedelta(days=d)
                if start <= t < start + dt.timedelta(hours=18) and t not in dropped:
                    trig.append(t)
        last, ran = f"{(day - dt.timedelta(days=1)).isoformat()}T20", []
        for t in sorted(trig):
            ok, slot, _ = g.should_run(t, last, "schedule")
            if ok:
                ran.append(t.astimezone(NY).strftime("%H:%M")); last = slot
        return ran

    def test_five_checks_in_winter_and_summer(self):
        self.assertEqual(self.simulate(dt.date(2027, 1, 15)), ["08:07", "11:07", "14:07", "17:07", "20:07"])
        self.assertEqual(self.simulate(dt.date(2027, 7, 15)), ["08:07", "11:07", "14:07", "17:07", "20:07"])

    def test_dst_switch_days(self):
        for day in (dt.date(2027, 3, 14), dt.date(2027, 11, 7), dt.date(2026, 11, 1)):
            self.assertEqual(self.simulate(day), ["08:07", "11:07", "14:07", "17:07", "20:07"], day)

    def test_dropped_trigger_is_covered_by_backup(self):
        # winter: the 08:07 EST trigger is dropped -> the next trigger (10:07) runs the 08:00 slot
        ran = self.simulate(dt.date(2027, 1, 15), dropped={dt.datetime(2027, 1, 15, 13, 7, tzinfo=self.U)})
        self.assertEqual(ran, ["10:07", "11:07", "14:07", "17:07", "20:07"])
        # summer: the 08:07 EDT trigger is dropped -> the 09:07 backup runs it
        ran = self.simulate(dt.date(2027, 7, 15), dropped={dt.datetime(2027, 7, 15, 12, 7, tzinfo=self.U)})
        self.assertEqual(ran, ["09:07", "11:07", "14:07", "17:07", "20:07"])

    def test_each_slot_runs_once_and_manual_always_runs(self):
        t = dt.datetime(2027, 1, 15, 16, 7, tzinfo=self.U)                       # 11:07 EST
        self.assertEqual(g.should_run(t, None, "schedule")[:2], (True, "2027-01-15T11"))
        self.assertFalse(g.should_run(t, "2027-01-15T11", "schedule")[0])
        self.assertTrue(g.should_run(t, "2027-01-15T11", "workflow_dispatch")[0])
        self.assertFalse(g.should_run(t, "2027-01-15T14", "schedule")[0])        # a later slot already ran
        self.assertEqual(g.current_slot(dt.datetime(2027, 1, 15, 12, 7, tzinfo=self.U)), "2027-01-14T20")

    def test_old_last_run_file_without_slot(self):
        tmp = Path(tempfile.mkdtemp()) / "last_run.json"
        tmp.write_text(json.dumps({"date": "2026-11-15"}))
        with mock.patch.object(g, "LAST", tmp):
            self.assertIsNone(g.read_last())
            g.mark_done("2026-11-16T08")
            self.assertEqual(g.read_last(), "2026-11-16T08")

    def test_external_trigger_follows_slot_rule(self):
        tmp = Path(tempfile.mkdtemp()) / "last_run.json"
        at_0801 = dt.datetime(2027, 1, 15, 13, 1, tzinfo=dt.timezone.utc)          # 08:01 EST
        def guard(trigger):
            out = Path(tempfile.mkdtemp()) / "out"
            env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GUARD_TRIGGER": trigger, "GITHUB_OUTPUT": str(out)}
            with mock.patch.object(g, "LAST", tmp), contextlib.redirect_stdout(io.StringIO()):
                g.main(["guard"], env, at_0801)
            return out.read_text()
        first = guard("external")
        self.assertIn("run=true", first); self.assertIn("slot=2027-01-15T08", first)
        with mock.patch.object(g, "LAST", tmp):
            g.main(["guard", "mark-done", "2027-01-15T08"], {}, at_0801)
        self.assertIn("run=false", guard("external"))         # second outside call in the same slot is skipped
        self.assertIn("run=true", guard("manual"))            # the Run workflow button always runs

    def test_workflow_cron_matches_five_slots(self):
        wf = (ROOT / ".github/workflows/daily.yml").read_text()   # plain text: PyYAML is not on the runner
        self.assertEqual(re.findall(r'cron:\s*"([^"]+)"', wf), ["7 0,1,12,13,15,16,18,19,21,22 * * *"])
        self.assertIn("issues: write", wf); self.assertIn("contents: write", wf)
        self.assertIn("mark-done ${{ steps.guard.outputs.slot }}", wf)
        # runs never overlap, and a queued run loads the latest history before deciding
        self.assertRegex(wf, r"concurrency:\s*\n\s*group: early-career-collector\s*\n\s*cancel-in-progress: false")
        self.assertRegex(wf, r"actions/checkout@v4\s*\n\s*with:\s*\n(\s*#.*\n)*\s*ref: main")

class TestSaveHistory(unittest.TestCase):
    def sh(self, *a, cwd):
        return subprocess.run(a, cwd=cwd, capture_output=True, text=True)

    def test_commit_push_and_retry_after_concurrent_push(self):
        t = Path(tempfile.mkdtemp())
        self.sh("git", "init", "-q", "--bare", "-b", "main", str(t / "remote.git"), cwd=t)
        self.sh("git", "clone", "-q", str(t / "remote.git"), "repo", cwd=t)
        repo = t / "repo"
        for k, v in (("user.name", "t"), ("user.email", "t@t")):
            self.sh("git", "config", k, v, cwd=repo)
        (repo / "scripts").mkdir(); (repo / "scripts/save_history.sh").write_text((ROOT / "scripts/save_history.sh").read_text())
        (repo / "README").write_text("x"); self.sh("git", "checkout", "-q", "-b", "main", cwd=repo)
        self.sh("git", "add", "-A", cwd=repo); self.sh("git", "commit", "-qm", "init", cwd=repo); self.sh("git", "push", "-q", "origin", "main", cwd=repo)
        # someone else pushes first (e.g. a manual edit on GitHub)
        self.sh("git", "clone", "-q", str(t / "remote.git"), "other", cwd=t)
        other = t / "other"
        for k, v in (("user.name", "o"), ("user.email", "o@o")):
            self.sh("git", "config", k, v, cwd=other)
        (other / "ats_sources.csv").write_text("edited"); self.sh("git", "add", "-A", cwd=other)
        self.sh("git", "commit", "-qm", "edit", cwd=other); self.sh("git", "push", "-q", "origin", "main", cwd=other)
        (repo / "state").mkdir(); (repo / "state/seen_jobs.json").write_text("{}")
        (repo / "reports").mkdir(); (repo / "reports/2026-11-10.md").write_text("r")
        r = self.sh("bash", "scripts/save_history.sh", cwd=repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        log = self.sh("git", "--git-dir", str(t / "remote.git"), "log", "--name-only", "--format=%s", "main", cwd=t).stdout
        self.assertIn("state/seen_jobs.json", log); self.assertIn("edit", log)
        r2 = self.sh("bash", "scripts/save_history.sh", cwd=repo)
        self.assertIn("nothing to commit", r2.stdout)
        # verify-only run: just source_status.csv exists as a new file
        (repo / "source_status.csv").write_text("company,status\nA,WORKING\n")
        r3 = self.sh("bash", "scripts/save_history.sh", cwd=repo)
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
        self.assertIn("source_status.csv", self.sh("git", "--git-dir", str(t / "remote.git"), "log", "-1", "--name-only", "main", cwd=t).stdout)

if __name__ == "__main__":
    unittest.main()
