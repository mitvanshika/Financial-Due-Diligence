# Financial Due-Diligence RAG

Ask questions about Apple's FY2025 10-K. Every answer carries citations and a
confidence band, and the system refuses when the filing does not contain the
answer. Answers can become approved actions (checklist item, review flag, risk
memo), and a team can work together in a shared web room.

Built by Vanshika Mittal and Samarth.

## What makes it different

- **Two searches, then a judge.** Meaning search (Qdrant, bge-small) and keyword
  search (BM25) each return 30 candidates. They are merged with Reciprocal Rank
  Fusion, and a reranker (bge-reranker-large) keeps the best 6. A passage that
  both searches rank in their top 5 is kept even if the reranker scores it low.
- **It checks its own evidence.** A grader model scores every retrieved passage
  from 0 to 1. If the evidence is weak, the question is reworded and tried again
  (up to 3 attempts in total). If the loop stops making progress, or the filing
  clearly does not cover the question, the system refuses instead of guessing.
- **Figures come from a database, not from prose.** Income statement, balance
  sheet and cash flow sit in DuckDB. Figure answers cite them as [S1], [S2].
  Building the database checks the key figures against the eval's known answers
  and ends with "All checks passed."
- **Numbers are checked.** A number in the answer that matches nothing in the
  cited evidence forces the confidence to LOW.
- **Confidence has visible parts.** The score is built from the grades of the
  evidence, and a borderline-graded figure lowers it. It is shown as HIGH,
  MEDIUM or LOW with the number.
- **Four ways to refuse, on purpose.** The retrieval loop gave up; the model says
  the evidence does not answer the question; the answer cites nothing real; the
  model call failed. Each one is said plainly.
- **Actions are verified, never automatic.** Propose, validate with hard rules,
  a person approves, then execute. A checklist item or memo can only cite sources
  the answer already cited. A review flag hands a refused or low-confidence
  question to a human analyst.
- **A shared room.** FastAPI + WebSocket + React: a question queue, live
  pipeline steps for everyone, and a group vote (every person present must
  approve an action; one rejection stops it).
- **Access code.** The room refuses to start on a public address without one.
- **Tested, not assumed.** 212 unit tests, a 29-question eval with known
  answers, a retrieval probe that checks the right passage is in the final 6,
  and an eval that refuses to mix results from different code.

## Layers

| Layer | What it does |
|---|---|
| 1 Ingest | Filing text plus three financial tables (income statement, balance sheet, cash flow) in DuckDB |
| 2 Index | Chunks into Qdrant (meaning search) and BM25 (keyword search) |
| 3 Retrieve | Two searches merged with RRF, then a reranker picks the best 6; plus a database lookup |
| 4 Self-heal | A checker model grades the evidence; reword and retry, or refuse |
| 5 Generate | Cited answer, number check, confidence score |
| 6 Actions | Propose, validate, human approval, execute |
| 7 Team room | FastAPI + WebSocket + React: queue, live steps, group vote |
| Eval | 29 questions with known answers |

## Results

Last full eval (commit `7209038`, 29 questions, 28 s average per question):

| Question type | Result |
|---|---|
| Numeric lookups | 8 of 8 correct |
| Questions the filing cannot answer (must refuse) | 5 of 5 refused |
| Auto-graded answers at HIGH confidence | 6 of 6 correct |
| Auto-graded answers at MEDIUM confidence | 2 of 2 correct |
| Narrative, cross-referencing and trend (graded by hand) | TODO: fill in after grading `eval/review.md` |

Questions that still fail or are incomplete are listed under Known limitations.

## Requirements

- Python 3.14 (what it was tested on)
- Node.js (to build the web page)
- A Groq API key (free tier is enough)
- About 8 GB of RAM (the reranker is large)
- Docker (optional, see below)

## Setup

```
python -m venv .venv
.venv\Scripts\Activate.ps1        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
Copy-Item .env.example .env       # then put your Groq key in .env
cd web; npm install; npm run build; cd ..
```

## Build the data (once)

The raw filing and tables are in `data/raw`.

