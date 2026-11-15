"""Job-description relevance filter for a Data Science / Analytics / AI / Software profile.

assess(job) -> dict with
  relevance   MATCH | BORDERLINE | UNRELATED
  family      role family (e.g. "Data Science", "Software Engineering")
  skills      technical skills found in the description
  education   short text, e.g. "BS/MS accepted", "PhD required", "Not stated"
  phd_only    True if the posting requires a PhD and does not accept BS/MS or equivalent experience
  auth        work-authorization restriction text, or "Not stated (unverified)"
  why         one-line explanation of the decision
Rule-based and deterministic (no external services). Tune the lists below.
"""
import re

FAMILIES = [  # (family, title regex) - first match wins
    ("Computer Vision", r"computer vision|\bcv engineer|perception|imaging (engineer|scientist)|vision (engineer|scientist|research)"),
    ("Machine Learning / AI", r"machine learning|\bml\b|mlops|\bai\b|a\.i\.|artificial intelligence|generative|gen ?ai|\bllm|agentic|deep learning|\bnlp\b|natural language"),
    ("Data Science", r"data scien|applied scien|decision scien|quantitative (researcher|analyst)|statistician"),
    ("Data Engineering", r"data engineer|analytics engineer|data platform|\betl\b|big data|database (engineer|developer|administrator)|data (architect|infrastructure)"),
    ("AI Automation", r"intelligent automation|\brpa\b|automation (engineer|developer)|process automation"),
    ("Digital Transformation", r"digital transformation|technology transformation|digital (analyst|engineer|product)"),
    ("Research", r"research (scientist|engineer|associate|assistant|analyst|fellow)"),
    ("Software Engineering", r"software|\bsde\b|\bswe\b|developer|programmer|full[- ]?stack|back[- ]?end|front[- ]?end|\bweb\b|mobile (engineer|developer)|\bios\b|android|"
                             r"platform engineer|cloud engineer|devops|site reliability|\bsre\b|qa engineer|test (automation )?engineer|forward deployed"),
    ("Analytics", r"\banalytics?\b|data analyst|business analyst|business intelligence|\bbi (analyst|developer|engineer)|product analyst|operations analyst|"
                  r"insights? analyst|reporting analyst|technology analyst|strategy (and|&) analytics|risk analyst|financial analyst.*data"),
    ("IT / Technical Analyst", r"systems analyst|technical analyst|\bit analyst|information technology|\bit (engineer|associate|specialist)|technology (associate|analyst|program)|"
                               r"solutions engineer|security (analyst|engineer)|cyber|network engineer|systems engineer|"
                               r"\b(it|computer|network|desktop) (technician|support)|help ?desk"),
]
FAMILIES = [(f, re.compile(p, re.I)) for f, p in FAMILIES]

UNRELATED_TITLE = re.compile(
    r"\bweld|welder|machinist|mechanic\b|\bassembl|fabricat|\btechnician\b|warehouse|forklift|material handler|\bdriver\b|"
    r"\boperator\b|production (associate|worker|supervisor)|\bpainter\b|electrician|plumber|\bhvac\b|carpenter|construction|"
    r"\bsales\b|account executive|business development rep|\bbdr\b|\bsdr\b|retail|\bstore\b|cashier|merchandis|"
    r"customer (service|support|success|experience)|call center|receptionist|administrative|office assistant|executive assistant|"
    r"recruit|talent acquisition|human resources|\bhr\b|payroll|accountant|accounting|bookkeep|\blegal\b|paralegal|attorney|counsel|"
    r"\bnurse|nursing|physician|pharmac|therapist|dental|medical assistant|clinical|"
    r"marketing (coordinator|specialist|associate|intern)|copywriter|social media|graphic design|\bevent|chef|\bcook\b|"
    r"janitor|custodian|security officer|guard\b|pilot\b|flight attendant|aircraft maintenance|crew\b", re.I)
IT_TECHNICIAN = re.compile(r"\b(it|computer|network|data ?center|software|systems?) technician", re.I)

