# Agent rules — read this first, then do only your step

You are implementing **one isolated step** of a build plan. Another agent did the previous step; another will do the next.

## Hard rules

1. **Do not read `HLD.md`.** It is 700+ lines of design rationale. Everything you need is in your step prompt. Reading it wastes the token budget this plan exists to protect.
2. **Only touch the files your step names.** If you believe another file needs changing, stop and say so in your final message instead of changing it.
3. **`app/contracts.py` is frozen.** It is the shared interface between all steps. Read it, import from it, never edit it. If it is genuinely wrong, stop and report — do not patch around it.
4. **Run your step's verify command before reporting done.** If it fails, fix it. If you cannot, report the exact error output. Never report success on unverified work.
5. **No tests, no docstring essays, no README updates** unless your step asks. Non-functional concerns (auth hardening, retries, metrics, rate limits) are explicitly out of scope for every step in this plan.
6. **Match the surrounding code.** Same imports, naming, and error style as the files you read.

## Project facts

- Python 3.12, FastAPI, SQLAlchemy 2.x, `anthropic` SDK, async throughout.
- Postgres, Redis, and Kroki run via `docker compose up -d`. Connection strings come from `.env` — never hardcode them.
- LLM: `claude-opus-5`. Always `thinking={"type": "adaptive"}`. Use `output_config={"format": {...}}` for JSON-shaped output. Stream anything with large `max_tokens`. Never pass `budget_tokens` or `temperature` — both return 400 on this model.
- Frontend is **one file**: `static/index.html`, vanilla JS, mermaid.js from CDN. There is no npm, no build step, no React.
- The only Node dependency is a Mermaid-parse sidecar. Nothing else uses Node.

## Final message format

Three lines, nothing more:

```
DONE: <step id>
FILES: <paths you created or modified>
VERIFY: <the command you ran> → <pass/fail + one-line result>
```

---

# Local environment overrides — THESE WIN over anything in your step prompt

`PLAN.md` was written for Docker + Python 3.12. This machine has **no Docker and no
container runtime**, and system Python is 3.9. The overrides below are not optional;
apply them wherever your step prompt conflicts.

### 1. Python

Use the project venv at `.venv/` (Python 3.13). It already exists.
**Every** command you run uses `.venv/bin/python` or `.venv/bin/pip` — never bare
`python`/`python3`, which is 3.9 and will fail on `StrEnum` and modern typing.
When your step's VERIFY line says `python ...`, read it as `.venv/bin/python ...`.

### 2. No Docker — Postgres becomes SQLite

- `DATABASE_URL` defaults to `sqlite+aiosqlite:///./uml.db`.
- Write portable SQLAlchemy 2.x that runs on **both** SQLite and Postgres:
  use `sqlalchemy.Uuid` for UUID columns (not the postgres-only `UUID`),
  `sqlalchemy.JSON` for JSON columns (**not** `JSONB`),
  `DateTime(timezone=True)` with `server_default=func.now()`.
- Still write `docker-compose.yml` if your step asks for it — it is the documented
  production path — but nothing here runs it, so do not make it a VERIFY step.

### 3. Redis is optional, never required

`app/cache.py` and the M9 feedback buffer must fall back to a process-local dict
when `REDIS_URL` is unset or unreachable. Every Redis error path is a cache miss,
never an exception that reaches a handler.

### 4. Kroki becomes a local PlantUML jar

Java 26 and Graphviz are installed, and `vendor/plantuml.jar` (v1.2026.0) is present.
Validate and render PlantUML by shelling out — this exact invocation, already
verified on this machine:

```
java -jar vendor/plantuml.jar -tsvg -pipe -failfast2
```

- stdin: the PlantUML source. stdout: the SVG.
- **exit 0 → valid.** stdout is the finished SVG; use it directly for rendering.
- **exit 200 → syntax error.** stderr holds exactly three lines:
  `ERROR`, the line number, then the message (e.g. `Syntax Error?`). Join the
  last two into the `parse_error` string. Do not look at the exit code alone and
  do not treat stderr content as failure on its own — a successful render can
  still print unrelated Java warnings to stderr.

Only fall back to `KROKI_URL` if that env var is explicitly set; it is unset by
default, since using it would send user content to a third-party service.

### 5. No API key — `MOCK_LLM=1` is the default mode

There is no `ANTHROPIC_API_KEY` on this machine. `app/llm.py` must branch:

- `MOCK_LLM` truthy (default in `.env.example`) → return deterministic canned
  responses from `app/mock_llm.py`, shaped to the requested JSON schema. No
  network call.
- `ANTHROPIC_API_KEY` set and `MOCK_LLM` falsy → the real Anthropic call.

This is what makes every step from M1 onward runnable and verifiable here. Mock
mode is a first-class code path, not a test fixture — it must satisfy the same
contracts and produce DSL that genuinely passes the validator.

### 6. Verify what you can, and say plainly what you could not

If a step's VERIFY genuinely cannot run here (needs a real API key, needs Docker),
run the closest thing that does work, and in your final message state exactly which
command you ran and which you skipped and why. Never report a skipped check as a pass.
