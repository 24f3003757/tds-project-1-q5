"""Data analyst Telegram bot for TDS Project 1.

Two things run in one process:
  1. a FastAPI app that serves the public JSONL run log at GET /run.jsonl
  2. a background thread that long polls Telegram and answers messages

Environment variables required:
  TELEGRAM_BOT_TOKEN : from @BotFather
  OPENROUTER_TOKEN       : from https://openrouter.io
  PUBLIC_BASE_URL    : e.g. https://myagent.onrender.com  (no trailing slash)
"""

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from openai import OpenAI

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_TOKEN = os.environ["OPENROUTER_TOKEN"]
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
TAVILY_TOKEN = os.environ.get("TAVILY_TOKEN", "")
BRAVE_TOKEN = os.environ.get("BRAVE_TOKEN", "")
GOOGLE_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CX = os.environ.get("GOOGLE_CX", "")
MODEL = os.environ.get("MODEL", "openai/gpt-4o-mini")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "1200"))  # cap the reservation
                                                        # OpenRouter holds per call
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

LOG_PATH = Path(os.environ.get("LOG_PATH", "run.jsonl"))
LOG_LOCK = threading.Lock()

# Publishing the log to GitHub makes log_url a static raw.githubusercontent
# URL. That is free, wget-able, and survives the bot host restarting, so the
# bot itself never needs to be reachable from outside.
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
GH_REPO = os.environ.get("GITHUB_REPO")            # e.g. "alix/tds-telegram-agent"
GH_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GH_PATH = os.environ.get("GITHUB_LOG_PATH", "run.jsonl")

if GH_TOKEN and GH_REPO:
    LOG_URL = f"https://raw.githubusercontent.com/{GH_REPO}/{GH_BRANCH}/{GH_PATH}"
else:
    LOG_URL = f"{PUBLIC_BASE_URL}/run.jsonl"

_gh_sha = None      # blob sha of the file currently on GitHub
_gh_lock = threading.Lock()


def publish_log():
    """Commit the current run.jsonl to GitHub. Safe to call often; failures
    are swallowed so a publishing problem can never break a reply."""
    if not (GH_TOKEN and GH_REPO) or not LOG_PATH.exists():
        return
    global _gh_sha
    with _gh_lock:
        import base64
        api = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
        hdrs = {"Authorization": f"Bearer {GH_TOKEN}",
                "Accept": "application/vnd.github+json"}
        try:
            if _gh_sha is None:  # find the existing file, if any
                r = requests.get(api, headers=hdrs, params={"ref": GH_BRANCH}, timeout=30)
                if r.status_code == 200:
                    _gh_sha = r.json().get("sha")
            body = {"message": "update run log",
                    "content": base64.b64encode(LOG_PATH.read_bytes()).decode(),
                    "branch": GH_BRANCH}
            if _gh_sha:
                body["sha"] = _gh_sha
            r = requests.put(api, headers=hdrs, json=body, timeout=45)
            if r.status_code in (200, 201):
                _gh_sha = r.json()["content"]["sha"]
            else:
                _gh_sha = None  # force a refetch next time
        except Exception:
            _gh_sha = None

client = OpenAI(api_key=OPENROUTER_TOKEN, base_url="https://openrouter.ai/api/v1")

# ----------------------------------------------------------------- logging


def log(event, **fields):
    """Append one JSON object per line. This file is what the graders wget."""
    row = {"ts": time.time(), "event": event, **fields}
    with LOG_LOCK:
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(row, default=str) + "\n")


# ------------------------------------------------------------------- tools

PY_PREAMBLE = "import json, math, re, statistics\n"


def _auto_echo(code: str) -> str:
    """If the last line is a bare expression, print it. Models habitually end a
    snippet with `result` as if in a REPL; without this they burn a whole step
    rediscovering that a subprocess prints nothing."""
    lines = code.rstrip().split("\n")
    if not lines:
        return code
    last = lines[-1]
    if (last.strip() and not last[0].isspace()
            and not re.match(r"\s*(print|import|from|def|class|return|#|@)\b", last)
            and "=" not in last.split("#")[0]
            and not last.rstrip().endswith((":", ",", "(", "[", "{", "\\"))):
        lines[-1] = f"print({last.strip()})"
        return "\n".join(lines)
    return code


