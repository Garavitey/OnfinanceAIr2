# UML Diagram Generation Platform

A chat platform that turns a prose system description into a set of rendered UML
diagrams. One prompt in, a stream of validated diagrams out — with version
history and user feedback captured as RL training data.

Design rationale: [`HLD.md`](HLD.md) · Agent and contributor notes:
[`AGENTS.md`](AGENTS.md)

This README is written for someone reviewing the repository. It states what
works, what does not, and what is left — in that order.

---

## The problem

Given a prompt and an optional list of diagram types:

```json
{
  "prompt": "I am working on a compliance monitoring solution which will pull in the latest circulars from SEBI and parse them. Once it is parsed into a table to clauses, you will need to extract: (1) the new compliance requirements proposed by the regulator, (2) gap analysis with my existing compliance setup, (3) the impact of these new requirements on my organization at an IT and operational level.",
  "diagram_types": ["sequence", "component"]
}
```

...produce correct, renderable UML. Three user flows are in scope: a new user
generating a set, an existing user revising a prompt and getting an updated set,
and an existing user leaving feedback that reaches a LangChain ART trainer.

---

## Quickstart

Full setup — venv, Mermaid sidecar, PlantUML jar — is in
[`AGENTS.md`](AGENTS.md#setup). Nothing runs on a fresh clone until you do it,
because every one of those artefacts is gitignored. Short version:

```bash
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd validator && npm install && cd ..
mkdir -p vendor && curl -sL -o vendor/plantuml.jar \
  https://github.com/plantuml/plantuml/releases/download/v1.2026.0/plantuml-1.2026.0.jar
cp .env.example .env
.venv/bin/python scripts/init_db.py

.venv/bin/python -m app.generate      # smoke test: venv + both validators + jar
.venv/bin/uvicorn app.main:app        # then open the printed URL
```

Requires Java and Graphviz on PATH for the PlantUML jar.

**Everything runs in mock mode by default** (`MOCK_LLM=1`). There is no API key
in this repo, so `app/mock_llm.py` returns deterministic canned responses shaped
to the same JSON schema the live model uses. See
[the caveat below](#the-caveat-that-matters-most).

---

## Status

| Phase | Steps | State |
|---|---|---|
| Phase 0 — POC | C1–C4 | **done** — `poc/`, `validator/validate.mjs` |
| Phase 1 — scaffold | S1–S6 | **done** — contracts frozen, DB models, type matrix, API, UI shell |
| Phase 2 — MVP | M1–M6, M8 | **done** — the generation pipeline, validators, auth stub |
| Phase 2 — MVP | M7, M9–M13 | **remaining — 6 steps** |

### What works

**Flow 1 is complete and demonstrable.** `POST /v1/generate` runs the real
pipeline — spec extraction, concurrent per-type generation, parser validation,
repair loop — streaming each diagram over SSE as it finishes. Measured in mock
mode on an M-series Mac, the payload above across 7 diagram types:

```
  0.04s  job_started        expected: 7 types
  0.62s  spec_ready         entities=6 actors=4
  1.98s  diagram_complete   use_case        valid  repairs=0
  ...
  4.75s  diagram_complete   deployment      valid  repairs=0
  4.75s  job_complete       7 succeeded, 0 failed
```

First diagram at 1.98s against a 4.75s job — the point of streaming per
completion rather than gathering.

- **All 14 UML 2.x diagram types** generate and pass their real parser. Seven
  are Mermaid-backed, seven PlantUML-backed; two (`use_case`, `package`) are
  flagged `approximated` where Mermaid has no native UML form. The capability
  matrix in `app/diagram_types.py` is the single source of truth and is
  assertion-checked against the `DiagramType` enum at import.
- **Syntax is verified, not trusted.** Every diagram passes a real parser before
  the browser sees it — the Node sidecar for Mermaid, the jar for PlantUML. On a
  parse failure the error text goes back to the model as a targeted repair
  prompt, up to `MAX_ATTEMPTS = 3`. A diagram that exhausts its budget ships as
  `failed` but **keeps its last source**, so the user can hand-fix it — and that
  fix is the strongest training signal in the system.
- **Both renderers reach the UI.** Mermaid renders client-side; PlantUML
  round-trips through `POST /v1/render/plantuml` to SVG via the local jar, so no
  diagram source leaves the machine.

### What does not work

- **Flow 2 (revision) is not wired.** The seams exist and are typed —
  `extract_spec(prior=)`, `generate_diagram(prior_source=)`,
  `run_generation(prior_sources=)` — but nothing is persisted, so a request
  carrying a `session_id` returns 200 and silently re-draws from scratch.
  `GET /v1/sessions` is a stub returning `[]`.
- **Flow 3 (feedback → ART) is not wired.** `POST /v1/feedback` returns 202 and
  discards the payload. The schema is there — `Feedback` table, five signal
  types, `RewardBreakdown` with weights fixed at 0.30 parse / 0.20 lint / 0.30
  human / 0.15 edit / 0.05 implicit, `TrajectoryRecord` — but `app/training/` is
  an empty package. No insert, no reward computation, no JSONL export.

### The caveat that matters most

**The live model path has never executed.** There is no API key here, so every
diagram this repo has ever produced came from `app/mock_llm.py` — hand-written
DSL in the SEBI compliance domain that genuinely passes the validators, but is
not model output. What is verified is the machinery: streaming, concurrency, the
parse gate, the repair loop, both renderers. What is entirely unverified is
whether the model writes good UML for an arbitrary prompt.

Two things to check the moment a key is wired: `app/llm.py` pins
`MODEL = "claude-opus-5"`, and the call uses the `output_config`/`effort` shape.
Neither has been exercised against the real endpoint. Prompt caching is wired —
a stable system prefix under `cache_control`, spec serialised with sorted keys —
but has never been measured.

---

## Remaining work

### Unblocked

| Step | Files | What's missing |
|---|---|---|
| **M7** | `app/sessions.py`, session/diagram read handlers in `app/routes.py` | Append-only persistence, version linkage, `prior_source()`, and `carry_forward()` so untouched diagram types survive a revision. Every read scoped by `user_id`; another user's session returns **404**, not 403. Unblocks Flow 2. |
| **M9** | `app/feedback.py`, real `POST /v1/feedback` | Insert the Feedback row, honour `training_consent` (store but mark `rejected`), best-effort Redis buffer, return 202. Unblocks Flow 3. |
| **M12** | `app/cache.py`, lookup in `app/orchestrator.py` | Keyed on normalised prompt + type + model + template version. Every Redis error is a cache miss, never an exception. Never cache a `FAILED` result. |

### Blocked

| Step | Files | Needs |
|---|---|---|
| **M10** | `app/training/reward.py`, `export.py`, `trainer_stub.py` | M9. Reconstruct trajectories from stored rows only, mark rows `exported` in the same transaction, skip non-consenting users. Stops at the ART trainer's input contract — no GPU, no RL step. |
| **M11** | `static/index.html` | M9. Extend, don't rewrite: session rail, `X-User-Id` from localStorage, version switcher with before/after source, feedback controls, and **editable source** (live re-render on change, EDIT signal on blur). |
| **M13** | `scripts/demo.py` | M7, M9. All three flows end to end with wall-clock deltas and a summary table. |

Suggested order: `M7 · M9 · M12` in parallel, then `M10 · M11`, then `M13`.

### Cross-cutting

- [ ] **Wire persistence into the orchestrator.** `app/orchestrator.py` has no
      persistence at all. M7 must call `save_spec` after `spec_ready` and
      `save_diagram_version` per completed diagram, and feed `prior_sources`
      from the prior version so a revision revises instead of re-draws.
- [ ] **Persist the lint result.** `generate_diagram` populates
      `DiagramResult.lint_score` / `lint_warnings`; M7 has to store them on
      `DiagramVersion`, because M10's reward reads them from storage, not from
      the SSE frame.
- [ ] **Lint under-reports on nested PlantUML.** `lint()`'s graph extractor does
      not understand `node "X" { ... }` nesting, so container nodes in a
      deployment diagram are reported as orphans — a correct diagram scores
      0.75. Cosmetic today, but lint becomes 20% of the training reward, so this
      poisons the signal the moment M10 lands.
- [ ] **There is no test suite.** Everything claimed above was verified by hand.
      The four checks worth automating first: all 14 types parse, the repair
      loop promotes `failed`→`repaired`, the parse gate rejects malformed
      source, and SSE frame order holds.

---

## Exit gate for Phase 2

1. **Flow 1** — a fresh prompt streams diagrams in, first well before last.
   **Met.**
2. **Flow 2** — a revised prompt returns an updated set with history preserved
   and untouched types carried forward. **Not met.**
3. **Flow 3** — feedback is captured and appears in the JSONL training export.
   **Not met.**

---

## Deliberately out of scope

Designed in [`HLD.md`](HLD.md), with no build step: real OIDC/JWT auth (M8 is a
stub with the right seam), retry/backoff, rate limiting, metrics and alerting
dashboards, load testing, actual ART GPU training, and the HLD §10 failure-mode
drills.

The two cheapest to add later, and the two most likely to be asked about: **real
auth** (swap the `current_user` dependency, no handler changes) and **metrics**
(the `Usage` dataclass in `app/llm.py` already carries every token and latency
number the dashboards need — only the emit call is missing).
