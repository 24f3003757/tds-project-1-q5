# TDS Project 1: Data Analyst Telegram Bot

An LLM agent reachable on Telegram. It receives a data analysis question as
plain text, computes or researches the answer using a tool loop, and replies
with exactly one JSON object in the shape the message asked for.

Every run is logged as JSONL and served publicly at `/run.jsonl`.

## Architecture

One process runs two things:

1. **FastAPI app** serving `GET /run.jsonl` (the public run log) and `GET /`
   (health check, which also echoes the configured `log_url`).
2. **Background thread** long polling the Telegram Bot API. Long polling was
   chosen over webhooks so the bot needs no inbound routing and behaves
   identically on a laptop and on a host.

Each incoming message is handled in its own thread, serialised per chat by a
lock so a slow answer can never arrive after the following question.

### The agent loop

The model is given five tools and iterates until it produces a final answer,
capped at 12 steps or 100 seconds of wall clock:

| Tool | Purpose |
| --- | --- |
| `run_python` | Arithmetic and statistics over data already in hand. |
| `web_search` | Find sources. Tries Tavily, Brave, Google CSE, then a DuckDuckGo scraper, using whichever is configured. |
| `fetch_url` | Download HTML or PDF and return readable text. PDFs are parsed with `pdfplumber`, including table extraction. |
| `search_document` | Ranked search inside an already fetched document. |
| `read_tables` | Return every table extracted from a fetched PDF. |

## Design decisions

**Routing before researching.** The first question the agent settles is where
the data lives. A message that hands over its own figures is self contained, so
it is answered with one `run_python` call and no network access at all.
`needs_external_data` decides this in a fixed precedence order. An explicit URL
always wins, because a URL is an instruction to read that document even when
figures are quoted beside it. Otherwise inline figures win, which deliberately
overrides a mention of MOSPI or a bulletin: "these values come from a MOSPI
bulletin and are reproduced here in full" is an arithmetic question wearing a
dataset's name, and sending it to a search engine wastes the budget and invites
an answer to a different question. With no URL and no figures, the question is
assumed to need looking up.

Counting figures needs two refinements to be trustworthy. Reporting periods are
stripped first, because "between the 2014-16 period and the 2019-21 period"
contains four numerals and no data. Thousands separated values are matched as
one number rather than two, so "per 100,000 live births" does not read as a
pair of figures. After those, every lookup question in the eval suite reduces to
zero numbers and every computation question keeps at least two, which is where
the threshold sits.

**Task boundary detection.** A grader sends its next question seconds after
receiving the previous reply, so elapsed time cannot distinguish a new question
from the next turn of the current one. The reliable separator is the output
template: a message that states its own JSON shape is the terminus of a task,
so the message after it begins a new one and the history is cleared. A message
that carries no data, names no source, and opens with a continuation cue
(`Now`, `Ignore`, `Also`) is treated as a later turn of the current task and
keeps the history. Two safety nets remain: a 240 second silence also clears the
history, and the final message is labelled explicitly in the prompt as the one
to answer.

**Shape mirroring, with log_url always present.** The grader parses the whole
reply and compares it to the expected answer, so a correct value inside the
wrong wrapper scores zero. Two shapes are in play: the portal's worked example
shows the `{"answer": ..., "log_url": ...}` envelope while the grading repo's
README shows a bare object. The course confirmed both will be accepted, and
that `log_url` should be included even on questions that do not ask for it.

`conform` therefore parses the skeleton out of the message and reshapes the
model's output against it. Where the message showed the envelope, the result
goes inside `answer`. Where the message showed a bare object, the requested
keys stay at the top level and `log_url` is added beside them. A single key
whose name the model invented is renamed back to the one the message asked
for, with `log_url` excluded from that count so it can never be mistaken for
the payload key. `log_url` is written by the program and never by the model,
which fabricates URLs. The `ALWAYS_INCLUDE_LOG_URL` flag reverts to strict
mirroring in one line if the guidance changes again.

**One reply per message.** The grading harness calls `get_response()` once per
message sent, so it reads the *first* message the bot sends back. A progress
update or acknowledgement would be consumed as the answer. The bot therefore
sends exactly one message per incoming message and never a status line. In a
multi turn exchange it must still answer every turn, because the harness waits
for a reply before sending the next message; only the final reply is graded.

**Ranked retrieval with inverse frequency weighting.** A naive search inside a
document returns the first matches in document order. Because a question's own
words (`maternal`, `mortality`) recur throughout a report, that always returns
page one and never reaches the statistical table. Each query term is therefore
weighted by `1 / log(2 + count_in_document)`, so rare terms dominate, and
windows are scored and ranked rather than taken positionally. Digit density and
row separator density add to the score, since that is what a table looks like.

**Loop detection.** Tool calls are fingerprinted by name plus arguments. A
repeat is not executed; the model instead receives a message stating the call
was already made and listing concrete alternatives. Without this the agent can
spend its entire budget repeating one fruitless call.