def run_python(code: str) -> str:
    """Execute Python in a subprocess and return whatever it printed."""
    code = _auto_echo(code)
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "snippet.py"
        f.write_text(PY_PREAMBLE + code)
        try:
            p = subprocess.run(
                [sys.executable, str(f)],
                capture_output=True, text=True, timeout=90, cwd=d,
            )
        except subprocess.TimeoutExpired:
            return "ERROR: code timed out after 90 seconds"
    out = (p.stdout or "") + (("\nSTDERR:\n" + p.stderr) if p.returncode else "")
    return out.strip()[:6000] or "(no output; remember to print() your result)"


def fetch_url(url: str) -> str:
    """Download a page, a PDF or a data file and return readable text."""
    if url in DOCS:  # already downloaded in this process
        return _store(url, DOCS[url])
    hdrs = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, timeout=90, headers=hdrs)
    except requests.exceptions.SSLError:
        try:  # many .gov.in hosts serve an incomplete certificate chain
            import urllib3
            urllib3.disable_warnings()
            r = requests.get(url, timeout=90, headers=hdrs, verify=False)
        except Exception as e:
            return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR: {e}"

    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        try:
            import pdfplumber, io
            out, tables = [], []
            with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                for pno, page in enumerate(pdf.pages[:40], 1):
                    out.append(f"\n=== page {pno} ===\n" + (page.extract_text() or ""))
                    for table in page.extract_tables() or []:
                        rendered = "\n".join(
                            " | ".join((c or "").replace("\n", " ").strip() for c in row)
                            for row in table)
                        tables.append(f"--- table on page {pno} ---\n{rendered}")
                        out.append(rendered)
            TABLES[url] = tables
            full = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
            return _store(url, full)
        except Exception as e:
            return f"ERROR reading PDF: {e}"

    text = r.text
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return _store(url, re.sub(r"\s+", " ", text).strip())


def _tavily_search(query: str) -> str:
    """Tavily: search API built for agents. 1000 free credits per month, no card.
    Returns cleaned page content, not just links, which saves a fetch step."""
    if not TAVILY_TOKEN:
        return ""
    try:
        r = requests.post("https://api.tavily.com/search",
                          json={"query": query, "max_results": 8,
                                "search_depth": "basic"},
                          headers={"Authorization": f"Bearer {TAVILY_TOKEN}",
                                   "Content-Type": "application/json"},
                          timeout=45)
        if r.status_code != 200:
            log("tavily_http", status=r.status_code, body=r.text[:300])
            return ""
        data = r.json()
    except Exception as e:
        log("tavily_error", error=repr(e))
        return ""

    parts = []
    if data.get("answer"):
        parts.append(f"[Tavily summary] {data['answer']}")
    for item in (data.get("results") or [])[:8]:
        parts.append(f"{item.get('title','')}\n{item.get('url','')}\n"
                     f"{(item.get('content','') or '')[:600]}")
    return "\n\n".join(parts)[:8000]


def _brave_search(query: str) -> str:
    """Brave Search API. Built for server side use, unlike scraping."""
    if not BRAVE_TOKEN:
        return ""
    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 8},
            headers={"Accept": "application/json",
                     "X-Subscription-Token": BRAVE_TOKEN},
            timeout=30)
        if r.status_code != 200:
            log("brave_http", status=r.status_code, body=r.text[:300])
            return ""
        results = (r.json().get("web") or {}).get("results") or []
    except Exception as e:
        log("brave_error", error=repr(e))
        return ""

    lines = []
    for item in results[:8]:
        desc = re.sub(r"<[^>]+>", "", item.get("description", "") or "")
        lines.append(f"{item.get('title','')}\n{item.get('url','')}\n{desc}")
    return "\n\n".join(lines)[:6000]


