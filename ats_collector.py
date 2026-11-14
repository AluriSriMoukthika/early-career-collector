#!/usr/bin/env python3
"""Early-career job collector.

Polls public ATS job-board endpoints, classifies every US posting into
  CONFIRMED  - early-career per ATS career-level metadata, title, or stated experience (0-1 yrs)
  REVIEW     - plausibly early-career but uncertain (IC2/level-3 metadata, "2+ years", early cues in text)
  UNCLASSIFIED - no level information at all (kept in a CSV so nothing is silently dropped)
  EXCLUDED   - co-op, senior/manager titles or levels, or 3+ years required
and reports only postings not seen before. Co-ops are always excluded.

Modes:
  run       daily collection (state in state/, reports in reports/)
  verify    call every source once and write source_status.csv (OK / FAIL / UNTESTED)
  discover  probe Greenhouse/Lever/Ashby board names for companies not yet configured
"""
import argparse, csv, datetime as dt, html, json, os, re, smtplib, sys, time
import urllib.error, urllib.request
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
STATE = HERE / "state"
SEEN = STATE / "seen_jobs.json"
SRC_STATE = STATE / "sources_state.json"
REPORTS = HERE / "reports"
NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "early-career-collector/2.0 (personal job search; contact via repo owner)",
      "Accept": "application/json"}
WORKDAY_DETAIL_CAP = int(os.environ.get("WORKDAY_DETAIL_CAP", "150"))   # detail calls per source per run
FAIL_EXIT_RATIO = float(os.environ.get("FAIL_EXIT_RATIO", "0.25"))       # >25% failed sources -> exit 2

TYPES = ["Internship", "New Graduate", "Entry-Level", "Early-Career Full-Time", "Graduate Program", "Rotational Program"]

# ============================================================== classification
COOP = re.compile(r"\bco-?op\b|\bcooperative education\b|\bco-operative\b", re.I)
SENIOR_TITLE = re.compile(
    r"\bsenior\b|\bsr\.?\b|\bstaff\b|\bprincipal\b|\blead\b|(?<!associate product )(?<!rotational product )\bmanager\b|\bdirector\b|\bhead of\b|\bvp\b|"
    r"\bvice president\b|\bchief\b|\barchitect\b|\bexpert\b|\bdistinguished\b|\bfellow\b(?!ship)|"
    r"\b(ii|iii|iv|v|2|3|4|5)\s*$|\b(engineer|developer|analyst|scientist|specialist|consultant)\s+(ii|iii|iv|ll|2|3|4)\b", re.I)
# Strong early signals in titles
TITLE_RULES = [
    ("Rotational Program", r"\brotation(al)?\b"),
    ("Graduate Program", r"\b(development|leadership|analyst|associate|graduate|technology|early careers?) (development )?program\b|\bTDP\b|\bLDP\b|\bresidency\b|\bassociate product manager\b|\bAPM\b"),
    ("Internship", r"\bintern(ship)?s?\b|\bsummer (analyst|associate)\b|\bapprentice(ship)?\b|\bfellowship\b|\bextern(ship)?\b"),
    ("New Graduate", r"new ?grad|university grad|recent grad|\bgrads?\b|\bgraduate\b|class of 20\d\d|20\d\d start|\bcampus\b"),
    ("Entry-Level", r"entry[- ]level|\bjunior\b|\bjr\.?\b|\b(engineer|developer|analyst|scientist|associate|specialist)\s*(i|1)\b|\blevel (i|1)\b|\bassociate\b(?!.*(general counsel|director|partner))"),
    ("Early-Career Full-Time", r"early[- ]career|early talent|emerging talent|\bpathway\b"),
]
TITLE_RULES = [(t, re.compile(p, re.I)) for t, p in TITLE_RULES]
# Metadata / ATS-field values
META_EARLY = [
    ("Internship", re.compile(r"\bintern(ship)?s?\b|\btrainee\b|\bstudent\b|\bsummer\b", re.I)),
    ("New Graduate", re.compile(r"new ?grad|university|graduat|campus", re.I)),
    ("Early-Career Full-Time", re.compile(r"early[ -]?(talent|career)|emerging talent|entry", re.I)),
]
META_ENTRY_LEVEL = re.compile(r"^(ic\s*-?\s*1|l\s*-?\s*[12]|level\s*[12]|p1|e1|t1|1)$", re.I)
META_AMBIG_LEVEL = re.compile(r"^(ic\s*-?\s*2|l\s*-?\s*3|level\s*3|p2|2|3)$", re.I)
META_SENIOR = re.compile(r"experienced|\bmanager\b|director|vice president|executive|people manager|^mg$|"
                         r"^(ic\s*-?\s*[3-9]|m\d|d\d|l\s*-?\s*[4-9]|[4-9](/\d)*|5/l6)$", re.I)
