#!/usr/bin/env python3
"""Offline tests for the routing, task boundary and shape logic in main.py.

No network, no API keys, no Telegram. Run before every push:

    python3 test_logic.py
"""
import os
import sys

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("OPENROUTER_TOKEN", "test")
os.environ.setdefault("LOG_PATH", "/tmp/test_run.jsonl")
os.environ.setdefault("PUBLIC_BASE_URL", "https://tds-project-1-q5-1.onrender.com")

import main as m

FAILS = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}")
        print(f"        want {want!r}")
        FAILS.append(label)


TAIL = 'Reply with ONLY this JSON object and nothing else: '

# --- messages used throughout -------------------------------------------
MEAN = ("Here are six readings: [230, 158, 318, 497, 444, 328]. Return their "
        "arithmetic mean rounded to 2 decimal places. " + TAIL +
        '{"mean": <number>}')
SORTED = ("Given [194, 121, 110, 195, 133, 131], return the values sorted "
          "descending. " + TAIL + '{"sorted": [<numbers>]}')
PCT = ("A figure moved from 543 to 271. Compute the percentage change "
       "relative to the earlier value, rounded to 2 decimal places. " + TAIL +
       '{"pct_change": <number>}')
NESTED = ("For [50, 90, 130] report summary statistics. " + TAIL +
          '{"stats": {"mean": <number>, "spread": <number>}}')
BOOL_Q = ("Does any value in [12, 44, 8] exceed 40? " + TAIL +
          '{"exceeds": <true or false>}')
INLINE_MOSPI = ("These six values are taken from a MOSPI bulletin and are "
                "reproduced here in full: [892, 184, 167, 305, 630, 611]. Do "
                "not look anything up. Return their maximum. " + TAIL +
                '{"max": <number>}')
RED_HERRING = ("Table 7 of the 2019-21 report lists these six district "
               "figures: [430, 512, 388, 601, 477, 633]. Ignore the table "
               "number and the report years. Return the mean. " + TAIL +
               '{"mean": <number>}')
WRAP_OBJ = ("Sum these numbers: [98, 52, 46, 80]. " + TAIL +
            '{"answer": {"sum": <number>}, "log_url": '
            '"<public wget-able URL to your agent\'s JSONL log>"}')
WRAP_SCALAR = ("What is the largest of [10, 20, 30]? " + TAIL +
               '{"answer": <number>, "log_url": '
               '"<public wget-able URL to your agent\'s JSONL log>"}')

MMR = ('Which state has the highest maternal mortality rate based on MOSPI '
       'data? Reply with ONLY a JSON object like {"state": "<state name>"}')
MMR_PERIODS = ("Using the SRS maternal mortality bulletins, by how many points "
               "did India's national maternal mortality ratio fall between the "
               "2014-16 period and the 2019-21 period? Reply with ONLY a JSON "
               'object like {"decline": <number>}')
GIVEN_URL = ("Using https://www.pib.gov.in/PressReleasePage.aspx?PRID=2128024 "
             "what was India's maternal mortality ratio in the 2019-21 period? "
             'Reply with ONLY a JSON object like {"mmr": <number>}')
SDG = ("What is the Sustainable Development Goal target for maternal mortality "
       "ratio per one lakh live births by the year 2030? Reply with ONLY a "
       'JSON object like {"target": <number>}')

T1 = "I am going to give you some data, then ask one question about it."
T2 = "The data is [676, 87, 310, 346, 400, 718, 308]"
T3 = 'What is the maximum value? ' + TAIL + '{"max": <number>}'
F1 = "Data: [318, 502, 718, 205, 466, 133]"
F2 = "Now drop the single smallest value."
F3 = 'What is the mean of what remains? ' + TAIL + '{"mean": <number>}'

print("=== routing: inline stays local, lookups go to the web ===")
check("mean is local", m.needs_external_data(MEAN), False)
check("sort is local", m.needs_external_data(SORTED), False)
check("two number percent change is local", m.needs_external_data(PCT), False)
check("nested stats is local", m.needs_external_data(NESTED), False)
check("boolean question is local", m.needs_external_data(BOOL_Q), False)
check("inline beats a MOSPI mention", m.needs_external_data(INLINE_MOSPI), False)
check("year in prose does not confuse it",
      m.needs_external_data(RED_HERRING), False)
check("envelope question is local", m.needs_external_data(WRAP_OBJ), False)
check("MMR lookup needs the web", m.needs_external_data(MMR), True)
check("periods are not data", m.needs_external_data(MMR_PERIODS), True)
check("an explicit URL always wins", m.needs_external_data(GIVEN_URL), True)
check("SDG target needs the web", m.needs_external_data(SDG), True)
check("multi turn data is local",
      m.needs_external_data(" ".join([T1, T2, T3])), False)

print("\n=== period and number parsing ===")
check("a bare period is not data", m.has_inline_data("Compare 2014-16 to 2019-21"), False)
check("two figures are data", m.has_inline_data("from 543 to 271"), True)
check("one figure is not data", m.has_inline_data("the top 3 states"), False)
check("thousands separator is one number",
      m.has_inline_data("per 100,000 live births"), False)