def _google_search(query: str) -> str:
    """Google Programmable Search JSON API. Free tier: 100 queries per day,
    no card required. Needs an API key and a search engine id (cx)."""
    if not (GOOGLE_KEY and GOOGLE_CX):
        return ""
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1",
                         params={"key": GOOGLE_KEY, "cx": GOOGLE_CX,
                                 "q": query, "num": 8},
                         timeout=30)
        if r.status_code != 200:
            log("google_http", status=r.status_code, body=r.text[:300])
            return ""
        items = r.json().get("items") or []
    except Exception as e:
        log("google_error", error=repr(e))
        return ""
    return "\n\n".join(
        f"{i.get('title','')}\n{i.get('link','')}\n{i.get('snippet','')}"
        for i in items[:8])[:6000]


def _ddg_search(query: str) -> str:
    """Fallback scraper. Often blocked from datacenter IPs, so it is second."""
    from urllib.parse import unquote
    try:
        r = requests.post("https://html.duckduckgo.com/html/",
                          data={"q": query}, timeout=45,
                          headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:
        return f"ERROR: {e}"

    blocks = re.findall(r'class="result__a" href="(.*?)".*?>(.*?)</a>', r.text, flags=re.S)
    def clean(h):
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h or "")).strip()
    lines = []
    for href, title in blocks[:8]:
        m = re.search(r"uddg=([^&]+)", href)
        lines.append(f"{clean(title)}\n{unquote(m.group(1)) if m else href}")
    return "\n\n".join(lines)[:6000]


def web_search(query: str) -> str:
    """Search the web. Tries Brave, then falls back to scraping."""
    out = _tavily_search(query)
    if out:
        log("search_engine", engine="tavily", query=query)
        return out
    out = _brave_search(query)
    if out:
        log("search_engine", engine="brave", query=query)
        return out
    out = _google_search(query)
    if out:
        log("search_engine", engine="google", query=query)
        return out
    out = _ddg_search(query)
    log("search_engine", engine="duckduckgo", query=query, empty=not out)
    return out or ("(no results from any search engine. Try a different query, "
                   "or fetch a likely official URL directly.)")


DOCS = {}     # url -> full extracted text
TABLES = {}   # url -> list of rendered tables (PDFs only)
HEAD = 6000


def _store(url, full):
    DOCS[url] = full
    if len(full) <= HEAD:
        return full
    return (full[:HEAD] + f"\n\n[...truncated. This document is {len(full)} characters. "
            f"Use search_document with this url and a keyword such as a state name "
            f"or a table heading to read the rest...]")


def search_document(url: str, query: str) -> str:
    """Return the best matching passages of an already fetched document.

    Windows are RANKED, not taken in document order, so a query made of common
    words cannot trap us on page one. Score rewards covering many distinct
    query terms and containing digits, which is what a statistical table looks
    like.
    """
    full = DOCS.get(url)
    if full is None:
        return "ERROR: fetch_url this document first."

    terms = [t.lower() for t in re.split(r"\W+", query) if len(t) > 2]
    if not terms:
        return "(query too short)"

    # Inverse frequency weighting: a word appearing 80 times in this document
    # cannot tell us where to look, so it counts for almost nothing. A rare
    # word ("Assam", "Table") is what actually locates a passage.
    low_full = full.lower()
    weight = {t: 1.0 / math.log(2 + low_full.count(t)) for t in set(terms)}

    win, step = 2500, 1200
    scored = []
    for start in range(0, max(1, len(full) - 500), step):
        chunk = full[start:start + win]
        low = chunk.lower()
        term_score = sum(weight[t] for t in set(terms) if t in low)
        if term_score == 0:
            continue
        digits = len(re.findall(r"\d", chunk))
        rows = chunk.count("|") + chunk.count("\n")
        score = term_score * 40 + min(digits, 200) * 1.2 + min(rows, 60) * 0.8
        scored.append((score, start, chunk))

    if not scored:
        return f"(no match for {query!r}. Try a single distinctive word.)"

    scored.sort(key=lambda x: -x[0])
    picked, out = [], []
    for score, start, chunk in scored:
        if any(abs(start - s0) < win for s0 in picked):
            continue
        picked.append(start)
        out.append(f"[offset {start}, score {score:.0f}]\n{chunk}")
        if len(out) >= 4:
            break
    return "\n\n---\n\n".join(out)[:11000]