LEVEL_FIELD = re.compile(r"level|career track|job type|req type|employment type|worker sub-?type|time type|ic or mg|"
                         r"bucket|department|category|job family|program|commitment|team|facet", re.I)
YEARS = re.compile(r"(?<![\d.])(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*\+?)?\s*(?:years?|yrs?)\b"
                   r"(?:\s+\w+){0,6}?\s+(?:of\s+)?(?:\w+\s+){0,4}?experience", re.I)
EARLY_TEXT = re.compile(r"new grad|recent(ly)? graduat|graduating in 20\d\d|entry[- ]level|early[- ]career|"
                        r"no (prior )?(professional )?experience (is )?required|0\s*(-|–|to)\s*[12]\s*years|"
                        r"university graduates|class of 20\d\d", re.I)

def _flat(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [x for i in v for x in _flat(i)]
    if isinstance(v, dict):
        return []        # e.g. pay ranges
    s = str(v).strip()
    return [] if s.lower() in ("", "null", "none") else [s]

def min_years(text):
    """Smallest 'N years ... experience' requirement found in text, or None."""
    vals = [int(m.group(1)) for m in YEARS.finditer(text or "")]
    return min(vals) if vals else None

def classify(job):
    """job: dict(title, meta={field: value}, desc, location). Returns (status, type, reason)."""
    title = (job.get("title") or "").strip()
    meta = {k: _flat(v) for k, v in (job.get("meta") or {}).items()}
    desc = job.get("desc") or ""
    level_vals = [(k, v) for k, vs in meta.items() if LEVEL_FIELD.search(k) for v in vs]

    # 1. Co-op: always excluded (title or any level/type field). A mention only in the description is flagged.
    if COOP.search(title) or any(COOP.search(v) for _, v in level_vals):
        return "EXCLUDED", "", "co-op"
    coop_note = " (description mentions co-op - check)" if COOP.search(desc[:4000]) else ""
    recruiter_role = re.search(r"recruit", title, re.I)   # 'University Recruiter' is not an early-career role

    # 2. Metadata signals
    meta_type, meta_reason, meta_senior, meta_ambig = None, "", None, None
    for k, v in level_vals:
        if recruiter_role and re.search(r"department|team|category|bucket|family", k, re.I):
            continue
        hits = [typ for typ, rx in META_EARLY if rx.search(v)]
        if len(hits) > 1 and "Internship" in hits and not re.search(r"\bintern", title, re.I):
            hits.remove("Internship")
        for typ in hits:
            if meta_type is None or TYPES.index(typ) < TYPES.index(meta_type):
                meta_type, meta_reason = typ, f"metadata {k}={v}"
        if re.search(r"level|track|ic or mg|sub-?type|job type|req type", k, re.I):
            if META_ENTRY_LEVEL.match(v) and not meta_type:
                meta_type, meta_reason = "Entry-Level", f"metadata {k}={v}"
            elif META_SENIOR.search(v):
                meta_senior = f"metadata {k}={v}"
            elif META_AMBIG_LEVEL.match(v):
                meta_ambig = f"metadata {k}={v}"

    # 3. Title signals
    title_type = next((t for t, rx in TITLE_RULES if rx.search(title)), None)
    senior_title = SENIOR_TITLE.search(title)
    yrs = min_years(desc)

    if meta_type and meta_type in ("Internship", "New Graduate") and title_type in ("Rotational Program", "Graduate Program"):
        meta_type = title_type
    if senior_title and title_type != "Internship":
        return "EXCLUDED", "", f"senior title ({senior_title.group(0).strip()})"
    if meta_type:
        return "CONFIRMED", meta_type, meta_reason + coop_note
    if senior_title:
        return "EXCLUDED", "", f"senior title ({senior_title.group(0).strip()})"
    if meta_senior and title_type not in ("Internship", "New Graduate"):
        return "EXCLUDED", "", meta_senior
    if title_type and title_type != "Entry-Level":
        return "CONFIRMED", title_type, "title" + coop_note
    if title_type == "Entry-Level":                 # 'Associate', 'Analyst I' etc. - check stated experience
        if yrs is not None and yrs >= 3:
            return "EXCLUDED", "", f"title says entry but requires {yrs}+ yrs"
        return "CONFIRMED", "Entry-Level", "title" + (f"; {yrs} yrs stated" if yrs is not None else "") + coop_note
    if yrs is not None:
        if yrs <= 1:
            return "CONFIRMED", "Entry-Level", f"requires {yrs} yrs experience" + coop_note
        if yrs == 2:
            return "REVIEW", "Entry-Level", "requires 2 yrs experience" + coop_note
        return "EXCLUDED", "", f"requires {yrs}+ yrs"
    if meta_ambig:
        return "REVIEW", "Entry-Level", meta_ambig + coop_note
    if EARLY_TEXT.search(desc):
        return "REVIEW", "Early-Career Full-Time", "description has early-career wording" + coop_note
    return "UNCLASSIFIED", "", "no level information"

# ============================================================== location
US_STATES = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
             "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY").split()