SKILLS = {
    "Python": r"\bpython\b", "SQL": r"\bsql\b|postgres|mysql|t-sql", "machine learning": r"machine learning|\bml\b",
    "deep learning": r"deep learning|neural network", "PyTorch": r"pytorch", "TensorFlow": r"tensorflow|keras",
    "scikit-learn": r"scikit|sklearn", "OpenCV": r"opencv", "computer vision": r"computer vision|image (processing|recognition)",
    "NLP": r"\bnlp\b|natural language", "LLMs": r"\bllms?\b|large language model|\bgpt\b|transformer model",
    "generative AI": r"generative ai|gen ?ai", "agentic AI": r"agentic|ai agents?", "LangChain/LangGraph": r"langchain|langgraph|llamaindex",
    "statistics": r"statistic|regression|hypothesis test|a/b test", "data analysis": r"data analys|analyz(e|ing) data|analytics",
    "data visualization": r"tableau|power ?bi|looker|data visuali[sz]ation|dashboards?", "pandas/NumPy": r"pandas|numpy",
    "Spark": r"\bspark\b|pyspark|databricks", "Airflow/dbt": r"airflow|\bdbt\b", "data pipelines": r"data pipelines?|\betl\b|\belt\b",
    "data warehouse": r"snowflake|bigquery|redshift|data warehouse|data lake", "cloud": r"\baws\b|azure|\bgcp\b|google cloud",
    "Docker/Kubernetes": r"docker|kubernetes", "MLOps": r"mlops|mlflow|model deployment|kubeflow", "Git": r"\bgit\b|github|gitlab",
    "Linux": r"\blinux\b|unix", "Java": r"\bjava\b", "C++": r"c\+\+", "JavaScript/TypeScript": r"javascript|typescript|react|node\.?js",
    "Go/Rust/C#": r"\bgolang\b|\bgo\b(?= |,)|\brust\b|c#|\.net", "software development": r"software development|object[- ]oriented|data structures|algorithms|rest(ful)? api|microservices",
    "databases": r"database|nosql|mongodb|dynamodb", "automation": r"\brpa\b|uipath|power automate|automation anywhere",
}
SKILLS = {k: re.compile(v, re.I) for k, v in SKILLS.items()}
CORE = {"Python", "SQL", "machine learning", "deep learning", "software development", "data analysis", "statistics",
        "computer vision", "LLMs", "data pipelines", "Java", "C++", "JavaScript/TypeScript"}
HARDWARE = re.compile(r"\brf\b|antenna|\bpcb\b|\basic\b|\bfpga\b|solidworks|\bcad\b|catia|thermal|structural analysis|"
                      r"mechanical design|tig\b|mig\b|blueprints?|hand tools|torque|soldering|wire harness|cnc", re.I)

PHD_SIGNAL = re.compile(r"ph\.?\s?d|doctora", re.I)
PHD_REQUIRED = re.compile(r"(ph\.?\s?d\.?|doctoral degree|doctorate)[^.]{0,60}\b(required|is required|must)\b|"
                          r"(must|required to) (have|hold|possess)[^.]{0,30}(ph\.?\s?d|doctora)|"
                          r"(currently|actively) (enrolled|pursuing)[^.]{0,30}(ph\.?\s?d|doctora)|ph\.?\s?d\.? (candidate|student)s?\b", re.I)
