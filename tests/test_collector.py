import contextlib, csv, datetime as dt, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
import ats_collector as c
import schedule_guard as g

FIX = json.loads((ROOT / "tests/fixtures/greenhouse_real_sample.json").read_text())["jobs"]
NY = ZoneInfo("America/New_York")

def gh_job(j):
    """Convert a raw Greenhouse posting the same way fetch_greenhouse does."""
    meta = {m["name"]: m.get("value") for m in j.get("metadata") or []}
    return dict(id=j["id"], title=j["title"], url="u", location=j["location"]["name"], country="",
                posted=j["first_published"][:10], meta=meta, desc=c.strip_html(j.get("content", "")))

def J(title, meta=None, desc="", loc="New York, NY", jid=None):
    return dict(id=jid or title, title=title, url="https://x/" + title, location=loc, country="", posted="2026-11-09",
                meta=meta or {}, desc=desc)

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

class TestClassification(unittest.TestCase):
    def test_coop_always_excluded(self):
        for job in (J("Software Engineer Co-op (Spring 2027)"), J("Data Science Coop"),
                    J("Engineering Student", {"Employment Type": "Co-op"}),
                    J("Engineering Intern", {"facet": "co-op"}),
                    J("Cooperative Education Student - IT")):
            self.assertEqual(c.classify(job)[0], "EXCLUDED", job["title"])

    def test_coop_mention_in_description_is_flag_not_exclusion(self):
        st, typ, why = c.classify(J("Software Engineering Intern", desc="Co-ops are only available to students in a co-op program."))
        self.assertEqual((st, typ), ("CONFIRMED", "Internship")); self.assertIn("co-op", why)

    def test_keywordless_entry_roles_found(self):
        self.assertEqual(c.classify(J("Data Analyst", desc="0-1 years of relevant experience preferred"))[:2], ("CONFIRMED", "Entry-Level"))
        self.assertEqual(c.classify(J("Software Engineer", {"Job Level": "IC1"}))[:2], ("CONFIRMED", "Entry-Level"))
        self.assertEqual(c.classify(J("Software Engineer", {"employment type": "Intern"}))[:2], ("CONFIRMED", "Internship"))
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
            self.assertEqual(c.classify(J(t))[:2], ("CONFIRMED", typ), t)

    def test_senior_excluded(self):
        for t in ("Senior Data Scientist", "Staff Software Engineer, New Grad Programs", "Engineering Manager, University Recruiting",
                  "Principal ML Engineer", "Product Manager", "Software Engineer III", "Lead Analyst", "Data Engineer 2"):
            self.assertEqual(c.classify(J(t))[0], "EXCLUDED", t)
        self.assertEqual(c.classify(J("Associate", desc="5+ years of experience in consulting"))[0], "EXCLUDED")
        self.assertEqual(c.classify(J("Senior Associate"))[0], "EXCLUDED")

    def test_min_years(self):
        self.assertEqual(c.min_years("Requires 3-5 years of professional experience"), 3)
        self.assertEqual(c.min_years("0 to 2 years of experience"), 0)
        self.assertEqual(c.min_years("a 4-year degree; 1+ years experience"), 1)
        self.assertIsNone(c.min_years("Join our 10 year old company"))