US_WORDS = re.compile(r"united states|\bU\.?S\.?A?\b|\bAMER\b|alabama|alaska|arizona|arkansas|california|colorado|"
                      r"connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|"
                      r"maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|"
                      r"new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|"
                      r"pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|"
                      r"washington|west virginia|wisconsin|wyoming|san francisco|seattle|boston|chicago|austin|atlanta|"
                      r"denver|los angeles|mountain view|bellevue|mclean|dallas|houston|palo alto|sunnyvale|san jose|"
                      r"pittsburgh|philadelphia|charlotte|miami|richmond|columbus|minneapolis|detroit|phoenix|menlo park|"
                      r"san diego|redmond|raleigh|nashville|salt lake|cambridge, ma|brooklyn|jersey city|plano|irving", re.I)
CANADA_CA = re.compile(r",\s*(ON|BC|QC|AB|MB|NS|NB|SK|NL|PE)\s*,\s*CA\b")

def is_us(location, country=""):
    """True / False / None(unknown)."""
    if country:
        return bool(re.search(r"united states|^us$|^usa$", country, re.I))
    loc = (location or "").strip()
    if not loc:
        return None
    for part in re.split(r";|\||/| or ", loc):
        p = CANADA_CA.sub("", part)
        if US_WORDS.search(p) or re.search(r",\s*(%s)\b" % "|".join(US_STATES), p):
            return True
    if re.fullmatch(r"(remote|hybrid|anywhere|multiple locations?|various)", loc, re.I):
        return None
    return False

# ============================================================== HTTP + fetchers
def http_json(url, payload=None, timeout=40, retries=2):
    data = json.dumps(payload).encode() if payload is not None else None
    hdr = dict(UA, **({"Content-Type": "application/json"} if data else {}))
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=hdr), timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 410):
                raise
            last = e
        except Exception as e:  # network error / timeout / bad JSON
            last = e
        time.sleep(2 * (attempt + 1))
    raise last