BS_MS_OK = re.compile(r"bachelor|master'?s|\bm\.?s\.?\b|\bb\.?s\.?\b|\bbs/ms\b|\bms/phd\b|ms or phd|equivalent (practical |professional )?experience", re.I)
# ------------------------------------------------------------------ work authorization
# EXCLUDE: explicit U.S. citizenship / nationals-only / security-clearance requirements
#          (U.S. clearances are only granted to U.S. citizens, so clearance eligibility implies citizenship).
# NO_SPONSORSHIP / WORK_AUTH: kept and flagged.  AMBIGUOUS: kept but moved to Needs Review.
REVIEW_ON_BACKGROUND_CHECK = True          # set False if "background check" moves too many roles to review
CLEARANCE = (r"(secret|top[- ]secret|ts/sci|\bts\b|sci|dod|doe|public trust|q|l|security)[- ]?(level )?clearance|clearance (level )?\(?(secret|top secret|ts/sci)")
CITIZEN_EXCLUDE = [re.compile(p, re.I) for p in (
    r"must be (a )?(united states|u\.?s\.?) citizen(?!s? or)(?! or)",
    r"(united states|u\.?s\.?) citizenship (is )?(required|mandatory|a requirement)",
    r"requires? (united states|u\.?s\.?) citizenship",
    r"(only|solely) (open to )?(united states|u\.?s\.?) (citizens|nationals)",
    r"(united states|u\.?s\.?) (citizens|nationals) only",
    r"must be (a )?(united states|u\.?s\.?) national",
)]
CLEARANCE_EXCLUDE = [re.compile(p, re.I) for p in (
    rf"(active|current|existing|valid|interim)[^.;]{{0,25}}({CLEARANCE})",
    rf"({CLEARANCE})[^.;]{{0,25}}(is |are )?(required|mandatory|needed)",
    rf"must (have|hold|possess|maintain|be able to obtain|obtain)[^.;]{{0,40}}({CLEARANCE})",
    rf"(eligib(le|ility)|ability|able) to (obtain|receive|be granted|hold)[^.;]{{0,30}}({CLEARANCE})",
    r"\bts/sci\b",
)]
SOFTENERS = re.compile(r"preferred|a plus|is a bonus|nice to have|desired|desirable|advantageous|not required", re.I)
NO_SPONSOR = [re.compile(p, re.I) for p in (
    r"(not|unable to|will not|won't|cannot|can't|does not|do not|is not able to) (be able to )?(provide |offer |support |consider )?(visa |immigration |employment visa )?sponsor",
    r"without (the need for )?(current or future |now or in the future )?(visa |immigration )?sponsorship",
    r"sponsorship (is )?(not |un)available", r"no (visa |immigration )?sponsorship",
)]
WORK_AUTH = re.compile(r"(authori[sz]ed|eligible|legally (able|permitted)) to work in the (united states|u\.?s\.?)|existing (u\.?s\.? )?work authori[sz]ation", re.I)
AMBIGUOUS = [(t, re.compile(p, re.I)) for t, p in (
    ("U.S. person / export control", r"\bu\.?s\.? persons?\b|export control|export administration|\bitar\b|\bear\b(?= regulation| compliance)"),
    ("permanent work authorization", r"permanent (work|employment) authori[sz]ation|permanently authori[sz]ed"),
    ("citizen or permanent resident", r"(united states|u\.?s\.?) citizens?( or|,) (lawful )?(permanent residents?|green card)"),
    ("security screening", r"security screening|security vetting|suitability (check|investigation)"),
    ("background check", r"background (check|investigation|screening)"),
    ("security clearance mentioned (not as a requirement)", CLEARANCE),
)]

def _sentences(text):
    # "U.S." / "U.S.A." must not end a sentence ("Must be a U.S. citizen.")
    text = re.sub(r"\b[Uu]\.\s?[Ss]\.(\s?[Aa]\.)?", "US", text or "")
    text = re.sub(r"\b(e\.g|i\.e|etc|incl|approx)\.", r"\1", text, flags=re.I)
    return [x for x in re.split(r"(?<=[.;!?])\s+|\n+", text) if x.strip()]

def auth_assessment(desc):
    """Returns (status, label). status: EXCLUDE | AMBIGUOUS | NO_SPONSORSHIP | WORK_AUTH | NOT_STATED."""
    ambiguous, notes = [], []
    for sent in _sentences(desc):
        cit = next((rx for rx in CITIZEN_EXCLUDE if rx.search(sent)), None)
        if cit and not re.search(r"or (lawful )?permanent resident|or green card|or (other )?authori[sz]ed", sent, re.I):
            return "EXCLUDE", f"Excluded — Citizenship/Clearance: \"{cit.search(sent).group(0)}\""
        clr = next((rx for rx in CLEARANCE_EXCLUDE if rx.search(sent)), None)
        if clr:
            if SOFTENERS.search(sent):
                ambiguous.append("clearance mentioned as preferred")
            else:
                return "EXCLUDE", f"Excluded — Citizenship/Clearance: \"{clr.search(sent).group(0)}\""
        for label, rx in AMBIGUOUS:
            if rx.search(sent) and (label != "background check" or REVIEW_ON_BACKGROUND_CHECK):
                ambiguous.append(label)
    text = desc or ""
    if any(rx.search(text) for rx in NO_SPONSOR):
        notes.append("Included — No Sponsorship (does not offer visa sponsorship)")
    elif WORK_AUTH.search(text):
        notes.append("Included — Existing U.S. work authorization required")
    if ambiguous:
        amb = set(ambiguous)
        if "clearance mentioned as preferred" in amb:
            amb.discard("security clearance mentioned (not as a requirement)")
        amb = sorted(amb)
        return "AMBIGUOUS", "; ".join(notes + [f"Needs review — work-authorization wording: {', '.join(amb)}"])
    if notes:
        return ("NO_SPONSORSHIP" if "No Sponsorship" in notes[0] else "WORK_AUTH"), notes[0]
    return "NOT_STATED", "Not stated (unverified)"

