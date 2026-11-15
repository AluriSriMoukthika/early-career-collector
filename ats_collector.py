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
import urllib.error, urllib.parse, urllib.request
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import relevance

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
MAX_AGE_HOURS = float(os.environ.get("MAX_AGE_HOURS", "24"))             # email only postings this fresh
EMAIL_CAP_PER_COMPANY = int(os.environ.get("EMAIL_CAP_PER_COMPANY", "10"))  # rows per company in the email
SEEN_RETENTION_DAYS = int(os.environ.get("SEEN_RETENTION_DAYS", "120"))   # older history can't pass the 24h rule anyway

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
    ("Internship", r"\bintern(ship)?s?\b|\bsummer (analyst|associate)\b|\bfellowship\b|\bextern(ship)?\b"),
    ("New Graduate", r"new ?grad|university grad|recent grad|\bgrads?\b|\bgraduate\b|class of 20\d\d|20\d\d start|\bcampus\b"),
    ("Entry-Level", r"entry[- ]level|\bapprentice(ship)?\b|\bjunior\b|\bjr\.?\b|\b(engineer|developer|analyst|scientist|associate|specialist)\s*(i|1)\b|\blevel (i|1)\b|\bassociate\b(?!.*(general counsel|director|partner))"),
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

MILITARY = re.compile(r"skillbridge|military fellowship|transitioning (service ?members?|military)|hiring our heroes|"
                      r"active[- ]duty service members? (in|within) (their|the) final", re.I)

def _flat(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [x for i in v for x in _flat(i)]
    if isinstance(v, dict):
        return []        # e.g. pay ranges
    s = str(v).strip()
    return [] if s.lower() in ("", "null", "none") else [s]

PREFERRED = re.compile(r"\bpreferred\b|nice[- ]to[- ]have|bonus points|\bpluses\b|\bbonus\b|desired (skills|qualifications|experience)|"
                       r"\bideal(ly)?\b|it would be great if|additional qualifications", re.I)

def min_years(text):
    """Years of experience REQUIRED by the posting, or None.
    Only the text before the first 'preferred / nice to have' marker counts, and because every
    required item must be met, the requirement is the LARGEST lower bound found there
    (e.g. '5+ years of experience ... 1+ year of experience with Python' -> 5)."""
    text = text or ""
    m = PREFERRED.search(text)
    required = text[:m.start()] if m else text
    vals = [int(x.group(1)) for x in YEARS.finditer(required)]
    return max(vals) if vals else None

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
        if relevance.ATL_METRO.search(p) or relevance.GEORGIA.search(p):   # "Atlanta", "Alpharetta", "Savannah, GA" ...
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
        if attempt < retries:              # pause only before an actual retry
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
                   posted=(j.get("first_published") or "")[:10], posted_ts=j.get("first_published") or "",
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
                   posted=ms_to_date(j.get("createdAt")),
                   posted_ts=dt.datetime.fromtimestamp(j["createdAt"] / 1000, dt.timezone.utc).isoformat() if j.get("createdAt") else "",
                   meta=meta, desc=desc)

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
                   posted=(j.get("publishedAt") or "")[:10], posted_ts=j.get("publishedAt") or "",
                   meta=meta, desc=j.get("descriptionPlain") or "")

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
        elif path not in seen_ids and relevance.title_unrelated(p.get("title", "")):
            pass                                   # e.g. store/driver/warehouse roles: rejected on title, no detail call
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
                   posted=workday_posted(p.get("postedOn"), today), posted_rel=p.get("postedOn") or "",
                   meta=meta, desc=desc)

def employment_type(job, typ):
    for k, v in (job.get("meta") or {}).items():
        if re.search(r"employment type|commitment|time type|worker sub-?type", k, re.I):
            vals = _flat(v)
            if vals:
                return vals[0]
    return "Internship" if typ == "Internship" else "Not stated"