def strip_html(s):
    s = html.unescape(s or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()

def fetch_greenhouse(src):
    js = http_json(f"https://boards-api.greenhouse.io/v1/boards/{src['slug']}/jobs?content=true")
    for j in js.get("jobs", []):
        meta = {m["name"]: m.get("value") for m in j.get("metadata") or [] if m.get("name")}
        for d in j.get("departments") or []:
            meta.setdefault("Department", d.get("name"))
        yield dict(id=j["id"], title=j["title"], url=j.get("absolute_url", ""),
                   location=(j.get("location") or {}).get("name", ""), country="",
                   posted=(j.get("first_published") or j.get("updated_at") or "")[:10],
                   meta=meta, desc=strip_html(j.get("content", "")))

def fetch_lever(src):
    for j in http_json(f"https://api.lever.co/v0/postings/{src['slug']}?mode=json"):
        c = j.get("categories") or {}
        meta = {"commitment": c.get("commitment"), "team": c.get("team"), "department": c.get("department")}
        desc = " ".join([j.get("descriptionPlain") or ""] + [strip_html(l.get("content", "")) for l in j.get("lists") or []])
        cc = (j.get("country") or "").upper()
        yield dict(id=j["id"], title=j["text"], url=j.get("hostedUrl", ""),
                   location=c.get("location") or ", ".join(c.get("allLocations") or []),
                   country="United States" if cc == "US" else (cc or ""),
                   posted=ms_to_date(j.get("createdAt")), meta=meta, desc=desc)

def fetch_ashby(src):
    js = http_json(f"https://api.ashbyhq.com/posting-api/job-board/{src['slug']}")
    for j in js.get("jobs", []):
        if j.get("isListed") is False:
            continue
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
        locs = [j.get("location") or ""] + [l.get("location", "") for l in j.get("secondaryLocations") or []]
        meta = {"employment type": j.get("employmentType"), "department": j.get("department"), "team": j.get("team")}
        yield dict(id=j["id"], title=j["title"], url=j.get("jobUrl", ""), location="; ".join(l for l in locs if l),
                   country="" if len(locs) > 1 else country,
                   posted=(j.get("publishedAt") or "")[:10], meta=meta, desc=j.get("descriptionPlain") or "")

def ms_to_date(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat() if ms else ""

def workday_posted(s, today):
    s = (s or "").lower()
    if "today" in s:
        return today.isoformat()
    if "yesterday" in s:
        return (today - dt.timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days", s)
    if m:
        return ("~" if "+" in s else "") + (today - dt.timedelta(days=int(m.group(1)))).isoformat()
    return s

def fetch_workday(src, seen_ids=frozenset(), today=None):
    """Lists ALL postings (paged), tags those inside early-career facets, and fetches detail
    (description, country) for unseen postings without a facet tag, up to WORKDAY_DETAIL_CAP."""
    today = today or dt.datetime.now(NY).date()
    host, tenant, site = src["wd_host"], src["slug"], src["wd_site"]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    first = http_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
    total = first.get("total", 0)
    # facet values that indicate early career (e.g. workerSubType=Intern (Trainee), jobFamilyGroup=Internship)
    early_facets = []
    for f in first.get("facets") or []:
        for v in f.get("values") or []:
            for typ, rx in META_EARLY:
                if v.get("descriptor") and rx.search(v["descriptor"]) and not COOP.search(v["descriptor"]):
                    early_facets.append((f["facetParameter"], v["id"], v["descriptor"], typ))
                    break
            if v.get("descriptor") and COOP.search(v["descriptor"]):
                early_facets.append((f["facetParameter"], v["id"], v["descriptor"], "COOP"))
    tagged = {}
    for param, vid, desc, typ in early_facets:
        for off in range(0, 2000, 20):
            js = http_json(f"{base}/jobs", {"appliedFacets": {param: [vid]}, "limit": 20, "offset": off, "searchText": ""})
            for p in js.get("jobPostings", []):
                tagged.setdefault(p["externalPath"], (typ, f"facet {param}={desc}"))
            if off + 20 >= js.get("total", 0):
                break
            time.sleep(0.3)
    postings = list(first.get("jobPostings", []))
    for off in range(20, min(total, 5000), 20):
        postings += http_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 20, "offset": off, "searchText": ""}).get("jobPostings", [])
        time.sleep(0.3)
    details = 0
    for p in postings:
        path = p.get("externalPath", "")
        meta = {"time type": p.get("timeType")}
        desc, country = "", ""
        if path in tagged:
            typ, why = tagged[path]
            meta["facet"] = "co-op" if typ == "COOP" else why.split("=", 1)[1]
        elif path not in seen_ids and details < WORKDAY_DETAIL_CAP:
            try:
                info = http_json(f"{base}{path}").get("jobPostingInfo", {})
                desc, country = strip_html(info.get("jobDescription", "")), (info.get("country") or {}).get("descriptor", "")
                details += 1
            except Exception:
                pass
        elif path not in seen_ids:
            meta["_detail_skipped"] = "yes"
        yield dict(id=path, title=p.get("title", ""), url=f"https://{host}/{site}{path}",
                   location=p.get("locationsText", ""), country=country,
                   posted=workday_posted(p.get("postedOn"), today), meta=meta, desc=desc)

FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby, "workday": fetch_workday}

# ============================================================== state helpers
def load_json(p, default):
    return json.loads(p.read_text()) if p.exists() else default

def save_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True))
    tmp.replace(p)

def load_sources(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r.get("ats", "").lower() in FETCHERS and r.get("enabled", "1") == "1"]

