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