def read_tables(url: str) -> str:
    """Return every table extracted from a PDF already fetched, as text rows."""
    tables = TABLES.get(url)
    if tables is None:
        return ("ERROR: no tables recorded for this url. fetch_url it first, "
                "and note that only PDFs yield tables.")
    if not tables:
        return "(no tables detected in this PDF; use search_document instead)"
    return "\n\n".join(tables)[:12000]


TOOLS = [
    {"type": "function", "function": {
        "name": "run_python",
        "description": "Run Python 3 code and get back what it prints. pandas, numpy and requests are available.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "Code to run. Print the result."}},
            "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "Download a web page, PDF or data file and return its text.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web to FIND sources. Use this first when you do not already know a URL.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_tables",
        "description": "Return every table from a PDF you already fetched. Use this FIRST for any ranking or comparison question about a statistical report.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "search_document",
        "description": "Look inside a document you already fetched. Use this when fetch_url said the document was truncated.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}, "query": {"type": "string"}},
            "required": ["url", "query"]}}},
]

DISPATCH = {"run_python": run_python, "fetch_url": fetch_url,
            "web_search": web_search, "search_document": search_document,
            "read_tables": read_tables}

SYSTEM = f"""You are a data analyst agent answering questions over Telegram.

EVIDENCE RULES (most important):
1. Never invent data. Writing a table of made up numbers into run_python and
   computing over it is the worst possible failure. Placeholders such as
   "State A", "State D", "<state name>", "Example" or "N/A" are always wrong.
2. FIRST decide where the data is. If the question ALREADY CONTAINS the
   numbers, the data is in the message: use run_python on those exact numbers
   and answer immediately. Do NOT search the web. Searching when the data was
   handed to you is a serious error and wastes the whole time budget.
   Only when the question names an external source or dataset do you call
   web_search to find a URL, then fetch_url to read it, then compute.
3. run_python is for arithmetic over data you actually retrieved, or for
   data the user pasted into the message. It is never a source of facts.
   Drop rows with missing values before comparing them, or max() will fail
   on None. The last bare expression is printed automatically.
4. If fetch_url says a document was truncated, DO NOT give up on it.
   For a ranking or comparison question call read_tables on that url first.
   Otherwise call search_document, but search with ONE distinctive word, such
   as a state name like "Assam" or "Kerala", or a heading word like "Table".
   Searching with common words from the question ("maternal mortality rate")
   only returns the opening page. Statistical tables live deep in a report.
5. Never search for a specific answer you already suspect. Searching for
   "Odisha maternal mortality" to confirm a hunch is confirmation bias. Search
   for the TABLE or the BULLETIN, then read every state's number and compare.
6. If a document yields only a run of loose numbers with no state names beside
   them, that is a CHART, not a table, and its values cannot be recovered.
   Move to a different source at once. Government press releases (pib.gov.in)
   and the SRS bulletins usually carry the same figures as real text.
7. Before answering, you must have seen a passage listing MANY states with
   their numbers, and you must have compared them. If you have only seen a
   national figure or a definition, you have not found the answer yet. Never
   fall back on what you remember.
8. Maternal mortality is reported as two different columns. The RATIO is
   deaths per 100,000 live births and its values run from about 20 to 250.
   The RATE is per 1,000 women of reproductive age and its values run from
   about 1 to 15. A question saying "maternal mortality rate" colloquially
   means the RATIO, the widely reported headline figure. If the column you
   read has a maximum below 40 you are in the wrong column: find the other.
9. Indian statistics questions mean INDIAN states. Always put "India" in your
   search queries or you will get United States data. Try mospi.gov.in, data.gov.in, the Sample
   Registration System bulletins, PIB releases, and their PDF reports.

OUTPUT RULES:
10. Your FINAL message must be exactly one JSON object and nothing else.
    No prose, no markdown fences, no explanation, no greeting.
11. COPY the JSON shape from the message, key for key. Use the exact key names
    it shows. If it asks for {{"sorted": [...]}} the key is "sorted", not
    "sorted_values" and not a name you invent. Never add keys it did not ask
    for. If, and only if, the shape it shows contains "log_url", include
    "log_url" with this value: {LOG_URL}
12. NEVER answer with N/A, null, unknown, empty strings or angle brackets.
    If you are unsure, commit to your single most likely answer. An unanswered
    question scores the same as a wrong one, so guessing strictly dominates.
13. Numbers must be JSON numbers, not strings, unless strings were asked for.
    Round exactly as the message asks. If it does not say, and the value is
    not a whole number, give one decimal place.
14. If several messages were sent, answer ONLY the final one. Earlier messages
    are background for it, never the question itself.
"""