def posting_age_hours(job, now):
    """Hours since the posting was published, or None if the feed gives no date.
    Workday only says 'Posted Today / Yesterday / N Days Ago', so it is mapped to 0 / 24 / N*24."""
    rel = (job.get("posted_rel") or "").lower()
    if rel:
        if "today" in rel:
            return 0.0
        if "yesterday" in rel:
            return 24.0
        m = re.search(r"(\d+)\+?\s*days?", rel)
        return float(m.group(1)) * 24 if m else None
    ts = job.get("posted_ts") or ""
    if not ts:
        return None
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (now - t).total_seconds() / 3600)

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
    res = dict(date=today, stamp=now.strftime("%Y-%m-%d_%H%M"), new=[], baseline=[], review=[], unclassified=[], older=[], errors=[], warnings=[],
               ok=0, total=len(sources), excluded={}, window_hours=MAX_AGE_HOURS)
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
        # Freshness window: normally MAX_AGE_HOURS; if the previous successful check was longer ago
        # (a missed day), widen it to cover the gap so nothing posted in between is lost.
        window = MAX_AGE_HOURS
        if st.get("last_ok_ts"):
            gap = (now - dt.datetime.fromisoformat(st["last_ok_ts"])).total_seconds() / 3600
            window = max(window, gap + 1)
        res["window_hours"] = max(res["window_hours"], window)
        st.update(last_ok=today, last_ok_ts=now.isoformat(timespec="seconds"), consecutive_failures=0, last_job_count=len(jobs))
        st.pop("last_error", None)
        res["ok"] += 1
        first_time = not st["baselined"]
        deferred = 0
        for j in jobs:
            key = f"{k}|{j['id']}"
            if key in seen:
                continue
            if j.get("meta", {}).pop("_detail_skipped", None):
                deferred += 1
                continue   # not marked seen -> retried next run
            us = is_us(j.get("location"), j.get("country", ""))
            seen[key] = today
            if us is False:
                continue
            status, typ, why = classify(j)
            if status == "EXCLUDED":
                res["excluded"][why.split(" (")[0]] = res["excluded"].get(why.split(" (")[0], 0) + 1
                continue
            # --- military transition programs (DoD SkillBridge etc.) are only open to service members
            meta_txt = " ".join(str(v) for v in (j.get("meta") or {}).values())
            if MILITARY.search(f"{j.get('title', '')} {meta_txt} {(j.get('desc') or '')[:3000]}"):
                res["excluded"]["military transition program"] = res["excluded"].get("military transition program", 0) + 1
                continue
            # --- relevance to the candidate profile (title + full description)
            a = relevance.assess(j)
            if a["relevance"] == "UNRELATED":
                res["excluded"]["not relevant to profile"] = res["excluded"].get("not relevant to profile", 0) + 1
                continue
            if a["phd_only"]:
                res["excluded"]["PhD required"] = res["excluded"].get("PhD required", 0) + 1
                continue
            if a["auth_status"] == "EXCLUDE":            # explicit U.S. citizenship / security clearance requirement
                res["excluded"]["Excluded — Citizenship/Clearance"] = res["excluded"].get("Excluded — Citizenship/Clearance", 0) + 1
                continue
            if a["auth_status"] == "AMBIGUOUS" and status == "CONFIRMED":
                status, why = "REVIEW", f"{why}; work-authorization wording needs review"
            if a["auth_status"] == "NO_SPONSORSHIP":
                res["no_sponsorship_kept"] = res.get("no_sponsorship_kept", 0) + 1
            if status == "CONFIRMED" and a["relevance"] == "BORDERLINE":
                status, why = "REVIEW", f"{why}; relevance uncertain"
            elif status == "REVIEW" and a["relevance"] == "BORDERLINE":
                status = "UNCLASSIFIED"
            elif status == "UNCLASSIFIED" and a["relevance"] == "MATCH":
                status, typ, why = "REVIEW", "Early-Career Full-Time", "relevant role, experience level not stated"
            age = posting_age_hours(j, now)
            yrs = min_years(j.get("desc", ""))
            row = dict(company=s["company"], title=j["title"].strip(), type=typ or "-", status=status, reason=why,
                       location=(j.get("location") or "(not listed)") + ("" if us else " [US? unverified]"),
                       region=relevance.region(j.get("location")), category=a["family"],
                       employment=employment_type(j, typ), experience=("Not stated" if yrs is None else f"{yrs}+ yrs required" if yrs else "0 yrs / no minimum"),
                       education=a["education"], work_auth=a["auth"], match=a["why"],
                       posted=(j.get("posted", "") or "(no date)"), age_hours="" if age is None else round(age, 1),
                       detected=today, url=j.get("url", ""))
            if status == "UNCLASSIFIED":
                res["unclassified"].append(row)
            elif first_time:
                res["baseline"].append(row)
            elif age is not None and age > window:
                row["reason"] += f"; posted {age:.0f}h ago (older than {window:.0f}h window)"
                res["older"].append(row)
            else:
                if age is None:
                    row["reason"] += "; feed gives no posting date"
                res["new" if status == "CONFIRMED" else "review"].append(row)
        if deferred:
            res["warnings"].append(f"{s['company']}: {deferred} postings deferred to the next run (Workday detail cap)"
                                   + ("; source stays in baseline mode until all are processed" if first_time else ""))
        else:
            st["baselined"] = True   # a source leaves baseline mode only once every posting has been processed
    res["warnings"] = sorted(set(res["warnings"]))
    cutoff = (now.date() - dt.timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
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

def cap_per_company(rows, cap, used=None):
    """Keep at most `cap` rows per company (counting rows already shown via `used`)."""
    used = used if used is not None else {}
    shown, hidden = [], {}
    for n in sorted(rows, key=lambda x: (x["company"], TYPES.index(x["type"]) if x["type"] in TYPES else 99, x["title"])):
        if used.get(n["company"], 0) < cap:
            shown.append(n); used[n["company"]] = used.get(n["company"], 0) + 1
        else:
            hidden[n["company"]] = hidden.get(n["company"], 0) + 1
    return shown, hidden

def _table(rows, with_reason=False):
    h = "| Company | Opportunity | Type | Location | Posted | Date Detected | Application Link |" + (" Why |" if with_reason else "")
    out = [h, "|" + "---|" * (8 if with_reason else 7)]
    for n in sorted(rows, key=lambda x: (x["company"], x["title"])):
        out.append(f"| {n['company']} | {n['title']} | {n['type']} | {n['location']} | {n['posted']} | "
                   f"{n['detected']} | {n['url']} |" + (f" {n['reason']} |" if with_reason else ""))
    return out

def _entries(rows, review=False):
    out = []
    for n in rows:
        out += [f"- **{n['title']}** - {n['company']}",
                f"  - {n['location']} | {n.get('employment', 'Not stated')} | {n.get('category', '-')} | {n['type']}",
                f"  - Experience: {n.get('experience', 'Not stated')} | Education: {n.get('education', 'Not stated')} | Posted: {n['posted']}",
                f"  - Work authorization: {n.get('work_auth', 'Not stated (unverified)')}",
                f"  - Why it matches: {n.get('match', '')}"]
        if review:
            out.append(f"  - Why it needs review: {n['reason']}")
        out += [f"  - Apply: {n['url']}", ""]
    return out

def write_report(res):
    REPORTS.mkdir(exist_ok=True)
    d = res["date"]
    st = res.get("stamp", d)          # one set of files per run (several runs per day)
    win = res.get("window_hours", MAX_AGE_HOURS)
    L = [f"# Early-career report - {d}", "", f"**Run status: {status_line(res)}**", "",
         f"Relevant postings published in the last {win:.0f} hours: New confirmed: {len(res['new'])} | Needs review: {len(res['review'])}", "",
         f"Sources OK: {res['ok']}/{res['total']} | Newly detected but older than {win:.0f}h (CSV only): {len(res.get('older', []))} | "
         f"Existing openings recorded as baseline (not emailed): {len(res['baseline'])} | Unclassified (CSV only): {len(res['unclassified'])}", ""]
    if not res["new"] and not res["review"]:
        L.append(f"No new relevant early-career opportunities published in the last {win:.0f} hours in the sources that were checked."
                 if res["ok"] else "")
    hidden = {}
    def add_hidden(h):
        for k, v in h.items():
            hidden[k] = hidden.get(k, 0) + v
    sections = [("Atlanta metro", "1. Atlanta area"), ("Georgia", "2. Elsewhere in Georgia"), ("United States", "3. Elsewhere in the United States")]
    for reg, heading in sections:
        rows = [n for n in res["new"] if n.get("region", "United States") == reg]
        if rows:
            shown, h = cap_per_company(rows, EMAIL_CAP_PER_COMPANY); add_hidden(h)
            L += ["", f"## {heading} - {len(rows)} new relevant", ""] + _entries(shown)
    if res["review"]:
        order = {r: i for i, (r, _) in enumerate(sections)}
        rv = sorted(res["review"], key=lambda n: order.get(n.get("region", "United States"), 9))
        shown, h = cap_per_company(rv, EMAIL_CAP_PER_COMPANY); add_hidden(h)
        shown.sort(key=lambda n: order.get(n.get("region", "United States"), 9))
        L += ["", f"## 4. Needs your review - {len(res['review'])} (relevant, but level or fit uncertain)", ""] + _entries(shown, review=True)
    if hidden:
        L += ["", f"Showing up to {EMAIL_CAP_PER_COMPANY} roles per company in each section. The full list is in reports/{st}.csv:"] + \
             [f"- +{v} more {k} roles in today's CSV" for k, v in sorted(hidden.items(), key=lambda x: -x[1])]
    if res["errors"]:
        L += ["", "## Sources that FAILED (not checked this run)", ""] + [f"- {e}" for e in res["errors"]]
    if res["warnings"]:
        L += ["", "## Warnings", ""] + [f"- {w}" for w in res["warnings"]]
    if res["unclassified"]:
        L += ["", f"Unclassified US postings with no level information: {len(res['unclassified'])} - see "
                  f"reports/{st}-unclassified.csv (not shown here to keep alerts readable)."]
    if res.get("no_sponsorship_kept"):
        L += ["", f"Included — No Sponsorship: {res['no_sponsorship_kept']} posting(s) say they do not sponsor visas; kept and flagged."]
    if res["excluded"]:
        L += ["", "Excluded this run: " + ", ".join(f"{k}: {v}" for k, v in sorted(res["excluded"].items()))]
    md = "\n".join(L) + "\n"
    (REPORTS / f"{st}.md").write_text(md)
    fields = ["company", "title", "region", "location", "employment", "category", "type", "status", "experience", "education",
              "work_auth", "match", "reason", "posted", "age_hours", "detected", "url"]
    older = [dict(r, status="OLDER_THAN_WINDOW") for r in res.get("older", [])]
    for name, rows in (("", res["new"] + res["review"] + older), ("-baseline", res["baseline"]), ("-unclassified", res["unclassified"])):
        if rows or name == "":
            with open(REPORTS / f"{st}{name}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
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
        (REPORTS / f"baseline-{st}.md").write_text("\n".join(B) + "\n")
    # machine-readable summary used by scripts/post_issue.py
    (REPORTS / f"{st}-summary.json").write_text(json.dumps(dict(
        date=d, status=status_line(res), ok=res["ok"], total=res["total"], failed=len(res["errors"]),
        new=len(res["new"]), review=len(res["review"]), older=len(res.get("older", [])),
        baseline=len(res["baseline"]), unclassified=len(res["unclassified"]), window_hours=win,
        zero_job_warnings=sum(1 for w in res["warnings"] if "returned 0 jobs" in w), stamp=st), indent=1))
    (REPORTS / "latest.json").write_text(json.dumps({"stamp": st, "date": d, "run_id": os.environ.get("GITHUB_RUN_ID", "")}))
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
    return f"[Early careers] {len(res['new'])} new, {len(res['review'])} to review - {res['date']}{tag}"

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

def _fast_get(url):
    return http_json(url, timeout=12, retries=0)

NAME_STOP = r"\b(inc|corp|corporation|company|co|group|the|holdings|international|us|usa|llc|ltd|plc|and)\b"

def core_name(name):
    """'Genuine Parts Company' -> 'genuineparts'; '(US)' and suffixes removed."""
    return norm(re.sub(NAME_STOP, " ", re.sub(r"\(.*?\)", " ", (name or "").replace("&", " and ")), flags=re.I))

def board_name_matches(company, board):
    """Greenhouse board name must be the company's name, not just share its first word
    (rejects 'Northside Veterinary Clinic' for Northside Hospital, 'General Interest' for General Mills)."""
    c, b = core_name(company), core_name(board)
    if not c or not b:
        return False
    return c == b or c in b or (b in c and len(b) >= 0.8 * len(c))

def text_names_company(company, text):
    """Lever/Ashby give no company name: require the full company name in the posting text."""
    return core_name(company) in norm(text)

def discover(companies, getter=_fast_get):
    """Probe public board endpoints for each company name. Returns candidate rows (never auto-enabled)."""
    found = []
    for name in companies:
        for slug in slug_candidates(name):
            try:
                b = getter(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
                ok = board_name_matches(name, b.get("name"))
                found.append(dict(company=name, ats="greenhouse", slug=slug, board_name=b.get("name"),
                                  name_match="yes" if ok else "NO - check"))
            except Exception:
                pass
            try:
                js = getter(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
                jobs = js.get("jobs", [])
                ok = any(text_names_company(name, j.get("descriptionPlain", "")[:5000]) for j in jobs[:5])
                if jobs:
                    found.append(dict(company=name, ats="ashby", slug=slug, board_name=f"{len(jobs)} jobs",
                                      name_match="yes" if ok else "NO - check"))
            except Exception:
                pass
            try:
                js = getter(f"https://api.lever.co/v0/postings/{slug}?mode=json")
                ok = any(text_names_company(name, (j.get("descriptionPlain") or "")[:5000] + (j.get("additionalPlain") or "")) for j in js[:5])
                if js:
                    found.append(dict(company=name, ats="lever", slug=slug, board_name=f"{len(js)} jobs",
                                      name_match="yes" if ok else "NO - check"))
            except Exception:
                pass
            time.sleep(0.1)
    return found

WD_HOSTS = ("wd5", "wd1", "wd3", "wd12", "wd103", "wd501")     # most common first; stops at the first hit
WD_SITE_GUESSES = ("External", "Careers", "{t}", "External_Careers", "{T}Careers")

def _wd_site_from_root(host, opener=None):
    """Workday career-site roots usually redirect to /<lang>/<site> or /<site>; return <site> or None."""
    opener = opener or (lambda url: urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=8))
    try:
        with opener(f"https://{host}/") as r:
            final = r.geturl()
    except Exception:
        return None
    parts = [p for p in urllib.parse.urlparse(final).path.split("/") if p]
    parts = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
    return parts[0] if parts else None

def discover_workday(company, tenants, poster=None, root_site=_wd_site_from_root):
    """Find a working Workday career site for any of the tenant guesses. Returns candidate rows."""
    poster = poster or (lambda url: http_json(url, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, timeout=8, retries=0))
    found = []
    for t in tenants:
        for wd in WD_HOSTS:
            host = f"{t}.{wd}.myworkdayjobs.com"
            guesses = [g.format(t=t, T=t.capitalize()) for g in WD_SITE_GUESSES]
            site = root_site(host)
            if site:
                guesses.insert(0, site)
            for g in dict.fromkeys(guesses):
                try:
                    js = poster(f"https://{host}/wday/cxs/{t}/{g}/jobs")
                except Exception:
                    continue
                if isinstance(js, dict) and "total" in js:
                    found.append(dict(company=company, ats="workday", slug=t, wd_host=host, wd_site=g,
                                      board_name=f"{js.get('total')} jobs", name_match="tenant name - check the site is the company's"))
                    return found
            time.sleep(0.2)
    return found

DISCOVER_FIELDS = ["company", "ats", "slug", "wd_host", "wd_site", "board_name", "name_match"]
PROGRESS = STATE / "discover_progress.json"

def discover_all(todo, wd_tenants, out_csv, budget_minutes=None, workers=8, probe=None, now=time.monotonic):
    """Probe companies in parallel, append results to out_csv as each company finishes, and record
    progress in state/discover_progress.json so a later run resumes where this one stopped.
    Stops starting new companies after `budget_minutes` (default DISCOVER_MAX_MINUTES or 40)."""
    import concurrent.futures as cf
    budget = 60 * float(budget_minutes if budget_minutes is not None else os.environ.get("DISCOVER_MAX_MINUTES", "40"))
    done = load_json(PROGRESS, {})
    queue = [c for c in todo if c not in done]
    probe = probe or (lambda name: discover([name]) + (discover_workday(name, wd_tenants[name]) if wd_tenants.get(name) else []))
    new_file = not out_csv.exists()
    start, found, finished = now(), 0, 0
    with open(out_csv, "a", newline="", encoding="utf-8") as fh, cf.ThreadPoolExecutor(max_workers=workers) as ex:
        w = csv.DictWriter(fh, fieldnames=DISCOVER_FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        pending, it = {}, iter(queue)
        def submit_more():
            while len(pending) < workers and now() - start < budget:
                name = next(it, None)
                if name is None:
                    return
                pending[ex.submit(probe, name)] = name
        submit_more()
        while pending:
            fin, _ = cf.wait(list(pending), return_when=cf.FIRST_COMPLETED)
            for f in fin:
                name = pending.pop(f)
                try:
                    rows = f.result()
                except Exception:
                    rows = []
                w.writerows(rows); fh.flush()
                found += len(rows); finished += 1
                done[name] = dt.date.today().isoformat()
                save_json(PROGRESS, done)
            submit_more()
    remaining = len([c for c in todo if c not in done])
    print(f"discover: probed {finished} companies this run, {found} candidate boards found, {remaining} still to probe"
          + (" (time budget reached - run discover again to continue)" if remaining else ""))
    return finished, found, remaining

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
            master = [r for r in csv.DictReader(f) if r["Company_Name"] not in have]
        wd = {}
        cand = HERE / "atlanta_candidates.csv"
        if cand.exists():
            with open(cand, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    wd[r["company"]] = [t.strip() for t in (r.get("workday_tenant_guesses") or "").split(";") if t.strip()]
        # Atlanta-area employers first, then everyone else
        atl = [r["Company_Name"] for r in master if "Atlanta-metro" in r.get("Notes", "")]
        todo = atl + [r["Company_Name"] for r in master if r["Company_Name"] not in atl]
        discover_all(todo, wd, HERE / "discovered_sources.csv")
        return 0
    res = run(load_sources(a.sources))
    md = write_report(res)
    print(md)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a") as f:
            f.write(md)
    # The email carries only postings published within the window (new + needs review).
    # Existing openings (baseline), older postings and unclassified ones stay in the repo's CSV files.
    if res["new"] or res["review"] or res["errors"] or os.environ.get("EMAIL_EMPTY_STATUS", "1") == "1":
        maybe_email(email_subject(res), md)
    if res["total"] and len(res["errors"]) / res["total"] > FAIL_EXIT_RATIO:
        print(f"::error::{len(res['errors'])}/{res['total']} sources failed")
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