def src_key(s):
    return f"{s['ats'].lower()}:{s['slug']}"

# ============================================================== run
def run(sources, now=None, fetch=None):
    """Returns result dict. `fetch(src, seen_ids)` can be injected for tests."""
    now = now or dt.datetime.now(NY)
    today = now.date().isoformat()
    seen = load_json(SEEN, {})
    sstate = load_json(SRC_STATE, {})
    res = dict(date=today, new=[], baseline=[], review=[], unclassified=[], errors=[], warnings=[],
               ok=0, total=len(sources), excluded={})
    for s in sources:
        k = src_key(s)
        st = sstate.setdefault(k, {"company": s["company"], "baselined": False, "consecutive_failures": 0})
        seen_ids = {key.split("|", 1)[1] for key in seen if key.startswith(k + "|")}
        try:
            if fetch:
                jobs = list(fetch(s, seen_ids))
            elif s["ats"].lower() == "workday":
                jobs = list(fetch_workday(s, seen_ids, now.date()))
            else:
                jobs = list(FETCHERS[s["ats"].lower()](s))
        except Exception as e:
            st["consecutive_failures"] += 1
            st["last_error"] = f"{today}: {type(e).__name__}: {str(e)[:200]}"
            res["errors"].append(f"{s['company']} ({k}): {type(e).__name__} {str(e)[:150]} "
                                 f"[{st['consecutive_failures']} consecutive failure(s)]")
            continue
        prev = st.get("last_job_count")
        if not jobs and prev:
            res["warnings"].append(f"{s['company']} ({k}) returned 0 jobs (previous run: {prev}) - board may have moved")
        st.update(last_ok=today, consecutive_failures=0, last_job_count=len(jobs))
        st.pop("last_error", None)
        res["ok"] += 1
        first_time = not st["baselined"]
        for j in jobs:
            key = f"{k}|{j['id']}"
            if key in seen:
                continue
            if j.get("meta", {}).pop("_detail_skipped", None):
                res["warnings"].append(f"{s['company']}: detail fetch cap reached; some postings deferred to next run")
                continue   # not marked seen -> retried next run
            us = is_us(j.get("location"), j.get("country", ""))
            seen[key] = today
            if us is False:
                continue
            status, typ, why = classify(j)
            if status == "EXCLUDED":
                res["excluded"][why.split(" (")[0]] = res["excluded"].get(why.split(" (")[0], 0) + 1
                continue
            row = dict(company=s["company"], title=j["title"].strip(), type=typ or "-", status=status, reason=why,
                       location=(j.get("location") or "(not listed)") + ("" if us else " [US? unverified]"),
                       posted=j.get("posted", ""), detected=today, url=j.get("url", ""))
            if status == "CONFIRMED":
                res["baseline" if first_time else "new"].append(row)
            elif status == "REVIEW":
                (res["baseline"] if first_time else res["review"]).append(row)
            else:
                res["unclassified"].append(row)
        st["baselined"] = True
    res["warnings"] = sorted(set(res["warnings"]))
    save_json(SEEN, seen)
    save_json(SRC_STATE, sstate)
    return res

# ============================================================== reporting
def status_line(res):
    failed = len(res["errors"])
    if res["ok"] == 0:
        return "RUN FAILED - no source could be checked. Results below are NOT a statement that nothing is open."
    if failed:
        return (f"INCOMPLETE - {failed} of {res['total']} sources failed. 'No new' applies only to the "
                f"{res['ok']} sources that succeeded.")
    return f"OK - all {res['total']} sources checked."

def _table(rows, with_reason=False):
    h = "| Company | Opportunity | Type | Location | Posted | Date Detected | Application Link |" + (" Why |" if with_reason else "")
    out = [h, "|" + "---|" * (8 if with_reason else 7)]
    for n in sorted(rows, key=lambda x: (x["company"], x["title"])):
        out.append(f"| {n['company']} | {n['title']} | {n['type']} | {n['location']} | {n['posted']} | "
                   f"{n['detected']} | {n['url']} |" + (f" {n['reason']} |" if with_reason else ""))
    return out