# ------------------------------------------------------------------- agent


def solve(history, run_id, max_steps=12, time_budget=100):
    """Returns (final_text, convo) so the caller can push for a commitment."""
    """history is a list of the user's messages in this conversation."""
    convo = [{"role": "system", "content": SYSTEM}]
    convo += [{"role": "user", "content": m} for m in history[:-1]]
    if history:
        # Naming the final message explicitly. A model given four bare user
        # turns will happily answer the most interesting one rather than the
        # last one.
        convo.append({"role": "user", "content":
            "THIS IS THE QUESTION TO ANSWER NOW. Anything above is background "
            "context only, not the question.\n\n" + history[-1]})

    # The grounding gate below exists to stop the model inventing a table and
    # computing over it. It must NOT fire when the message already carries its
    # own data, or an inline arithmetic question is forced onto the web and
    # comes back with an answer to a different question entirely.
    need_source = needs_external_data(" ".join(history))
    grounded = False  # True once a real source has been retrieved
    deadline = time.time() + time_budget
    seen_calls = {}   # (tool, args) -> how many times already made
    doc_probes = {}   # url -> how many times we have dug into that document

    for step in range(max_steps):
        if time.time() > deadline:
            break
        resp = client.chat.completions.create(
            model=MODEL, messages=convo, tools=TOOLS, temperature=0,
            max_tokens=MAX_TOKENS)
        msg = resp.choices[0].message
        convo.append(msg.model_dump(exclude_none=True))
        log("llm_step", run_id=run_id, step=step,
            content=msg.content, tool_calls=[c.function.name for c in (msg.tool_calls or [])])

        if not msg.tool_calls:
            if need_source and not grounded:
                convo.append({"role": "user", "content":
                    "You have not retrieved any real source yet. Call web_search "
                    "to find an authoritative page, then fetch_url to read it. "
                    "Do not use invented data."})
                continue
            return (msg.content or ""), convo

        for call in msg.tool_calls:
            name = call.function.name
            args = json.loads(call.function.arguments or "{}")
            sig = (name, json.dumps(args, sort_keys=True))
            seen_calls[sig] = seen_calls.get(sig, 0) + 1

            if seen_calls[sig] > 1:
                # Repeating a call cannot produce a new result. Say so plainly
                # instead of running it again, and name the way out.
                result = ("STOP. You already made this exact call and got the "
                          "result above. Repeating it will never give anything "
                          "new. Do ONE of these instead: (a) call read_tables "
                          "on a PDF you fetched, (b) fetch a DIFFERENT source "
                          "from your earlier search results, (c) run a NEW "
                          "web_search with different words, or (d) if you "
                          "already have enough, give your final JSON answer.")
                log("repeat_blocked", run_id=run_id, step=step, tool=name, args=args)
            else:
                result = DISPATCH[name](**args)
                if name in ("web_search", "fetch_url") and not result.startswith("ERROR"):
                    grounded = True

            # Abandon a document we keep failing to mine.
            if name in ("search_document", "read_tables"):
                u = args.get("url", "")
                doc_probes[u] = doc_probes.get(u, 0) + 1
                if doc_probes[u] >= 3:
                    result += ("\n\n[You have probed this document 3 times "
                               "without finding the figures. It probably stores "
                               "them as charts or images, which cannot be "
                               "extracted. ABANDON this url and use a different "
                               "source.]")
            log("tool", run_id=run_id, step=step, tool=name,
                args=args, result=result[:2000])
            convo.append({"role": "tool", "tool_call_id": call.id, "content": result})

    # Budget exhausted. Force one final answer with no tools available, so we
    # always reply with the best JSON we can rather than falling silent.
    convo.append({"role": "user", "content":
        "Time is up. Answer NOW using what you already retrieved. Reply with "
        "exactly one JSON object in the requested shape and nothing else."})
    try:
        final = client.chat.completions.create(
            model=MODEL, messages=convo, temperature=0, max_tokens=MAX_TOKENS)
        out = final.choices[0].message.content or ""
        log("forced_final", run_id=run_id, content=out)
        return out, convo
    except Exception as e:
        log("forced_final_error", run_id=run_id, error=repr(e))
        return "", convo