**Document abandonment.** After three unsuccessful probes into the same URL,
the result carries a note that the figures are probably chart images and the
document should be dropped. Several MOSPI publications store state level data
as charts, which no text extractor can recover.

**Grounding gate, applied conditionally.** On the research path the agent may
not produce a final answer before at least one `web_search` or `fetch_url` has
succeeded, because a model with no data will otherwise write a table of
invented numbers and compute over it. The gate is skipped in two cases. It is
skipped when the data is inline, since forcing a search on a question that
already contains its numbers sends the agent to the web for an answer it was
handed and it returns with an answer to a different question. It is also
skipped on any turn that states no output shape, because such a turn is never
the reply that gets graded and researching it only burns budget shared with the
turns that are.

**Placeholder detection and commit pass.** Under exact match grading, an
unanswered question and a wrong answer score identically, so committing to a
best guess weakly dominates returning `N/A`. `looks_like_placeholder` walks the
JSON recursively for nulls, empty collections, angle bracket templates and
known filler strings; when it fires, the model gets one more call demanding a
commitment.

**Shape aware fallback.** If everything fails, the JSON skeleton is parsed out
of the question itself so the reply at least matches the requested shape. The
skeleton parser strips the quotes around a quoted placeholder before
substituting, since `"<state name>"` would otherwise substitute to `""?""`,
which is not valid JSON.

**Capped `max_tokens`.** OpenRouter reserves the requested ceiling against the
account balance before a call runs, so an uncapped request fails on a low
balance even though the actual reply is tiny. Capped at 1200.

**Auto echo in `run_python`.** A trailing bare expression is wrapped in
`print()`, since models habitually end a snippet as if in a REPL and would
otherwise waste a step discovering that a subprocess prints nothing.

**Step and time budget sized to the harness.** The harness applies one timeout
to an entire multi turn exchange, so a three turn question gets the same total
budget as a single turn one. The per message budget is therefore 100 seconds
and 12 steps: if twelve tool calls have not found the answer, twenty will not
either, and the remaining time is better spent on the next turn.

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in, then export the variables
uvicorn main:app --host 0.0.0.0 --port 8000
```

`python3 test_logic.py` exercises the routing, task boundary, shape
conformance, placeholder rejection and JSON extraction logic with no network
and no API keys. `EVALS.md` documents the 38 question local eval suite in
`evals/questions.json` and what each question is designed to catch.

## Deployment

Render web service.

- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Environment: `TELEGRAM_BOT_TOKEN`, `OPENROUTER_TOKEN`, `TAVILY_TOKEN`,
  `PUBLIC_BASE_URL`, optionally `MODEL` and `MAX_TOKENS`

An UptimeRobot monitor pings `/` every five minutes so the free instance never
idles into a cold start, which would otherwise consume most of a question's
timeout budget.

## Known limitations and compromises

**Relaxed TLS verification for government hosts.** Several `.gov.in` hosts,
including `censusindia.gov.in`, serve an incomplete certificate chain that
Python rejects while browsers accept. On an SSL error `fetch_url` retries with
verification disabled. This is a deliberate tradeoff for reading public
statistical bulletins and would not be acceptable in production.

**Ephemeral log storage.** Render's disk resets on restart, so `run.jsonl`
holds the current session rather than full history. Setting `GITHUB_TOKEN` and
`GITHUB_REPO` switches `log_url` to a static `raw.githubusercontent.com` URL
that survives restarts. Either way it is publicly downloadable, which is what
the specification requires.

**Chart data is unrecoverable.** Where a publication renders state level
figures as an image, no text extraction can recover them. The agent detects
this pattern and moves to another source rather than misreading axis labels as
data.

**MMR versus Maternal Mortality Rate.** The SRS bulletin reports two different
columns: Maternal Mortality *Ratio* (deaths per 100,000 live births) and
Maternal Mortality *Rate* (per 1,000 women of reproductive age). Questions
saying "rate" colloquially mean the Ratio, which is the widely reported
headline figure. The agent is told the expected magnitude of each column, so a
column whose maximum is below 40 is recognised as the wrong one rather than
reported as the answer.

**The leading state depends on the edition.** The bulletin is reissued roughly
annually and the ranking is not stable across editions: Assam led the 2017-19
and 2018-20 editions, Madhya Pradesh led 2019-21, and Uttar Pradesh leads the
2022-24 edition. A "which state is highest" question is therefore a recency
question in disguise, and answering it from a remembered figure or from a news
summary of an older edition is the most likely way to get it wrong. The agent
is instructed to establish which edition it is reading and to search again with
a newer period if its source is more than one edition behind.

**JavaScript rendered pages.** Some MOSPI pages build their tables in the
browser, so a plain fetch returns an empty shell. The agent treats a near empty
page as unusable and looks for a PDF or an alternative source.

**Key renaming is top level only.** `conform` restores an asked-for key name
only for a single key object. A nested object whose inner keys the model
renamed is left alone, since guessing which invented key maps to which
requested one is less safe than leaving the model's output intact.