def write_report(res):
    REPORTS.mkdir(exist_ok=True)
    d = res["date"]
    L = [f"# Early-career report - {d}", "", f"**Run status: {status_line(res)}**", "",
         f"Sources OK: {res['ok']}/{res['total']} | New confirmed: {len(res['new'])} | Needs review: {len(res['review'])} | "
         f"Baseline (existing, not new): {len(res['baseline'])} | Unclassified (in CSV): {len(res['unclassified'])}", ""]
    if not res["new"] and not res["review"]:
        L.append("No new early-career opportunities in the sources that were checked." if res["ok"] else "")
    for typ in TYPES:
        grp = [n for n in res["new"] if n["type"] == typ]
        if grp:
            L += ["", f"## New - {typ} ({len(grp)})", ""] + _table(grp)
    if res["review"]:
        L += ["", f"## Needs your review ({len(res['review'])}) - level uncertain", ""] + _table(res["review"], True)
    if res["errors"]:
        L += ["", "## Sources that FAILED (not checked this run)", ""] + [f"- {e}" for e in res["errors"]]
    if res["warnings"]:
        L += ["", "## Warnings", ""] + [f"- {w}" for w in res["warnings"]]
    if res["unclassified"]:
        L += ["", f"Unclassified US postings with no level information: {len(res['unclassified'])} - see "
                  f"reports/{d}-unclassified.csv (not shown here to keep alerts readable)."]
    if res["excluded"]:
        L += ["", "Excluded this run: " + ", ".join(f"{k}: {v}" for k, v in sorted(res["excluded"].items()))]
    md = "\n".join(L) + "\n"
    (REPORTS / f"{d}.md").write_text(md)
    fields = ["company", "title", "type", "status", "reason", "location", "posted", "detected", "url"]
    for name, rows in (("", res["new"] + res["review"]), ("-baseline", res["baseline"]), ("-unclassified", res["unclassified"])):
        if rows or name == "":
            with open(REPORTS / f"{d}{name}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    if res["baseline"]:
        B = [f"# Baseline - existing early-career postings ({d})", "",
             "These were already open when each source was first added. They are listed for review, "
             "not reported as new openings.", ""]
        for typ in TYPES:
            grp = [n for n in res["baseline"] if n["type"] == typ and n["status"] == "CONFIRMED"]
            if grp:
                B += ["", f"## {typ} ({len(grp)})", ""] + _table(grp)
        rv = [n for n in res["baseline"] if n["status"] == "REVIEW"]
        if rv:
            B += ["", f"## Level uncertain ({len(rv)})", ""] + _table(rv, True)
        (REPORTS / f"baseline-{d}.md").write_text("\n".join(B) + "\n")
    return md

def maybe_email(subject, body):
    host, user, pw, to = (os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "REPORT_TO"))
    if not all((host, user, pw, to)):
        print("Email not configured (SMTP_HOST/SMTP_USER/SMTP_PASS/REPORT_TO); skipping.")
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT", 465))) as s:
        s.login(user, pw); s.send_message(msg)
    return True

def email_subject(res):
    tag = "" if not res["errors"] else (" [RUN FAILED]" if res["ok"] == 0 else f" [{len(res['errors'])} sources failed]")
    base = f"[Early careers] {len(res['new'])} new, {len(res['review'])} to review - {res['date']}{tag}"
    if res["baseline"] and not res["new"] and not res["review"]:
        base = f"[Early careers] Baseline: {len(res['baseline'])} existing roles (not new) - {res['date']}{tag}"
    return base