PLACEHOLDERS = {"", "n/a", "na", "none", "null", "unknown", "not available",
                "not found", "no data", "tbd", "example", "state a", "state b",
                "state c", "state d", "xxx", "answer", "value"}


TEMPLATE_RE = re.compile(
    r"reply with only|respond with only|reply with exactly|reply with ONLY",
    re.I)

# Words that mean "the thing I sent you before", which is the one case where a
# new message genuinely needs the previous turns.
PRIOR_RE = re.compile(
    r"\b(above|these|those|that data|that list|previous|earlier|"
    r"same (?:data|numbers|list|values))\b", re.I)

# A follow up turn often names nothing at all: "Ignore the smallest value."
# An imperative opener, with no data and no source of its own, is a
# continuation of whatever came before it.
CONT_RE = re.compile(
    r"^\s*(now|also|then|and|next|instead|ignore|exclude|drop|remove|"
    r"using|from (?:that|those|it)|what about)\b", re.I)

# Naming any of these means the data lives outside the message.
DATASET_HINTS = ("mospi", "data.gov.in", "census", "http://", "https://",
                 "dataset", "bulletin", "nfhs", "pib", "world bank", "rbi",
                 "niti", "sample registration", "srs ", "public data")


def _payload(text):
    """The question with its output template stripped off.

    The template ("Reply with ONLY {"mean": <number>}") is boilerplate present
    in every message, so counting digits without removing it first would count
    the template's own placeholders.
    """
    return TEMPLATE_RE.split(text)[0]


def has_inline_data(text):
    """True when the message hands over enough numbers to be self contained.

    Four is the threshold: a lookup question ("highest maternal mortality rate
    based on MOSPI data") carries at most a year or two, while a compute
    question carries a whole list.
    """
    return len(re.findall(r"-?\d+(?:\.\d+)?", _payload(text))) >= 4


def needs_external_data(text):
    """Whether this question requires the web. Naming a source always wins:
    a question can quote a few figures and still point at a dataset."""
    low = text.lower()
    if any(h in low for h in DATASET_HINTS):
        return True
    return not has_inline_data(text)


def is_task_terminus(text):
    """True when a message states its own output shape.

    Such a message completes a task, so whatever follows it is a NEW task.
    This is the only reliable separator available: the grader sends its next
    question seconds after receiving the previous reply, so no elapsed-time
    threshold can tell a new question from the next turn of the current one.
    """
    return bool(TEMPLATE_RE.search(text)) or requested_shape(text) is not None