class TempState(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for name, val in (("SEEN", self.tmp / "state/seen.json"), ("SRC_STATE", self.tmp / "state/src.json"), ("REPORTS", self.tmp / "reports")):
            p = mock.patch.object(c, name, val); p.start(); self.addCleanup(p.stop)
        self.now = dt.datetime(2026, 11, 10, 8, 0, tzinfo=NY)

class TestRunBehaviour(TempState):
    SRC = [dict(company="A", ats="greenhouse", slug="a"), dict(company="B", ats="lever", slug="b")]

    def test_first_run_is_baseline_then_new_only(self):
        jobs = {"a": [J("SWE Intern 2027", jid=1), J("Senior SWE", jid=2), J("Data Analyst", desc="0-1 years experience", jid=3)],
                "b": [J("Software Engineer, New Grad", jid=9, loc="Toronto, ON, CA")]}
        r1 = c.run(self.SRC, self.now, fetch=lambda s, seen: jobs[s["slug"]])
        self.assertEqual(r1["new"], []); self.assertEqual(len(r1["baseline"]), 2)    # Canada role skipped
        md = c.write_report(r1)
        self.assertTrue((self.tmp / "reports/baseline-2026-11-10.md").exists())
        self.assertIn("Baseline (existing, not new): 2", md)
        self.assertIn("Baseline", c.email_subject(r1))
        jobs["a"].append(J("Machine Learning Intern", jid=4))
        r2 = c.run(self.SRC, self.now + dt.timedelta(days=1), fetch=lambda s, seen: jobs[s["slug"]])
        self.assertEqual([n["title"] for n in r2["new"]], ["Machine Learning Intern"]); self.assertEqual(r2["baseline"], [])
        r3 = c.run(self.SRC, self.now + dt.timedelta(days=2), fetch=lambda s, seen: jobs[s["slug"]])
        self.assertEqual(r3["new"], [])                                                # de-duplicated

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
        r = c.run(self.SRC[:1], self.now, fetch=lambda s, seen: [J("Business Analyst", desc="2+ years of experience", jid=1),
                                                                  J("Software Engineer", jid=2), J("Support Engineer", loc="", jid=3)])
        self.assertEqual(len(r["review"]), 1); self.assertEqual(len(r["unclassified"]), 2)
        c.write_report(r)
        self.assertTrue((self.tmp / f"reports/{r['date']}-unclassified.csv").exists())
        self.assertIn("[US? unverified]", r["unclassified"][1]["location"])

    def test_deferred_postings_in_first_run_stay_baseline(self):
        def batch(n_deferred):
            jobs = [J("Engineering Intern", jid="a")]
            for i in range(n_deferred):
                d = J("Program Analyst " + str(i), jid=f"d{i}"); d["meta"]["_detail_skipped"] = "yes"; jobs.append(d)
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
                                       for j in FIX if j["board"] == "robinhood"]}).encode()
        class Resp:
            def __init__(s, b): s.b = b
            def read(s, *a): return s.b
            def __enter__(s): return s
            def __exit__(s, *a): pass
        calls = []
        def fake_urlopen(req, timeout=0):
            calls.append(req.full_url); return Resp(payload)
        src = self.tmp / "src.csv"
        src.write_text("company,ats,slug,wd_host,wd_site,enabled\nRobinhood,greenhouse,robinhood,,,1\nOff,greenhouse,off,,,0\n")
        with mock.patch.object(c.urllib.request, "urlopen", fake_urlopen), mock.patch.object(c, "maybe_email", return_value=False), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(c.main(["run", "--sources", str(src)]), 0)
        self.assertEqual(calls, ["https://boards-api.greenhouse.io/v1/boards/robinhood/jobs?content=true"])   # disabled source skipped
        base = list((self.tmp / "reports").glob("baseline-*.md"))[0].read_text()
        self.assertIn("Customer Experience Associate, Pathway", base)   # keyword-less early role caught via metadata
        self.assertIn("Fraud Operations Intern", base)
        self.assertNotIn("Futures Risk Program Manager", base)

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
    def test_winter_and_summer(self):
        jan = lambda h, m: dt.datetime(2027, 1, 15, h, m, tzinfo=self.U)
        jul = lambda h, m: dt.datetime(2027, 7, 15, h, m, tzinfo=self.U)
        self.assertFalse(g.should_run(jan(11, 50), None, "schedule")[0])   # 06:50 EST
        self.assertTrue(g.should_run(jan(12, 50), None, "schedule")[0])    # 07:50 EST
        self.assertTrue(g.should_run(jul(11, 50), None, "schedule")[0])    # 07:50 EDT
        self.assertFalse(g.should_run(jul(12, 50), "2027-07-15", "schedule")[0])
        self.assertTrue(g.should_run(jul(14, 40), None, "schedule")[0])    # 10:40 EDT - late trigger still runs
        self.assertFalse(g.should_run(jul(16, 30), None, "schedule")[0])   # 12:30 - too late
        self.assertTrue(g.should_run(jan(3, 0), "2027-01-15", "workflow_dispatch")[0])

    def test_dst_switch_days(self):
        # 2027-03-14 (spring forward) and 2027-11-07 (fall back): exactly one trigger inside the window runs first
        for day in (dt.date(2027, 3, 14), dt.date(2027, 11, 7), dt.date(2026, 11, 1)):
            last, ran = None, []
            for hh in (11, 12, 13):
                t = dt.datetime(day.year, day.month, day.day, hh, 50, tzinfo=self.U)
                ok, _ = g.should_run(t, last, "schedule")
                if ok:
                    ran.append(t.astimezone(NY).strftime("%H:%M")); last = t.astimezone(NY).date().isoformat()
            self.assertEqual(len(ran), 1, (day, ran)); self.assertIn(ran[0], ("07:50", "08:50"))

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
