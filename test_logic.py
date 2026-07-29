import os, json
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("OPENROUTER_TOKEN", "x")
os.environ.setdefault("PUBLIC_BASE_URL", "https://tds-project-1-q5-1.onrender.com")
os.environ.setdefault("LOG_PATH", "/tmp/test_run.jsonl")

import main as m

MMR = ('Which state has the highest maternal mortality rate based on MOSPI '
       'data? Reply with ONLY a JSON object like {"state": "<state name>"}')
MEAN = ('Compute the mean of these values: [412, 288, 301, 355, 190, 531]. '
        'Reply with ONLY {"mean": <number>}')
LIST_Q = ('Sort these descending: [110, 121, 131, 133, 194, 195]. '
          'Reply with ONLY {"sorted": [<numbers>]}')
T1 = 'Here is my data: [318, 502, 718, 205, 466, 133].'
T2 = 'Ignore the smallest value.'
T3 = 'What is the max? Reply with ONLY {"max": <number>}'
WRAP = ('Sum these: [10, 20, 30, 40, 55]. Reply with ONLY this JSON object and '
        'nothing else: {"answer": {"sum": <number>}, '
        '"log_url": "<public wget-able URL to your JSONL log>"}')

fails = []
def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got!r}")
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")

print("=== routing: does it go to the web? ===")
check("MMR needs web", m.needs_external_data(MMR), True)
check("mean stays local", m.needs_external_data(MEAN), False)
check("sort stays local", m.needs_external_data(LIST_Q), False)
check("multiturn data local", m.needs_external_data(" ".join([T1, T2, T3])), False)
check("wrapper stays local", m.needs_external_data(WRAP), False)

print("\n=== task boundaries: is stale context dropped? ===")
check("MMR ends a task", m.is_task_terminus(MMR), True)
check("mean ends a task", m.is_task_terminus(MEAN), True)
check("bare data does not", m.is_task_terminus(T1), False)
check("turn 3 points back", m.references_prior(T2), True)
check("new question does not", m.references_prior(MEAN), False)

print("\n=== shape conformance ===")
check("bare stays bare",
      m.conform({"state": "Odisha"}, m.requested_shape(MMR)),
      {"state": "Odisha"})
check("invented key renamed",
      m.conform({"state_mmr": "Odisha"}, m.requested_shape(MMR)),
      {"state": "Odisha"})
check("stray envelope unwrapped",
      m.conform({"answer": {"mean": 329.4}, "log_url": "x"},
                m.requested_shape(MEAN)),
      {"mean": 329.4})
check("envelope added when asked",
      m.conform({"sum": 155}, m.requested_shape(WRAP)),
      {"answer": {"sum": 155}, "log_url": m.LOG_URL})
check("model url overwritten",
      m.conform({"answer": {"sum": 155}, "log_url": "https://fake.example"},
                m.requested_shape(WRAP)),
      {"answer": {"sum": 155}, "log_url": m.LOG_URL})

print("\n=== ratio vs rate guard present in prompt ===")
check("prompt mentions 100,000", "100,000 live births" in m.SYSTEM, True)

print("\n=== simulate the graded sequence's history handling ===")
hist = []
seq = [MMR, MEAN, LIST_Q, T1, T2, T3]
sizes = []
for msg in seq:
    if hist and m.is_task_terminus(hist[-1]) and not m.references_prior(msg):
        hist = []
    hist.append(msg)
    sizes.append(len(hist))
check("history depth per turn", sizes, [1, 1, 1, 1, 2, 3])

print()
if fails:
    print("FAILURES:")
    for f in fails:
        print(" -", f)
    raise SystemExit(1)
print("ALL LOGIC TESTS PASSED")
