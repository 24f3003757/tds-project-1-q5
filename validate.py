#!/usr/bin/env python3
"""Decide whether an eval run was VALID before reading its score.

A low score from a broken run and a low score from a weak agent look identical
in grade.json, and fixing the wrong one wastes both credits and time. This
script separates them. It answers three questions in order:

  1. Did the harness actually talk to the bot?      (transport)
  2. Did the bot answer in a gradeable shape?       (contract)
  3. Did the agent reason the way it was meant to?  (behaviour)

Only when all three are clean is the score in grade.json meaningful.

Run it from inside the harness checkout, after collect.py and grade.py:

    wget -q -O run.jsonl https://tds-project-1-q5-1.onrender.com/run.jsonl
    python3 validate.py --log run.jsonl

Exit code is 0 when the run is valid, 1 when it is not, so it can gate a
retry loop.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

# Question id prefixes whose data is inside the message. Any web search during
# one of these is a routing bug, not a hard question.
INLINE_FAMILIES = ("a", "b", "c")

# Published list prices, dollars per million tokens, for the spend estimate.
PRICES = {"claude-haiku-4-5-20251001": (1.0, 5.0),
          "claude-haiku-4-5": (1.0, 5.0),
          "claude-sonnet-5": (2.0, 10.0),
          "claude-opus-5": (5.0, 25.0)}

PLACEHOLDER_RE = re.compile(r"<[^>]*>|^(n/?a|none|null|unknown|tbd)$", re.I)


def slugify(email):
    return re.sub(r"[^a-zA-Z0-9]+", "_", email.strip().lower()).strip("_")


class Report:
    def __init__(self):
        self.blockers = []
        self.warnings = []

    def blocker(self, msg):
        self.blockers.append(msg)
        print(f"  BLOCKER  {msg}")

    def warn(self, msg):
        self.warnings.append(msg)
        print(f"  warning  {msg}")

    def ok(self, msg):
        print(f"  ok       {msg}")


# ------------------------------------------------------------ 1. transport


def check_transport(rep, questions, data_dir, slug):
    """Every question was delivered and every turn drew exactly one reply."""
    print("\n=== 1. transport: did the harness reach the bot ===")
    statuses = Counter()
    missing = []

    for q in questions:
        path = Path(data_dir) / slug / f"{q['id']}.json"
        if not path.exists():
            missing.append(q["id"])
            statuses["not_attempted"] += 1
            continue

        rec = json.loads(path.read_text())
        status = rec.get("status", "?")
        statuses[status] += 1

        if status == "timeout":
            rep.blocker(f"{q['id']}: timed out. The instance was asleep, "
                        f"crashed, or exceeded the budget. Not a wrong answer.")
        elif status == "bad_bot":
            rep.blocker(f"{q['id']}: bad_bot. The username in students.csv is "
                        f"wrong, or nobody has pressed Start on the bot.")
        elif status == "error":
            rep.blocker(f"{q['id']}: harness side error: {rec.get('detail')}")
        elif status == "ok":
            n_sent, n_replies = len(rec.get("sent", [])), len(rec.get("replies", []))
            if n_replies != n_sent:
                rep.blocker(f"{q['id']}: sent {n_sent} messages, got {n_replies} "
                            f"replies. Every turn must draw exactly one reply, "
                            f"or every later answer is shifted by one.")

    if missing:
        rep.blocker(f"{len(missing)} question(s) never collected: "
                    f"{', '.join(missing[:5])}")
    if not statuses:
        rep.blocker("no collected data at all. Did collect.py run?")
    else:
        rep.ok(f"statuses: {dict(statuses)}")
    return statuses


# ------------------------------------------------------------- 2. contract


def check_contract(rep, questions, data_dir, slug, expect_log_url):
    """The final reply is exactly one JSON object, and carries log_url.

    This mirrors grade.py's extract_answer exactly: json.loads on the raw
    final reply, nothing else. A reply with so much as a leading space of
    prose is a format_error and scores zero however right the number is.
    """
    print("\n=== 2. contract: is every reply gradeable ===")
    seen_urls = Counter()

    for q in questions:
        path = Path(data_dir) / slug / f"{q['id']}.json"
        if not path.exists():
            continue
        rec = json.loads(path.read_text())
        if rec.get("status") != "ok" or not rec.get("replies"):
            continue

        raw = rec["replies"][-1]
        try:
            obj = json.loads(raw.strip())
        except json.JSONDecodeError:
            rep.blocker(f"{q['id']}: final reply is not parseable JSON, so "
                        f"grade.py records format_error: {raw[:90]!r}")
            continue

        if not isinstance(obj, dict):
            rep.blocker(f"{q['id']}: final reply is {type(obj).__name__}, not a "
                        f"JSON object")
            continue

        if "log_url" not in obj:
            rep.blocker(f"{q['id']}: reply has no log_url. The course guidance "
                        f"is that every reply carries one. Check "
                        f"ALWAYS_INCLUDE_LOG_URL in main.py.")
        else:
            seen_urls[obj["log_url"]] += 1

        flat = json.dumps(obj)
        for bad in ("localhost", "127.0.0.1", "your-host", "example.com"):
            if bad in flat:
                rep.blocker(f"{q['id']}: log_url points at {bad}, which the "
                            f"graders cannot wget. PUBLIC_BASE_URL is unset or "
                            f"wrong on the host.")
        if PLACEHOLDER_RE.search(json.dumps(obj.get("answer", obj))):
            rep.warn(f"{q['id']}: the answer looks like an unfilled placeholder: "
                     f"{flat[:90]}")

    if len(seen_urls) > 1:
        rep.warn(f"replies carried {len(seen_urls)} different log_urls: "
                 f"{list(seen_urls)}. Two instances are probably polling at once.")
    for url in seen_urls:
        if expect_log_url and url != expect_log_url:
            rep.warn(f"log_url {url!r} is not the one expected in "
                     f"questions.json ({expect_log_url!r}); exact match "
                     f"grading will fail on that difference alone")
    if seen_urls:
        rep.ok(f"log_url in every reply: {list(seen_urls)[0]}")


# ------------------------------------------------------------ 3. behaviour


def check_behaviour(rep, log_path, questions, data_dir, slug):
    """Read the agent's own log and confirm it worked the way it should."""
    print("\n=== 3. behaviour: what the agent actually did ===")
    if not log_path or not Path(log_path).exists():
        rep.warn(f"no log file at {log_path}; behaviour checks skipped. "
                 f"wget it from your log_url first.")
        return

    rows = []
    for line in Path(log_path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rep.warn("log has a line that is not valid JSON; the graders read "
                     "this as JSONL, one object per line")
    if not rows:
        rep.blocker(f"{log_path} is empty. The host restarted and wiped it, or "
                    f"nothing was ever logged. Set GITHUB_TOKEN and GITHUB_REPO "
                    f"so log_url survives a restart.")
        return

    events = Counter(r.get("event") for r in rows)
    rep.ok(f"{len(rows)} log lines, events: {dict(events)}")

    boots = [r for r in rows if r.get("event") == "boot"]
    if boots:
        b = boots[-1]
        rep.ok(f"last boot: model={b.get('model')} cheap={b.get('cheap_model')} "
               f"base_url={b.get('base_url')} log_url={b.get('log_url')}")
        if not b.get("base_url"):
            rep.warn("boot recorded no base_url, so this log predates the "
                     "current main.py. You may be reading a stale log.")
        engines = b.get("search_engines") or []
        if engines and engines == ["duckduckgo"]:
            rep.warn("no search API key is set, so web_search scrapes "
                     "DuckDuckGo, which routinely blocks datacenter IPs. "
                     "Set TAVILY_TOKEN before judging retrieval answers.")
    if len(boots) > 1:
        rep.warn(f"{len(boots)} boots in this log. The instance restarted "
                 f"during the run, which loses in memory conversation history "
                 f"and can break a multi turn question.")

    n_recv, n_reply = events.get("received", 0), events.get("replied", 0)
    if n_recv != n_reply:
        rep.blocker(f"{n_recv} messages received but {n_reply} replied. Some "
                    f"message was dropped or answered twice.")

    for bad, why in (("error", "an exception escaped the handler"),
                     ("model_error", "an LLM call failed"),
                     ("shape_fallback", "no answer was produced, so the empty "
                                        "template was sent"),
                     ("commit_error", "the commitment retry itself failed"),
                     ("bad_tool_args", "the model emitted malformed tool "
                                       "arguments"),
                     ("tool_error", "a tool raised")):
        if events.get(bad):
            level = rep.blocker if bad in ("error", "shape_fallback") else rep.warn
            level(f"{events[bad]} x {bad}: {why}")

    for r in rows:
        if r.get("event") in ("model_error", "error"):
            print(f"           {r.get('event')}: {str(r.get('error'))[:140]}")

    # Routing. An inline family question that searched the web means the
    # message carried its own data and the agent went online anyway.
    searched_runs = {r.get("run_id") for r in rows
                     if r.get("event") == "search_engine"}
    received = {r.get("run_id"): r.get("text", "") for r in rows
                if r.get("event") == "received"}
    inline_ids = [q["id"] for q in questions
                  if q["id"][:1] in INLINE_FAMILIES]
    inline_texts = []
    for qid in inline_ids:
        path = Path(data_dir) / slug / f"{qid}.json"
        if path.exists():
            inline_texts += json.loads(path.read_text()).get("sent", [])
    for run_id in searched_runs:
        text = received.get(run_id, "")
        if any(text.strip() == t.strip() for t in inline_texts):
            rep.warn(f"a question whose data was in the message still ran a web "
                     f"search: {text[:80]!r}. That is a routing bug, and it "
                     f"burns the time budget.")

    tiers = Counter(r.get("model") for r in rows if r.get("event") == "model_tier")
    if tiers:
        rep.ok(f"model tiers used: {dict(tiers)}")

    # Rough spend, so the next run can be budgeted.
    tier_seq = [r.get("model") for r in rows if r.get("event") == "model_tier"]
    if tier_seq:
        est = sum(PRICES.get(t, (2.0, 10.0))[0] * 0.04
                  + PRICES.get(t, (2.0, 10.0))[1] * 0.002 for t in tier_seq)
        print(f"           very rough spend estimate for this log: ${est:.2f} "
              f"(check the provider console for the real figure)")


# ----------------------------------------------------------------- scoring


def summarise_score(data_dir, slug):
    path = Path(data_dir) / slug / "grade.json"
    if not path.exists():
        print("\nno grade.json yet; run grade.py")
        return
    rows = json.loads(path.read_text())
    correct = [r for r in rows if r.get("correct")]
    print(f"\n=== score: {len(correct)}/{len(rows)} auto marked correct ===")
    for r in rows:
        if not r.get("correct"):
            print(f"  {r['question_id']:<32} {r.get('status')}  "
                  f"{str(r.get('detail'))[:110]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", default="evals/questions.json")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--students", default="students.csv")
    ap.add_argument("--email", help="defaults to the first row of the roster")
    ap.add_argument("--log", default="run.jsonl",
                    help="the agent's own log, downloaded from log_url")
    args = ap.parse_args()

    questions = json.load(open(args.questions))

    email = args.email
    if not email:
        import csv
        email = next(csv.DictReader(open(args.students, newline="")))["email"]
    slug = slugify(email)
    print(f"validating {email}  ({len(questions)} questions)")

    # Whatever log_url the answer key expects, so a mismatch is caught here
    # rather than as a mysterious exact match failure.
    expect = None
    for q in questions:
        exp = q.get("expected")
        if isinstance(exp, dict) and isinstance(exp.get("log_url"), str):
            expect = exp["log_url"]
            break

    rep = Report()
    check_transport(rep, questions, args.data_dir, slug)
    check_contract(rep, questions, args.data_dir, slug, expect)
    check_behaviour(rep, args.log, questions, args.data_dir, slug)
    summarise_score(args.data_dir, slug)

    print("\n" + "=" * 62)
    if rep.blockers:
        print(f"RUN INVALID: {len(rep.blockers)} blocker(s). Fix these and "
              f"re-run before reading the score.")
        for b in rep.blockers:
            print("  -", b)
        sys.exit(1)
    if rep.warnings:
        print(f"RUN VALID with {len(rep.warnings)} warning(s). The score is "
              f"meaningful; the warnings are where marks are leaking.")
        for w in rep.warnings:
            print("  -", w)
    else:
        print("RUN VALID and clean. Any remaining wrong answers are the "
              "agent's reasoning, not the plumbing.")


if __name__ == "__main__":
    main()
