# UML Diagram Generation Platform — Remaining Work

Chat platform that turns a prose system description into a set of UML diagrams,
streams them to the browser as they finish, keeps every prompt and diagram
version, and captures user feedback as RL training data.

Design: [`HLD.md`](HLD.md) · Build order: [`PLAN.md`](PLAN.md) ·
Agent constraints and local environment overrides: [`AGENT_RULES.md`](AGENT_RULES.md)

This README tracks **what is left**. It is the handoff doc, not the design doc.

---

## Status at a glance

| Phase | Steps | State |
|---|---|---|
| Phase 0 — POC | C1–C4 | **done** — `poc/generate.py`, `poc/validate.py`, `poc/two_phase.py`, `validator/validate.mjs` |
| Phase 1 — PLAN | S1–S6 | **done** — contracts frozen, DB models, type matrix, stubbed API, UI shell |
| Phase 2 — MVP | M1, M3b, M4 | **done** — `app/llm.py` (incl. mock mode), `app/templates/plantuml.py`, `app/validate.py` |
| Phase 2 — MVP | M2, M3a, M5–M13 | **remaining — 11 steps, 15 files** |

What runs today: `uvicorn app.main:app` serves the single-file UI, which streams
**fixture** diagrams over real SSE and renders them as tabs. PlantUML source
renders for real via `POST /v1/render/plantuml` (local jar). Nothing is
persisted, no LLM is called from a request path, no feedback is recorded.

---

## 1. Environment setup (do this first — nothing below runs without it)

These artefacts are gitignored and absent from a fresh clone:

- [ ] **`.venv/`** — Python 3.13 venv. System Python is 3.9 and will fail on
      `StrEnum` and modern typing. Create it, then `.venv/bin/pip install -r requirements.txt`.
      **Every** command in every step uses `.venv/bin/python`, never bare `python`.
- [ ] **`validator/node_modules/`** — `cd validator && npm install`. Required by
      the Mermaid parse gate (`app/validate.py` shells out to `validator/validate.mjs`).
- [ ] **`vendor/plantuml.jar`** — PlantUML v1.2026.0. Required by PlantUML
      validation and by `POST /v1/render/plantuml`. Needs Java and Graphviz on PATH.
- [ ] **`.env`** — copy from `.env.example`. Defaults are SQLite + `MOCK_LLM=1`,
      which is the intended local mode: no Docker, no API key, fully runnable.
- [ ] **`uml.db`** — `.venv/bin/python scripts/init_db.py` (creates 7 tables).
      Not yet run in this workspace; M7/M9/M10 all need it.

`docker-compose.yml` (Postgres + Redis + Kroki) is the documented production
path and is intentionally **not** the local path — see the local overrides in
`AGENT_RULES.md`. Redis is optional everywhere; `KROKI_URL` stays unset so no
diagram source leaves the machine.

---

## 2. Remaining steps

Each step's copy-paste prompt is in `PLAN.md` under the matching heading. Prepend:

```
Read AGENT_RULES.md first. Then do only this step:
```

### Ready to start now (no unmet dependencies — 5 in parallel)

| Step | Files | Model | What's missing |
|---|---|---|---|
| **M2** | `app/spec.py` | sonnet | `extract_spec(prompt, prior)` — the one genuinely hard reasoning call (effort `high`). Must support PATCH-ing a prior spec for revisions (Flow 2) and return non-empty `suggested_diagram_types`. |
| **M3a** | `app/templates/mermaid.py` | **opus** | Per-type prompt fragments for the 7 Mermaid types (CLASS, SEQUENCE, STATE_MACHINE, ACTIVITY, COMPONENT, USE_CASE, PACKAGE). Blocks M5. This file is the main lever on first-attempt parse rate. Match the structure of the finished `app/templates/plantuml.py`. |
| **M7** | `app/sessions.py` + session/diagram read handlers in `app/routes.py` | sonnet | Append-only persistence, version linkage, `prior_source()`, and `carry_forward()` so untouched diagram types survive a revision. Every read scoped by `user_id`; another user's session returns **404**, not 403. |
| **M8** | `app/auth.py` + dependency on the `/v1` routes | sonnet | `current_user` from `X-User-Id`, defaulting to a demo UUID. `user_id` must come from the dependency only — taking it from the body is a trivial IDOR. |
| **M9** | `app/feedback.py` + real `POST /v1/feedback` | sonnet | Insert the Feedback row, honour `training_consent` (store but mark `rejected`), best-effort Redis buffer, return 202. |