print("\n=== task boundaries ===")
check("a template ends a task", m.is_task_terminus(MEAN), True)
check("MMR question ends a task", m.is_task_terminus(MMR), True)
check("a bare preamble does not", m.is_task_terminus(T1), False)
check("bare data does not", m.is_task_terminus(T2), False)
check("a continuation is recognised", m.references_prior(F2), True)
check("'Now do the median' is a continuation",
      m.references_prior("Now do the median instead."), True)
check("a new question is not a continuation", m.references_prior(MEAN), False)
check("data bearing message is not a continuation", m.references_prior(T2), False)

print("\n=== history depth across a graded sequence ===")
seq = [MEAN, SORTED, MMR, T1, T2, T3, F1, F2, F3, WRAP_OBJ]
want = [1, 1, 1, 1, 2, 3, 1, 2, 3, 1]
hist, sizes = [], []
for msg in seq:
    if hist and m.is_task_terminus(hist[-1]) and not m.references_prior(msg):
        hist = []
    hist.append(msg)
    sizes.append(len(hist))
check("depth per turn", sizes, want)

print("\n=== requested_shape parses every skeleton ===")
for label, msg in [("mean", MEAN), ("sorted", SORTED), ("nested", NESTED),
                   ("bool", BOOL_Q), ("envelope object", WRAP_OBJ),
                   ("envelope scalar", WRAP_SCALAR), ("quoted placeholder", MMR),
                   ("given url", GIVEN_URL)]:
    check(f"shape parsed: {label}", m.requested_shape(msg) is not None, True)

print("\n=== conform: reply matches the requested shape ===")
check("bare stays bare",
      m.conform({"mean": 329.4}, m.requested_shape(MEAN)), {"mean": 329.4})
check("renamed key restored",
      m.conform({"average": 329.4}, m.requested_shape(MEAN)), {"mean": 329.4})
check("invented list key restored",
      m.conform({"sorted_values": [3, 2, 1]}, m.requested_shape(SORTED)),
      {"sorted": [3, 2, 1]})
check("unrequested envelope stripped",
      m.conform({"answer": {"mean": 329.4}, "log_url": "x"},
                m.requested_shape(MEAN)),
      {"mean": 329.4})
check("envelope added when requested",
      m.conform({"sum": 276}, m.requested_shape(WRAP_OBJ)),
      {"answer": {"sum": 276}, "log_url": m.LOG_URL})
check("model written url replaced",
      m.conform({"answer": {"sum": 276}, "log_url": "https://fake.example"},
                m.requested_shape(WRAP_OBJ)),
      {"answer": {"sum": 276}, "log_url": m.LOG_URL})
check("scalar answer envelope",
      m.conform({"answer": 30, "log_url": "x"},
                m.requested_shape(WRAP_SCALAR)),
      {"answer": 30, "log_url": m.LOG_URL})
check("multi key reply untouched",
      m.conform({"min": 1, "max": 9, "n": 3},
                m.requested_shape('x ' + TAIL + '{"min": <n>, "max": <n>, "n": <n>}')),
      {"min": 1, "max": 9, "n": 3})
check("nested reply untouched",
      m.conform({"stats": {"mean": 90.0, "spread": 80}},
                m.requested_shape(NESTED)),
      {"stats": {"mean": 90.0, "spread": 80}})
check("no template means no reshaping",
      m.conform({"anything": 1}, None), {"anything": 1})

print("\n=== placeholder rejection ===")
for label, obj in [("null value", {"state": None}),
                   ("angle brackets", {"state": "<state name>"}),
                   ("N/A", {"state": "N/A"}),
                   ("empty string", {"state": ""}),
                   ("empty list", {"sorted": []}),
                   ("nested null", {"answer": {"sum": None}})]:
    check(f"rejected: {label}", m.looks_like_placeholder(obj), True)
for label, obj in [("real state", {"state": "Uttar Pradesh"}),
                   ("zero is a real answer", {"count": 0}),
                   ("false is a real answer", {"exceeds": False}),
                   ("list of numbers", {"sorted": [3, 2, 1]})]:
    check(f"accepted: {label}", m.looks_like_placeholder(obj), False)

print("\n=== JSON extraction from messy model output ===")
check("fenced json", m.extract_json_object('```json\n{"mean": 1.5}\n```'),
      {"mean": 1.5})
check("prose around json",
      m.extract_json_object('Sure! Here you go: {"mean": 1.5} Hope that helps.'),
      {"mean": 1.5})
check("plain json", m.extract_json_object('{"mean": 1.5}'), {"mean": 1.5})
check("nested json", m.extract_json_object('{"a": {"b": [1, 2]}}'),
      {"a": {"b": [1, 2]}})
check("garbage returns None", m.extract_json_object("no json here"), None)

print("\n=== prompt guardrails present ===")
check("ratio magnitude guidance", "100,000 live births" in m.SYSTEM, True)
check("edition recency guidance",
      "2022-24" in m.SYSTEM or "newest" in m.SYSTEM.lower(), True)
check("inline data instruction",
      "ALREADY CONTAINS" in m.SYSTEM or "already contains" in m.SYSTEM, True)
check("log_url value injected", m.LOG_URL in m.SYSTEM, True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILURE(S):")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL TESTS PASSED")
