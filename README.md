# TDS Project 1: Data Analyst Telegram Bot

An LLM agent reachable on Telegram. It receives a data analysis question as
plain text, researches or computes the answer using a tool loop, and replies
with exactly one JSON object in the shape the message asked for.

Every run is logged as JSONL and served publicly at `/run.jsonl`.

## Architecture

One process runs two things:

1. **FastAPI app** serving `GET /run.jsonl` (the public run log) and `GET /`
   (health check, which also echoes the configured `log_url`).
2. **Background thread** long polling the Telegram Bot API. Long polling was
   chosen over webhooks so the bot needs no inbound routing and behaves
   identically on a laptop and on a host.

Each incoming message is handled in its own thread, so a slow research question
never blocks the next message.

### The agent loop

The model is given four tools and iterates until it produces a final answer,
capped at 20 steps or 200 seconds of wall clock:

| Tool | Purpose |
| --- | --- |
| `web_search` | Find sources. Tries Tavily, Brave, Google CSE, then a DuckDuckGo scraper, using whichever is configured. |
| `fetch_url` | Download HTML or PDF and return readable text. PDFs are parsed with `pdfplumber`, including table extraction. |
| `search_document` | Ranked search inside an already fetched document. |
| `read_tables` | Return every table extracted from a fetched PDF. |

## Design decisions

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

**Grounding gate.** The agent may not produce a final answer before at least
one `web_search` or `fetch_url` has succeeded. `run_python` deliberately does
not count, because a model with no data will otherwise write a table of
invented numbers and compute over it.

**Placeholder detection and commit pass.** Under exact match grading, an
unanswered question and a wrong answer score identically, so committing to a
best guess weakly dominates returning `N/A`. `looks_like_placeholder` walks the
JSON recursively for nulls, empty collections, angle bracket templates and
known filler strings; when it fires, the model gets one more call demanding a
commitment.

**Shape aware fallback.** If everything fails, the JSON skeleton is parsed out
of the question itself so the reply at least matches the requested shape.

**Capped `max_tokens`.** OpenRouter reserves the requested ceiling against the
account balance before a call runs, so an uncapped request fails on a low
balance even though the actual reply is tiny. Capped at 1200.

**Auto echo in `run_python`.** A trailing bare expression is wrapped in
`print()`, since models habitually end a snippet as if in a REPL and would
otherwise waste a step discovering that a subprocess prints nothing.

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in, then export the variables
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Deployment

Render web service.

- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Environment: `TELEGRAM_BOT_TOKEN`, `OPENROUTER_TOKEN`, `TAVILY_TOKEN`,
  `PUBLIC_BASE_URL`, optionally `MODEL` and `MAX_TOKENS`

An UptimeRobot monitor pings `/` every five minutes so the free instance never
idles into a cold start.

## Known limitations and compromises

**Relaxed TLS verification for government hosts.** Several `.gov.in` hosts,
including `censusindia.gov.in`, serve an incomplete certificate chain that
Python rejects while browsers accept. On an SSL error `fetch_url` retries with
verification disabled. This is a deliberate tradeoff for reading public
statistical bulletins and would not be acceptable in production.

**Ephemeral log storage.** Render's disk resets on restart, so `run.jsonl`
holds the current session rather than full history. It is always publicly
downloadable, which is what the specification requires.

**Chart data is unrecoverable.** Where a publication renders state level
figures as an image, no text extraction can recover them. The agent detects
this pattern and moves to another source rather than misreading axis labels as
data.

**MMR versus Maternal Mortality Rate.** The SRS bulletin reports two different
columns: Maternal Mortality *Ratio* (deaths per 100,000 live births) and
Maternal Mortality *Rate* (per 1,000 women of reproductive age). For 2021 to
2023 the highest Ratio is Odisha at 153, while the highest Rate is shared by
Madhya Pradesh and Uttar Pradesh at 12. Questions saying "rate" colloquially
mean the Ratio, which is the widely reported headline figure, and that is the
reading the agent takes.

**JavaScript rendered pages.** Some MOSPI pages build their tables in the
browser, so a plain fetch returns an empty shell. The agent treats a near empty
page as unusable and looks for a PDF or an alternative source.