# ============================================================== verify & discover
def verify(sources, all_rows=None, fetch=None):
    out = []
    tested = {src_key(s) for s in sources}
    for s in sources:
        try:
            jobs = list(fetch(s, set()) if fetch else (fetch_workday(s) if s["ats"].lower() == "workday" else FETCHERS[s["ats"].lower()](s)))
            us = [j for j in jobs if is_us(j.get("location"), j.get("country", "")) is not False]
            early = sum(1 for j in us if classify(j)[0] == "CONFIRMED")
            out.append(dict(company=s["company"], ats=s["ats"], slug=s["slug"], status="WORKING" if jobs else "WORKING_EMPTY",
                            total_jobs=len(jobs), us_jobs=len(us), early_confirmed=early, checked=dt.date.today().isoformat(), error=""))
        except Exception as e:
            out.append(dict(company=s["company"], ats=s["ats"], slug=s["slug"], status="FAILED", total_jobs="", us_jobs="",
                            early_confirmed="", checked=dt.date.today().isoformat(), error=f"{type(e).__name__}: {str(e)[:120]}"))
        print(out[-1])
    for r in all_rows or []:
        if f"{r.get('ats','').lower()}:{r.get('slug','')}" not in tested:
            disabled = r.get("enabled", "1") != "1"
            out.append(dict(company=r["company"], ats=r.get("ats", ""), slug=r.get("slug", ""),
                            status="DISABLED" if disabled else "UNTESTED", total_jobs="", us_jobs="", early_confirmed="",
                            checked=r.get("last_checked", "") if disabled else "",
                            error=(f"enabled=0; last live check: {r.get('live_status','')} {r.get('notes','')}".strip()
                                   if disabled else "")))
    with open(HERE / "source_status.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
    return out

def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def slug_candidates(name):
    base = re.sub(r"\(.*?\)", "", name).lower().replace("&", "and")
    stop = r"\b(inc|technologies|technology|corporation|corp|company|group|the|holdings|international|us|usa)\b"
    words = re.findall(r"[a-z0-9]+", re.sub(stop, "", base))
    c = {norm(base), "".join(words)} | ({words[0]} if words and len(words[0]) >= 4 else set())
    return [x for x in c if len(x) >= 3]

def discover(companies, getter=http_json):
    """Probe public board endpoints for each company name. Returns candidate rows (never auto-enabled)."""
    found = []
    for name in companies:
        first = norm(re.split(r"[ (]", name)[0])
        for slug in slug_candidates(name):
            try:
                b = getter(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
                ok = first[:5] in norm(b.get("name"))
                found.append(dict(company=name, ats="greenhouse", slug=slug, board_name=b.get("name"),
                                  name_match="yes" if ok else "NO - check"))
            except Exception:
                pass
            try:
                js = getter(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
                jobs = js.get("jobs", [])
                ok = any(first[:5] in norm(j.get("descriptionPlain", "")[:3000]) for j in jobs[:5])
                if jobs:
                    found.append(dict(company=name, ats="ashby", slug=slug, board_name=f"{len(jobs)} jobs",
                                      name_match="yes" if ok else "NO - check"))
            except Exception:
                pass
            try:
                js = getter(f"https://api.lever.co/v0/postings/{slug}?mode=json")
                ok = any(first[:5] in norm((j.get("descriptionPlain") or "")[:3000]) for j in js[:5])
                if js:
                    found.append(dict(company=name, ats="lever", slug=slug, board_name=f"{len(js)} jobs",
                                      name_match="yes" if ok else "NO - check"))
            except Exception:
                pass
            time.sleep(0.2)
    return found

# ============================================================== main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["run", "verify", "discover"])
    ap.add_argument("--sources", default=str(HERE / "ats_sources.csv"))
    ap.add_argument("--master", default=str(HERE / "US_Early_Careers_300_Companies.csv"))
    a = ap.parse_args(argv)
    if a.mode == "verify":
        with open(a.sources, newline="", encoding="utf-8") as f:
            all_rows = list(csv.DictReader(f))
        verify(load_sources(a.sources), all_rows)
        return 0
    if a.mode == "discover":
        with open(a.sources, newline="", encoding="utf-8") as f:
            have = {r["company"] for r in csv.DictReader(f)}
        with open(a.master, newline="", encoding="utf-8") as f:
            todo = [r["Company_Name"] for r in csv.DictReader(f) if r["Company_Name"] not in have]
        rows = discover(todo)
        with open(HERE / "discovered_sources.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["company", "ats", "slug", "board_name", "name_match"])
            w.writeheader(); w.writerows(rows)
        print(f"{len(rows)} candidate boards written to discovered_sources.csv (review, then add to ats_sources.csv)")
        return 0
    res = run(load_sources(a.sources))
    md = write_report(res)
    print(md)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a") as f:
            f.write(md)
    if res["new"] or res["review"] or res["errors"] or res["baseline"]:
        body = md + (("\n\n" + (REPORTS / f"baseline-{res['date']}.md").read_text()) if res["baseline"] else "")
        maybe_email(email_subject(res), body)
    elif os.environ.get("EMAIL_EMPTY_STATUS", "1") == "1":
        maybe_email(email_subject(res), md)
    if res["total"] and len(res["errors"]) / res["total"] > FAIL_EXIT_RATIO:
        print(f"::error::{len(res['errors'])}/{res['total']} sources failed")
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