def references_prior(text):
    """True when a message points back at data sent in an earlier turn.

    Carrying its own data or naming its own source both settle it: the message
    is self sufficient and cannot be a continuation.
    """
    if has_inline_data(text) or needs_external_data(text) is True and any(
            h in text.lower() for h in DATASET_HINTS):
        return False
    return bool(PRIOR_RE.search(text)) or bool(CONT_RE.search(text))


def conform(obj, template):
    """Force the reply into the exact shape the message asked for.

    The grader parses the whole reply and compares it to the expected answer
    with ==, so a correct value inside the wrong wrapper scores zero. Some
    messages ask for a bare object, others for the {"answer": ..., "log_url":
    ...} envelope. Mirror whichever the message showed rather than guessing.
    """
    if not isinstance(template, dict) or not isinstance(obj, dict):
        return obj

    want_env = "log_url" in template and "answer" in template
    has_env = "log_url" in obj and "answer" in obj

    if want_env:
        inner = obj["answer"] if has_env else obj
        # log_url is written by us, never by the model, which fabricates URLs.
        return {"answer": inner, "log_url": LOG_URL}

    if has_env:                      # envelope sent but not requested: unwrap
        inner = obj["answer"]
        obj = inner if isinstance(inner, dict) else obj
        if not isinstance(obj, dict):
            return obj

    # Single key template and a single key reply: the model renamed the key.
    # The value is what was computed, so keep it and restore the asked-for name.
    if len(template) == 1 and len(obj) == 1:
        want, got = next(iter(template)), next(iter(obj))
        if want != got:
            return {want: obj[got]}

    return obj


def requested_shape(text):
    """Pull the JSON skeleton the question asked for, so that even a failed run
    replies in the right shape rather than a shape that is certainly wrong."""
    for m in re.finditer(r"\{.*\}", text, flags=re.S):
        blob = m.group(0)
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            # skeletons contain <placeholders>, so quote them before parsing
            # A quoted placeholder ("<state name>") must lose its own quotes
            # too, or substituting gives ""?"" which is not valid JSON. This
            # ran first for years and silently returned None for every
            # question whose skeleton quoted its placeholders, which is most
            # of them.
            patched = re.sub(r'"<[^>{}]*>"', '"?"', blob)
            patched = re.sub(r"<[^>{}]*>", '"?"', patched)
            try:
                return json.loads(patched)
            except json.JSONDecodeError:
                continue
    return None


def looks_like_placeholder(obj):
    """True if any leaf value is a non answer. Under exact match grading a
    placeholder scores zero, and so does a wrong guess, so there is never a
    reason to submit one: a committed guess can only do better."""
    def leaf_bad(v):
        if v is None:
            return True
        if isinstance(v, str):
            t = v.strip().lower()
            return t in PLACEHOLDERS or t.startswith("<") or t.endswith(">")
        return False

    def walk(v):
        if isinstance(v, dict):
            return any(walk(x) for x in v.values())
        if isinstance(v, list):
            return not v or any(walk(x) for x in v)
        return leaf_bad(v)

    return walk(obj)


