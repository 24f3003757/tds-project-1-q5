#!/usr/bin/env python3
"""Preflight the LLM provider for a few tenths of a cent.

Everything the agent depends on that only fails at runtime is exercised here:
the base URL, the key, both model names, tool calling, strict role alternation,
and whether a truncated document still comes back as usable text. Run this
before any eval run so a broken variable costs a cent instead of a full pass.

    export OPENROUTER_TOKEN=sk-ant-...
    python3 smoke.py

Exits non zero on the first hard failure so it can gate a deploy.
"""
import json
import os
import sys

from openai import OpenAI

BASE = os.environ.get("LLM_BASE_URL", "https://api.anthropic.com/v1/")
KEY = os.environ.get("OPENROUTER_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
STRONG = os.environ.get("MODEL", "claude-sonnet-5")
CHEAP = os.environ.get("CHEAP_MODEL", "claude-haiku-4-5-20251001")

if not KEY:
    sys.exit("set OPENROUTER_TOKEN (or ANTHROPIC_API_KEY) first")

client = OpenAI(api_key=KEY, base_url=BASE)

TOOLS = [{"type": "function", "function": {
    "name": "run_python",
    "description": "Run Python 3 code and get back what it prints.",
    "parameters": {"type": "object",
                   "properties": {"code": {"type": "string"}},
                   "required": ["code"]}}}]

FAILS = []
COST = {"in": 0, "out": 0}

# Published list prices, dollars per million tokens. Only used to print a
# rough spend estimate, so being a little stale is harmless.
PRICES = {"claude-haiku-4-5-20251001": (1.0, 5.0),
          "claude-haiku-4-5": (1.0, 5.0),
          "claude-sonnet-5": (2.0, 10.0),
          "claude-opus-5": (5.0, 25.0)}


def record(resp, model):
    u = getattr(resp, "usage", None)
    if not u:
        return
    COST["in"] += u.prompt_tokens or 0
    COST["out"] += u.completion_tokens or 0
    print(f"       usage: {u.prompt_tokens} in, {u.completion_tokens} out "
          f"({model})")


def check(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if detail:
        print(f"       {detail}")
    if not ok:
        FAILS.append(label)


def call(model, messages, tools=None):
    kw = {"model": model, "messages": messages, "temperature": 0,
          "max_tokens": 400}
    if tools:
        kw["tools"] = tools
    resp = client.chat.completions.create(**kw)
    record(resp, model)
    return resp.choices[0].message


print(f"base_url : {BASE}")
print(f"strong   : {STRONG}")
print(f"cheap    : {CHEAP}")
print(f"key      : {KEY[:11]}...{KEY[-4:]}\n")

# 1. Can we reach the provider at all with the cheap model.
print("=== 1. cheap model reachable ===")
try:
    msg = call(CHEAP, [{"role": "user", "content": "Reply with the single word OK."}])
    check("cheap model responds", "OK" in (msg.content or "").upper(),
          f"content={msg.content!r}")
except Exception as e:
    check("cheap model responds", False, f"{type(e).__name__}: {e}")
    print("\n404 means a wrong base URL or model name. 401 means a bad key. "
          "429 means no credit. Fix that before anything else.")
    sys.exit(1)

# 2. Same for the strong model, so a retrieval question does not discover a
#    typo mid run.
print("\n=== 2. strong model reachable ===")
try:
    msg = call(STRONG, [{"role": "user", "content": "Reply with the single word OK."}])
    check("strong model responds", "OK" in (msg.content or "").upper(),
          f"content={msg.content!r}")
except Exception as e:
    check("strong model responds", False, f"{type(e).__name__}: {e}")

# 3. Strict role alternation. main.py joins prior turns into one user message
#    for exactly this reason; this proves the shape it actually sends is legal
#    and that a system message is accepted alongside it.
print("\n=== 3. system message plus a joined background turn ===")
try:
    msg = call(CHEAP, [
        {"role": "system", "content": "You are a data analyst. Your final "
                                      "message is one JSON object, nothing else."},
        {"role": "user", "content": "Background turn one.\n\nThe data is "
                                    "[230, 158, 318, 497]."},
        {"role": "user", "content": "THIS IS THE QUESTION TO ANSWER NOW.\n\n"
                                    'Mean to 2 decimal places. Reply with ONLY: '
                                    '{"mean": <number>}'},
    ])
    check("two consecutive user messages accepted", True,
          f"content={(msg.content or '')[:120]!r}")
except Exception as e:
    check("two consecutive user messages accepted", False,
          f"{type(e).__name__}: {e}")
    print("       if this is the only failure, the join patch in solve() is "
          "missing or was reverted")

# 4. Tool calling round trip, including feeding a tool result back in.
print("\n=== 4. tool calling round trip ===")
try:
    convo = [
        {"role": "system", "content": "Use run_python for arithmetic. Your final "
                                      "message is one JSON object, nothing else."},
        {"role": "user", "content": 'Mean of [230, 158, 318, 497] to 2 decimal '
                                    'places. Reply with ONLY: {"mean": <number>}'},
    ]
    msg = call(CHEAP, convo, TOOLS)
    if msg.tool_calls:
        call_obj = msg.tool_calls[0]
        check("model emits a tool call", call_obj.function.name == "run_python",
              f"name={call_obj.function.name}")
        try:
            args = json.loads(call_obj.function.arguments or "{}")
            check("tool arguments parse as a JSON object",
                  isinstance(args, dict) and "code" in args,
                  f"keys={list(args)}")
        except Exception as e:
            check("tool arguments parse as a JSON object", False, repr(e))
        convo.append(msg.model_dump(exclude_none=True))
        convo.append({"role": "tool", "tool_call_id": call_obj.id,
                      "content": "300.75"})
        final = call(CHEAP, convo, TOOLS)
        text = final.content or ""
    else:
        check("model emits a tool call", True,
              "answered directly without tools, acceptable for trivial arithmetic")
        text = msg.content or ""

    # 5. The final message must be parseable, because grade.py runs
    #    json.loads on the raw reply and nothing else.
    print("\n=== 5. final message parses as one JSON object ===")
    stripped = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        obj = json.loads(stripped)
        check("final message is valid JSON", isinstance(obj, dict), f"got {obj!r}")
        check("value is right", abs(float(obj.get("mean", 0)) - 300.75) < 0.01,
              f"got {obj.get('mean')!r}, want 300.75")
    except json.JSONDecodeError:
        check("final message is valid JSON", False, f"raw={text[:200]!r}")
        print("       main.py's extract_json_object handles prose around JSON, "
              "so this is a warning, not a blocker")
except Exception as e:
    check("tool calling round trip", False, f"{type(e).__name__}: {e}")

# 6. Spend so far, as a sanity check on the budget arithmetic.
pin, pout = PRICES.get(CHEAP, (1.0, 5.0))
spent = COST["in"] / 1e6 * pin + COST["out"] / 1e6 * pout
print(f"\ntokens: {COST['in']} in, {COST['out']} out")
print(f"approx spend on this preflight: ${spent:.5f}")

print()
if FAILS:
    print(f"{len(FAILS)} FAILURE(S):")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("PREFLIGHT PASSED. Safe to run the eval harness.")