```
python build_financials_db.py     # run from the repo root; ends with "All checks passed."
dd chunk                          # filing text -> citation-carrying chunks (343)
dd index                          # chunks -> Qdrant + BM25
dd stats                          # check what is on disk
```

## Run

```
dd answer "What was Apple's net income for FY2025?"
dd serve                          # team room at http://localhost:8000 - needs ROOM_ACCESS_CODE in .env
dd serve --host 127.0.0.1         # this machine only: runs with no code (auth off)
```

Only one process can use the search index at a time. Stop `dd serve` before
running `dd answer`, the eval, or any other `dd` command.

Other commands: `dd retrieve`, `dd heal`, `dd probe`, `dd session`,
`dd act "question" --action checklist|flag|memo`, `dd flags`,
`dd close-flag <id> --note "..."`.

## Run with Docker

The image holds the code, the built web page and both models (unused model
file formats are skipped to keep it small). The data (database, chunks, index)
is built once into a volume.

```
docker build -t dd-app .
docker run --rm -v dd-data:/app/data dd-app python build_financials_db.py
docker run --rm -v dd-data:/app/data dd-app dd chunk
docker run --rm -v dd-data:/app/data dd-app dd index
docker run --rm -v dd-data:/app/data dd-app dd stats     # expect 343 / 343 / 343
docker run --rm --env-file .env -v dd-data:/app/data -p 8000:8000 dd-app
```

Then open `http://localhost:8000` (not the `172.x` address the server prints,
that one is inside the container).

- The first build takes 10 to 25 minutes (torch and the models).
- The server refuses to start without `ROOM_ACCESS_CODE`. Try it: run the last
  command without `--env-file`.
- `.env` must be plain `KEY=value` lines: no quotes and no `# comments` on a line,
  because Docker keeps them as part of the value.
- `.env` is in `.dockerignore`, so secrets reach the container at run time and
  never sit inside the image.
- The container and the data are separate. Containers are thrown away
  (`--rm`); the volume `dd-data` keeps the index.

## Live demo link (Cloudflare Tunnel)

A quick tunnel gives your local container a temporary public HTTPS link. It is
for demos, not hosting: it only works while your laptop is on, awake and online.

```
# Terminal 1: the app
docker run --rm --env-file .env -v dd-data:/app/data -p 8000:8000 dd-app

# One-time install (Windows), then reopen the terminal
winget install --id Cloudflare.cloudflared

# Terminal 2: the tunnel (use the full path if "cloudflared" is not found)
cloudflared tunnel --url http://localhost:8000
```

It prints a link ending in `trycloudflare.com`.

- **Copy the link, do not type it.** A one-letter typo opens a different, ad-filled
  website.
- A new link is created every time the tunnel starts.
- Anyone with the link reaches the login screen. Use a code made only for the
  demo and change it afterwards. All visitors look like one IP to the lockout,
  so five wrong codes can block everyone for a minute.
- Stop with Ctrl+C in both terminals.

## Access code

The team room is protected by one shared access code. Put it in `.env`:

```
ROOM_ACCESS_CODE=12345
```


One way to make one:

