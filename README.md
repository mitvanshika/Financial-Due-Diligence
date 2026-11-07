# Financial Due-Diligence RAG

Ask questions about Apple's FY2025 10-K. Every answer carries citations and a
confidence band, and the system refuses when the filing does not contain the
answer. Answers can become approved actions (checklist item, review flag, risk
memo), and a team can work together in a shared web room.

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

## Requirements

- Python 3.14 (what it was tested on)
- Node.js (to build the web page)
- A Groq API key (free tier is enough)
- About 8 GB of RAM (the reranker is large)

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
dd chunk                          # filing text -> citation-carrying chunks
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

## Access code

The team room is protected by one shared access code. Put it in `.env`:

```
ROOM_ACCESS_CODE=<a long random code>
```

The code must be long and random, not a word, a name or anything guessable.
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

```
docker run --env-file .env -p 8000:8000 <image>
```

Either way, anyone who can run `docker inspect` on the server can read it.

Honest limits:

- **One shared code.** Everyone with it is in. You cannot remove one person:
  change the code and restart, which disconnects everyone, then hand out the
  new code.
- **No per-user accounts.** Names are typed by each person and not checked.
  Anyone with the code can join under any name, including someone else's.
- **Plain HTTP, no TLS.** The code and everything in the room cross the network
  unencrypted, readable by anyone on the path. Over the internet (for example
  on AWS), put HTTPS in front (a load balancer with a certificate, or a
  reverse proxy), or allow only known IPs in the security group.
- **State in memory, one process.** The queue, any vote in progress and the
  wrong-code counts are lost on a restart. History, finished actions and the
  log are saved to `data/room_state.json`. In a container that file goes
  with the container unless `data/` is on a volume.
- **The lockout is per IP.** Behind Docker Desktop or a proxy all users may
  share one IP, and then a few wrong codes from one person can lock everyone
  out for a minute.

## Tests and eval

```
python -m pytest -q
python eval/run_eval.py [types] [--repeat N]
python eval/compare_runs.py old.jsonl new.jsonl "words from the question"
```

Rename the old `eval/results.jsonl` before a new run. The eval refuses to mix
results from different code.

## Known limitations

(Fill in after the current fixes are merged.)

- Single filing (Apple FY2025 10-K), three years of tables.
- The team room has one shared access code and no per-user accounts (see
  Access code), and holds one room on one server.
- Confidence weights are hand-picked and checked only against the eval set.
- Free-tier model limits make answers take 20 to 60 seconds.