def work_auth(desc):
    return auth_assessment(desc)[1]

def _required_text(desc):
    m = re.search(r"\bpreferred\b|nice[- ]to[- ]have|bonus|\bideal(ly)?\b", desc, re.I)
    return desc[:m.start()] if m else desc

def education(title, desc):
    text = f"{title}. {desc}"
    if not PHD_SIGNAL.search(text):
        if re.search(r"bachelor|\bb\.?s\.?\b|undergraduate", text, re.I):
            return "Bachelor's accepted", False
        if re.search(r"master'?s|\bm\.?s\.?\b", text, re.I):
            return "Master's mentioned", False
        return "Not stated", False
    req = _required_text(desc)
    phd_req = bool(PHD_REQUIRED.search(req)) or bool(re.search(r"\bph\.?\s?d\b", title, re.I))
    if phd_req and not BS_MS_OK.search(re.sub(r"ph\.?\s?d|doctora\w*", "", f"{title} {req}", flags=re.I)):
        return "PhD required", True
    return "BS/MS accepted (PhD also mentioned)", False

def work_auth(desc):
    found = [t for t, rx in AUTH_RULES if rx.search(desc or "")]
    return "; ".join(found) if found else "Not stated (unverified)"

def assess(job):
    title = (job.get("title") or "").strip()
    desc = job.get("desc") or ""
    family = next((f for f, rx in FAMILIES if rx.search(title)), None)
    skills = [k for k, rx in SKILLS.items() if rx.search(desc)]
    core = [s for s in skills if s in CORE]
    hw = len(set(m.group(0).lower() for m in HARDWARE.finditer(f"{title} {desc}")))
    edu, phd_only = education(title, desc)
    auth_status, auth_label = auth_assessment(desc)
    out = dict(family=family or "Other technical", skills=skills, education=edu, phd_only=phd_only, auth=auth_label, auth_status=auth_status)
    skill_txt = ", ".join(skills[:6]) if skills else "none listed"
    unrelated_title = None if IT_TECHNICIAN.search(title) else UNRELATED_TITLE.search(title)

    if family:
        if unrelated_title and len(core) < 2:
            out.update(relevance="UNRELATED", why=f"title is {unrelated_title.group(0).strip()} work")
        elif not desc.strip() or core or len(skills) >= 2:
            out.update(relevance="MATCH", why=f"{family} role; skills in posting: {skill_txt}")
        else:
            out.update(relevance="BORDERLINE", why=f"{family} title but description lists no technical skills")
        return out
    if unrelated_title:
        out.update(relevance="UNRELATED", why=f"unrelated role ({unrelated_title.group(0).strip()})")
        return out
    score = len(skills) + len(core) - 2 * hw          # core skills count double; hardware terms count against
    if score >= 6 and len(core) >= 2:
        out.update(relevance="MATCH", family="Other technical", why=f"title not specific, but the work is technical: {skill_txt}")
    elif score >= 3 and core:
        out.update(relevance="BORDERLINE", why=f"title not specific; some technical skills: {skill_txt}")
    else:
        out.update(relevance="UNRELATED", why="not a data/AI/software role" + (" (hardware/trades focus)" if hw else ""))
    return out

# ------------------------------------------------------------------ location priority
ATL_UNIQUE = r"atlanta|alpharetta|sandy springs|dunwoody|kennesaw|norcross|peachtree corners|peachtree city|johns creek|buckhead|suwanee|vinings"
ATL_AMBIGUOUS = (r"decatur|marietta|roswell|duluth|smyrna|brookhaven|cumming|lawrenceville|tucker|chamblee|doraville|college park|"
                 r"east point|hapeville|stone mountain|lithonia|conyers|mcdonough|fayetteville|newnan|woodstock|canton|acworth|austell|"
                 r"douglasville|lilburn|snellville|buford|milton|forest park")   # these names also exist in other states
ATL_METRO = re.compile(rf"\b({ATL_UNIQUE})\b|\b({ATL_AMBIGUOUS})\b\s*,?\s*(ga|georgia)\b", re.I)
GEORGIA = re.compile(r",\s*ga\b|\bgeorgia\b(?!,? (country|tbilisi))|savannah|augusta, ga|athens, ga|macon|columbus, ga|valdosta|albany, ga", re.I)

def region(location):
    loc = location or ""
    if ATL_METRO.search(loc):
        return "Atlanta metro"
    if GEORGIA.search(loc):
        return "Georgia"
    return "United States"