```
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

Give it to the team over a private channel. The page asks for it once and
keeps it until the browser tab is closed. Scripts calling `/api/health` send it
as `Authorization: Bearer <code>`. The web page's files themselves stay public;
they hold no data.

- `dd serve` on any address other than this machine alone (the default
  `0.0.0.0`, `::`, a LAN or Tailscale IP) refuses to start without a code.
- `dd serve --host 127.0.0.1` with no code runs with auth off and prints a
  warning. Only this machine can reach it.
- Five wrong codes from one IP within a minute lock that IP out for the rest
  of the minute.

In Docker, pass the code with `--env-file`, not `-e`: `-e` puts the code on
the command line, where it ends up in shell history and the process list.
Either way, anyone who can run `docker inspect` on the server can read it.

Honest limits:

- **One shared code.** Everyone with it is in. You cannot remove one person:
  change the code and restart, which disconnects everyone, then hand out the
  new code.
- **No per-user accounts.** Names are typed by each person and not checked.
  Anyone with the code can join under any name, including someone else's.
- **Plain HTTP, no TLS** on `dd serve` itself. The code and everything in the
  room cross the network unencrypted when you reach it directly. Put HTTPS in
  front for anything beyond a demo (a reverse proxy with a certificate), or
  allow only known IPs. A Cloudflare quick tunnel gives HTTPS for demos.
- **State in memory, one process.** The queue, any vote in progress and the
  wrong-code counts are lost on a restart. History, finished actions and the
  log are saved to `data/room_state.json`. In a container that file goes
  with the container unless `data/` is on a volume.
- **The lockout is per IP.** Behind Docker Desktop, a tunnel or a proxy all users
  may share one IP, and then a few wrong codes from one person can lock everyone
  out for a minute.

## Tests and eval

```
python -m pytest -q
python eval/run_eval.py [types] [--repeat N]
python eval/compare_runs.py old.jsonl new.jsonl "words from the question"
python eval/probe_retrieval.py --label <name>
```

Rename the old `eval/results.jsonl` before a new run. The eval refuses to mix
results from different code.

The retrieval probe (`eval/retrieval_probe.json`) lists, for nine questions,
which chunk must be in the final 6. If you rebuild the chunks, chunk ids can
change: re-read each listed chunk before trusting the probe again.

## What went wrong, and what we did about it

- **The reranker threw away the tariff passage.** The passage was number 2 in
  meaning search and number 4 in keyword search, yet the reranker scored it low
  and dropped it. Fix: a chunk both searches put in their top 5 is always kept.
- **Tables lost their year headings.** A table cut into pieces left some pieces
  with numbers and no years. Fix: the chunker keeps a table whole where it fits;
  when a table must be split, every piece carries the title, units and years.
  Column padding is removed, and the exhibit index (Item 15) is dropped, because
  its filing numbers matched numeric queries. 343 chunks now.
- **Chunk ids moved.** After the new chunker the retrieval probe pointed at the
  wrong passages, some ids still existed but held different text. Fix: every id
  re-read by hand, and a second person's remap by content matched exactly.
- **Groq sometimes rejects a call with `json_validate_failed`.** At temperature 0
  it repeats the same mistake. Fix: one retry at temperature 0.5 in the grader,
  the answer call and the actions. The eval found it (one question crashed);
  the grader and the answer call each have three tests for it.
- **Confidence moved between runs.** Same code, two runs: three of 29 questions
  changed band, with unchanged correct answers (HIGH to MEDIUM and LOW). The
  grader is a model, so it is not perfectly repeatable. We report it instead of
  hiding it.
- **The pinned packages did not install on Python 3.14.** numpy 2.2.6 had no
  ready-made build, and pandas was pinned to 2.x while everything was tested on
  3.x. A clean Docker build caught it. Fix: pins now match the tested
  environment.
- **The image was far too big.** The model repositories ship the same weights in
  several formats. Skipping the unused ones cut the models from 6.7 GB to 2.3 GB.

## Known limitations

- Single filing (Apple FY2025 10-K): three years of income statement and cash
  flow, two years of balance sheet. Broad questions such as "how have Apple's
  finances been over the years" are untested.
- Confidence weights are hand-picked and checked only against the eval set. It is
  often too low on correct figure answers (equity, EPS, balance sheet check,
  cash flow check, the EPS-gap question).
- The database lookup uses a short hand-picked list of row labels.
- Free-tier model limits: a daily token limit, and answers take 20 to 60 seconds.
- The team room has one shared access code, no per-user accounts, plain HTTP and
  state in memory (see Access code). It holds one room on one server.
- There is no always-on deployment yet. Docker and a Cloudflare tunnel give
  a repeatable local run and a temporary public demo link.
- `requirements.txt` pins only the top-level packages; the packages underneath
  can differ between machines.

## Project layout

```
src/dd/         the pipeline: chunking, indexing, retrieval, self_healing,
                generation, actions, room, server, cli
web/            React team room (built into web/dist)
eval/           29-question eval, retrieval probe, run comparison
tests/          unit tests
data/raw/       the filing and the three tables
Dockerfile      multi-stage build: web page, then the app with models inside
```
