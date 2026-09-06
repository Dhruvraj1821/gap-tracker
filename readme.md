# DSA Thinking-Gap Tracker

A backend service that diagnoses the *reasoning gap* behind a wrong DSA solution, not just the bug. A user submits a problem and their incorrect code, an LLM analyzes the underlying misconception (not the syntax error), and the system tracks recurring gaps over time and across problems using embedding-based similarity search.

## Table of contents

- [Problem statement](#problem-statement)
- [Architecture overview](#architecture-overview)
- [Tech stack and why](#tech-stack-and-why)
- [Data model](#data-model)
- [API reference](#api-reference)
- [Design decisions and tradeoffs](#design-decisions-and-tradeoffs)
- [Known limitations](#known-limitations)
- [Local setup](#local-setup)
- [Environment variables](#environment-variables)
- [Possible future work](#possible-future-work)

## Problem statement

Most DSA practice tools tell you *that* your solution is wrong. They rarely tell you *why you personally* got it wrong in a way that generalizes. A single wrong submission is a data point. Twelve wrong submissions that share the same root misconception are a pattern worth knowing about. This project logs each wrong attempt, uses an LLM to diagnose the specific thinking gap (not the surface-level bug), and surfaces recurring patterns across a user's history using semantic similarity rather than exact string or category matching.

## Architecture overview

```
                     ┌─────────────┐
   Browser  ───────► │  FastAPI    │ ───────► Postgres (users, submissions)
  (plain HTML/JS)     │  API server │
                     └─────────────┘
                            │
                            │ writes row, status=pending
                            ▼
                     ┌─────────────┐
                     │  Postgres   │◄──────── polled every 3s with
                     │ (as queue)  │          SELECT ... FOR UPDATE SKIP LOCKED
                     └─────────────┘
                            │
                            ▼
                     ┌─────────────┐
                     │  worker.py  │ ── embeds submission (Cohere) ──► pgvector similarity search
                     │ (separate   │                                    against user's own history
                     │  process)   │ ── diagnoses gap (Groq LLM) ──────► stores category, note, tags
                     └─────────────┘
```

Two separate processes: the API server (`main.py`, run via `uvicorn`) handles requests and never blocks on external calls. A background worker (`worker.py`, run as its own process) polls for pending work and does everything slow: generating embeddings, running similarity search, and calling the LLM. They communicate only through Postgres, no message broker.

## Tech stack and why

| Component | Choice | Why |
|---|---|---|
| API framework | FastAPI (async) | Async request handling matters because the LLM/embedding calls this system depends on are slow (seconds, not milliseconds); a sync framework would block the whole server on one slow request. |
| Database | PostgreSQL 16 + pgvector extension | Relational data (users, submissions) and vector similarity search in one database, no separate vector store needed. |
| ORM | SQLAlchemy 2.0 (async, `Mapped`/`mapped_column`) | Type-checked models, async session support matching the rest of the stack. |
| Migrations | Alembic | Schema changes tracked and reproducible from a clean database. |
| Password hashing | pwdlib (Argon2) | `passlib`, the older standard, is unmaintained and breaks on current Python. pwdlib is FastAPI's current recommended replacement. |
| Auth tokens | PyJWT, access + refresh split | See [Design decisions](#design-decisions-and-tradeoffs). |
| LLM (gap diagnosis) | Groq API, `openai/gpt-oss-120b` | Free tier, OpenAI-compatible SDK, fast inference. Anthropic, OpenAI, and Gemini were ruled out for this project specifically due to lack of a free tier suitable for iterative development. |
| Embeddings | Cohere Embed v4 | Free trial API key, no credit card. See [Design decisions](#design-decisions-and-tradeoffs) for the tradeoffs this specific choice carries. |
| Background processing | Postgres-as-queue (`SELECT ... FOR UPDATE SKIP LOCKED`) | No Redis, no message broker. See [Design decisions](#design-decisions-and-tradeoffs). |
| Retry logic | tenacity (exponential backoff) | Wraps the Groq call; transient failures get retried instead of immediately failing the submission. |
| Frontend | Plain HTML/CSS/JS, no framework | Scope of the project is backend systems design; the frontend exists to demonstrate the API, not to showcase frontend engineering. |

## Data model

**`users`**
| Column | Type | Notes |
|---|---|---|
| id | int, PK | |
| email | string, unique | |
| hashed_password | string | Argon2 hash, never plaintext |
| created_at | timestamptz | |

**`submissions`**
| Column | Type | Notes |
|---|---|---|
| id | int, PK | |
| user_id | int, FK -> users.id | |
| problem_title | string | |
| problem_statement | string | |
| wrong_code | string | The user's incorrect submission |
| correct_code | string, nullable | Backfilled later if the user provides it; triggers re-analysis |
| gap_category | string, nullable | One of a fixed enum (see prompt design below) |
| gap_note | string, nullable | LLM-generated explanation of the specific reasoning gap |
| topic_tags | string, nullable | Comma-separated (see tradeoff note below) |
| status | string | `pending` -> `processing` -> `complete` \| `failed` |
| embedding | vector(1024), nullable | Cohere Embed v4 output, populated once processed |
| created_at | timestamptz | |
| updated_at | timestamptz | Auto-updated on every row change; used to detect stuck `processing` rows |

## API reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/signup` | none | Create an account |
| POST | `/login` | none | Returns access token, sets httpOnly refresh cookie |
| POST | `/refresh` | refresh cookie | Issues a new access token |
| GET | `/me` | bearer token | Returns the current user's id (used to verify auth works) |
| POST | `/submissions` | bearer token | Accepts a wrong attempt, returns `202` with `status: pending` immediately |
| GET | `/submissions/{id}` | bearer token | Poll for a specific submission's current status/diagnosis |
| GET | `/submissions` | bearer token | Paginated list of the user's submissions, newest first |
| PATCH | `/submissions/{id}/correct-solution` | bearer token | Backfill the correct solution, triggers re-diagnosis |
| GET | `/patterns` | bearer token | Aggregated counts by gap category and topic tag |

## Design decisions and tradeoffs

This section exists because most of the value in this project is in these decisions, not in the code itself. Each one below was a real choice with a real alternative, made deliberately and for a stated reason, not a default.

### Access token + refresh token split, not a single long-lived token

A single token valid for days is a large liability if it leaks. The access token is short-lived (15 minutes) and used on every request; the refresh token is long-lived (7 days) and used only to silently obtain a new access token. The refresh token is stored in an httpOnly cookie, unreadable by JavaScript, which limits exposure to XSS. The access token is stored in `sessionStorage` on the frontend, a deliberate middle ground: readable by JS (a real, accepted risk for the higher-blast-radius long-lived token) but cleared when the tab closes, unlike `localStorage`, which persists indefinitely.

**Constraint accepted:** `sessionStorage` is not maximally secure. A stricter implementation would keep the access token only in memory (a JS variable, lost on refresh) and silently re-fetch it via `/refresh` on every page load. This project accepts the slightly weaker but simpler approach.

### Postgres as the background queue, not Redis or a message broker

The submission-diagnosis pipeline needs to be asynchronous: an LLM call takes seconds, and a synchronous HTTP endpoint should not hold a connection open for that long. The conventional solution is a message broker (Redis + RQ/Celery, RabbitMQ, SQS). This project does not use one.

**Reason:** free-tier hosted Redis is not reliably available at zero cost, and introducing a third-party dependency purely to support an internal queue, for a project of this scale, adds operational risk without a corresponding benefit. Postgres is already a hard dependency; using it as the queue avoids adding a second stateful service to provision, monitor, and keep alive.

**Mechanism:** submissions are inserted with `status = 'pending'`. A separate worker process polls with:
```sql
SELECT ... FROM submissions WHERE status = 'pending'
ORDER BY created_at LIMIT 1
FOR UPDATE SKIP LOCKED
```
`FOR UPDATE` locks the selected row so no other worker can pick it up simultaneously. `SKIP LOCKED` means a second worker, if one existed, would skip past already-locked rows instead of blocking and waiting. This makes it safe to run more than one worker process without any additional coordination logic, without ever needing a real broker.

**Constraints accepted, stated explicitly:**
- Polling has inherent latency (a fixed 3-second interval here), whereas a real message broker pushes work immediately. At this project's scale, that latency is invisible in practice; it would not be at high throughput.
- This does not scale to high submission volume the way a dedicated broker would. Postgres is not designed to be a queue, and under heavy concurrent load this pattern would need to be replaced.
- If a worker process crashes between marking a row `processing` and completing it, that row would be stuck permanently without a check. This project handles it explicitly: on every polling cycle, the worker also resets any row that has been `processing` for longer than 5 minutes back to `pending`, using the `updated_at` column (which auto-updates via SQLAlchemy's `onupdate`) to detect staleness.

### Per-user rate limiting via a direct count query, not a token bucket

`/submissions` checks `COUNT(*) FROM submissions WHERE user_id = X AND created_at >= now() - interval '1 hour'` before accepting a new row, capped at 10 per hour. This is a simpler algorithm than the token-bucket/sliding-window approach used in a separate rate-limiter project built alongside this one. The simpler approach was chosen here deliberately: this project's rate limiting exists to bound LLM API cost and abuse, not to demonstrate rate-limiting algorithm design, which is already covered by the other project. The number 10/hour is a placeholder tuned for "generous for real practice sessions, bounded for worst-case cost," not derived from load testing.

### Retry with exponential backoff, not a full circuit breaker

The Groq API call is wrapped with `tenacity`, retrying up to 3 times with exponential backoff (1s, ~2s, ~4s) before the worker gives up and marks the submission `failed`. A full circuit breaker (tracking consecutive failures across requests, tripping into a cooldown state, and refusing new calls entirely until the cooldown expires) was considered and explicitly not implemented.

**Reason:** a circuit breaker earns its complexity at a traffic volume and failure rate this project does not operate at. At this scale, retry-with-backoff plus a `failed` status (allowing the user to see it happened and eventually re-trigger analysis) covers the realistic failure mode, which is an occasional transient error or rate limit, not a sustained outage that would benefit from a breaker's cooldown behavior.

### Embeddings and retrieval: Cohere over Voyage, symmetric input type, no eval harness (yet)

The original plan was Voyage AI, which offers 200 million free tokens on its current generation, effectively unlimited for a project this size. Voyage's signup was unavailable at the time of building this feature, so Cohere's Embed v4 was used instead, via a free trial API key.

**Constraint accepted:** Cohere's free trial key is capped at 1,000 API calls per month total, and is explicitly not licensed for production traffic. This is workable for a portfolio project's realistic usage but is a real, stated limit, not an oversight. It also shaped an implementation choice: Cohere's embedding model is asymmetric by design, expecting a different `input_type` for a stored document (`search_document`) versus a live query (`search_query`). Because both sides of this project's similarity search are the same kind of object, a past submission compared against a new submission, this project uses `search_document` for both, halving the embedding calls needed per submission. This is a deliberate tradeoff of retrieval quality (using the asymmetric mode as intended would likely retrieve marginally better matches) against staying well inside a hard monthly quota.

**Mechanism:** every completed submission is embedded (problem title, statement, and code, concatenated) and stored in a `vector(1024)` column via pgvector. When a new submission is processed, the worker embeds it, then queries:
```sql
ORDER BY embedding <=> :new_embedding LIMIT 3
```
scoped to the same user and to submissions that already completed, retrieving that user's most semantically similar past mistakes. Those are injected into the LLM prompt with an explicit instruction: if the new submission reflects the same root cause as a retrieved one, the model's note must begin with the literal phrase "This is a recurring pattern:" and name which prior problem(s) match; if the new submission is genuinely unrelated, it should say nothing about history at all. This was tested deliberately with both matching and non-matching cases to confirm the instruction is followed in both directions, not just the positive case.

**What was not done:** a formal evaluation harness (a hand-labeled set of wrong-solution examples with known-correct categories, run through the pipeline to measure categorization accuracy) was planned but deliberately deferred. This is disclosed here rather than implied to exist. The retrieval and prompt-injection mechanism was verified to work correctly through targeted manual testing (confirming it fires on genuine repeats and stays silent on unrelated mistakes), which is real evidence of correctness for the specific cases tested, but is not the same as a systematic accuracy measurement across a broader set.

### Topic tags stored as a comma-separated string, not a normalized table

`topic_tags` is a single string column, split/joined in application code, rather than a proper `tags` table with a many-to-many relationship to submissions. This is a known simplification. It means tag aggregation in `/patterns` happens in Python after fetching all rows, rather than as a single SQL `GROUP BY`, which a normalized schema would allow. This was an accepted shortcut for development speed at the current scale; a production version handling a large number of tags per user would need the normalized version for the aggregation query to remain efficient.

### Fail-loud on missing JWT secret in production, silent fallback in development

`JWT_SECRET` has a hardcoded development fallback so the app runs locally with zero configuration. In production (`ENVIRONMENT=production`), the app refuses to start at all if the real secret is not set via environment variable, rather than silently signing tokens with a publicly known default. This is a deliberate asymmetry: convenience locally, safety in production, decided by an explicit environment flag rather than trying to guess.

## Known limitations

- No automated eval harness for diagnosis accuracy (see above).
- Rate limit threshold (10/hour) is a placeholder, not derived from measured usage patterns.
- Comma-separated topic tags do not scale to large tag vocabularies efficiently.
- Cohere's free-tier embedding key is not production-licensed and is capped at 1,000 calls/month; a real deployment beyond demo/portfolio use would need a paid key or a different provider.
- The background queue (Postgres polling) does not scale to high submission throughput; a real message broker would be needed well before that point.
- Refresh cookie `secure`/`samesite` flags are environment-conditional but have not been tested against a real HTTPS deployment yet, only reasoned about.

## Local setup

Requires Docker, Python 3.12+, and API keys for Groq and Cohere (both free).

```powershell
git clone <repo-url>
cd gap-tracker
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=devpassword --name gaptracker-postgres pgvector/pgvector:pg16
docker exec -it gaptracker-postgres createdb -U postgres gaptracker
docker exec -it gaptracker-postgres psql -U postgres -d gaptracker -c "CREATE EXTENSION IF NOT EXISTS vector;"

alembic upgrade head
```

Copy `.env.example` to `.env` and fill in real values (see below), then run the API and worker as two separate processes:

```powershell
uvicorn main:app --reload
```
```powershell
python worker.py
```

Serve the frontend separately (any static file server; e.g. `python -m http.server 5500` from the `frontend/` directory).

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `JWT_SECRET` | Required in production; has a dev fallback locally | Signs access/refresh tokens |
| `GROQ_API_KEY` | Yes | LLM gap diagnosis |
| `COHERE_API_KEY` | Yes | Embedding generation |
| `ENVIRONMENT` | No, defaults to `development` | Set to `production` to enforce `JWT_SECRET` and strict cookie flags |

## Possible future work

- A recency- and frequency-weighted scoring system over gap categories, to recommend what to review next rather than only reporting raw counts.
- A formal eval harness: a hand-labeled set of wrong-solution examples with known categories, run through the pipeline, with an accuracy score reported here.
- Migrating `topic_tags` to a normalized table if tag volume grows.
- Load-testing the Postgres-as-queue approach to find its actual throughput ceiling, and documenting the point at which a real broker would become necessary.