def extract_json_object(text):
    """Pull the single JSON object out of whatever the model returned."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    pass
    return None


# ---------------------------------------------------------------- telegram

HISTORY = {}        # chat_id -> list of recent message texts
LAST_SEEN = {}      # chat_id -> timestamp of the previous message
CHAT_LOCKS = {}     # chat_id -> lock, so replies cannot overtake each other
LOCKS_GUARD = threading.Lock()
CONTEXT_GAP = 240   # seconds; a longer pause means a new, unrelated question


def chat_lock(chat_id):
    with LOCKS_GUARD:
        return CHAT_LOCKS.setdefault(chat_id, threading.Lock())


def send(chat_id, text):
    requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=30)


def _handle(chat_id, text):
    run_id = str(uuid.uuid4())
    now = time.time()

    # A long silence means this is a fresh question, not a later turn of the
    # previous one. Carrying stale context across questions made the agent
    # answer with data from an earlier exchange.
    gap = now - LAST_SEEN.get(chat_id, 0)
    LAST_SEEN[chat_id] = now
    if gap > CONTEXT_GAP:
        HISTORY[chat_id] = []
        log("context_reset", run_id=run_id, chat_id=chat_id, gap=round(gap, 1))

    hist = HISTORY.setdefault(chat_id, [])

    # A message that carried its own output template ended a task, so this new
    # message starts a fresh one unless it explicitly points back. Without this
    # the fifth graded question still has questions one to four sitting in the
    # prompt, and an inline mean question comes back as a state name.
    if hist and is_task_terminus(hist[-1]) and not references_prior(text):
        log("task_boundary", run_id=run_id, chat_id=chat_id,
            closed=hist[-1][:120])
        hist.clear()

    hist.append(text)
    del hist[:-6]
    log("received", run_id=run_id, chat_id=chat_id, text=text)

    convo = None
    try:
        raw, convo = solve(list(hist), run_id)
    except Exception as e:
        log("error", run_id=run_id, error=repr(e))
        raw = ""

    obj = extract_json_object(raw)

    # Never submit a placeholder or an unparseable reply. Force a commitment.
    if (obj is None or looks_like_placeholder(obj)) and convo is not None:
        log("commit_pass", run_id=run_id, first_attempt=raw[:500])
        try:
            convo = convo + [{"role": "user", "content":
                "That answer is not acceptable: it is empty, a placeholder, or "
                "not valid JSON. An unanswered question scores the same as a "
                "wrong one, so you must COMMIT to your single most likely "
                "answer using everything you retrieved plus your best "
                "judgement. Never output N/A, null, unknown or angle brackets. "
                "Reply with exactly one JSON object in the requested shape."}]
            final = client.chat.completions.create(
                model=MODEL, messages=convo, temperature=0, max_tokens=MAX_TOKENS)
            retry = final.choices[0].message.content or ""
            log("commit_result", run_id=run_id, content=retry[:500])
            retry_obj = extract_json_object(retry)
            if retry_obj is not None and not looks_like_placeholder(retry_obj):
                obj = retry_obj
        except Exception as e:
            log("commit_error", run_id=run_id, error=repr(e))

    template = requested_shape(text)
    if obj is None:
        obj = template or {"answer": None, "log_url": LOG_URL}
        log("shape_fallback", run_id=run_id, obj=obj)

    before = json.dumps(obj, ensure_ascii=False, default=str)
    obj = conform(obj, template)
    after = json.dumps(obj, ensure_ascii=False, default=str)
    if before != after:
        log("conformed", run_id=run_id, before=before[:400], after=after[:400])

    reply = json.dumps(obj, ensure_ascii=False)
    log("replied", run_id=run_id, reply=reply)
    send(chat_id, reply)


def handle(chat_id, text):
    """One at a time per chat, so a slow answer cannot arrive after the next
    question and shift every subsequent reply by one."""
    with chat_lock(chat_id):
        _handle(chat_id, text)
    publish_log()   # after replying, so it never delays the answer


def poll_loop():
    offset = None
    started = time.time()
    log("boot", log_url=LOG_URL, model=MODEL)
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"timeout": 30, "offset": offset}, timeout=60)
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                text = msg.get("text")
                # Messages queued while the instance was down are stale: the
                # sender has stopped waiting, and answering now would be
                # consumed as the reply to their NEXT question.
                if msg.get("date", 0) < started - 90:
                    log("stale_skipped", chat_id=(msg.get("chat") or {}).get("id"),
                        text=(text or "")[:120])
                    continue
                if text and text.strip() != "/start":
                    threading.Thread(
                        target=handle, args=(msg["chat"]["id"], text), daemon=True).start()
        except Exception as e:
            log("poll_error", error=repr(e))
            time.sleep(3)


# ----------------------------------------------------------------- web app

app = FastAPI()


@app.get("/run.jsonl")
def run_log():
    return PlainTextResponse(LOG_PATH.read_text() if LOG_PATH.exists() else "")


@app.get("/")
def health():
    return {"ok": True, "log_url": LOG_URL}


threading.Thread(target=poll_loop, daemon=True).start()