### Blocked until the above land

| Step | Files | Model | Needs |
|---|---|---|---|
| **M5** | `app/generate.py` | **opus** | M3a. Per-diagram generation + validate + repair loop (max 3 attempts); FAILED still returns the last source so the user can hand-fix it. |
| **M10** | `app/training/reward.py`, `export.py`, `trainer_stub.py` | sonnet | M9. Reward weights are already in `contracts.RewardBreakdown.total`. Export must reconstruct trajectories from stored rows only, mark rows `exported` in the same transaction, and skip non-consenting users. Stops at the ART trainer's input contract — no GPU, no RL step. |
| **M6** | `app/orchestrator.py` + real `POST /v1/generate` | **opus** | M2, M5. Fan out over types with `Semaphore(6)`, yielding each result **as it completes** (`as_completed`/queue, never gather-then-yield — that defeats the whole latency strategy). One diagram failing must not fail the job. Do not change any route or event shape. |
| **M11** | `static/index.html` | sonnet | M6, M9. Extend, don't rewrite: session rail, `X-User-Id` from localStorage, version switcher with before/after source, feedback controls, **editable source** (live re-render on change, EDIT signal on blur — the strongest training signal in the system), server-side PlantUML rendering in place of the placeholder note, repair/lint badges. |
| **M12** | `app/cache.py` + lookup in `app/orchestrator.py` | sonnet | M6. Keyed on normalised prompt + type + model + template version. Every Redis error is a cache miss, never an exception. Never cache a FAILED result. |
| **M13** | `scripts/demo.py` | sonnet | M6, M7, M9. The script you run in front of a reviewer: all three flows end to end with wall-clock deltas and a summary table. |

### Suggested execution waves

```
Wave A   M2 · M3a · M7 · M8 · M9     (5 parallel)
Wave B   M5 · M10                    (2 parallel)
Wave C   M6
Wave D   M11 · M12                   (2 parallel)
Wave E   M13
```

---

## 3. Cross-cutting work the steps assume but don't own

- [ ] **Wire persistence into the orchestrator.** M7 writes `app/sessions.py`;
      M6 writes `app/orchestrator.py`. Whoever lands second must call `save_spec`
      and `save_diagram_version` at the obvious points. Easy to drop between the two.
- [ ] **Store the lint result.** `app/validate.py:lint()` exists and M10's reward
      reads a stored lint score, but no column or payload currently carries it —
      today it silently defaults to 1.0. Decide where it lives (M5 result or
      `DiagramVersion`) before M10.
- [ ] **`app/templates/__init__.py` and `app/training/__init__.py` are empty.**
      Fine as-is; no re-exports are expected by any step.
- [ ] **Mock mode must keep passing the validator.** `app/mock_llm.py` is a
      first-class code path, not a test fixture — every new step has to work with
      `MOCK_LLM=1`, since that is the only mode runnable here.

---

## 4. Exit gate for Phase 2

All three flows demonstrable from the UI:

1. **Flow 1** — a fresh prompt streams diagrams in, first one arriving well before the last.
2. **Flow 2** — a revised prompt in the same session returns an updated set with
   history preserved and untouched types carried forward.
3. **Flow 3** — feedback is captured and shows up in the JSONL training export.

---

## 5. Deliberately out of scope

Designed in `HLD.md`, no build step anywhere: real OIDC/JWT auth (M8 is a stub
with the right seam), retry/backoff, rate limiting, metrics and alerting
dashboards, load testing, actual ART GPU training, and the HLD §10 failure-mode
drills.

The two cheapest to add later and most likely to be asked about: **real auth**
(swap the `current_user` dependency, no handler changes) and **metrics** (the
`Usage` dataclass in `app/llm.py` already carries every token and latency number
the dashboards need — only the emit call is missing).
