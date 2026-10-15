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
| Phase 2 — MVP | M1, M3b, M4, M8 | **done** — `app/llm.py` (incl. mock mode), `app/templates/plantuml.py`, `app/validate.py`, `app/auth.py` |
| Phase 2 — MVP | M2, M3a, M5, M6 | **done** — the generation pipeline: `app/spec.py`, `app/templates/mermaid.py`, `app/generate.py`, `app/orchestrator.py` |
| Phase 2 — MVP | M7, M9–M13 | **remaining — 6 steps, 9 files** |

What runs today: `uvicorn app.main:app` serves the single-file UI, and
`POST /v1/generate` runs the **real** pipeline — spec extraction, concurrent
per-type generation, parser validation, repair loop — streaming each diagram
over SSE as it finishes. Mermaid renders in the browser; PlantUML renders
server-side via `POST /v1/render/plantuml` (local jar). Measured in mock mode on
this machine, a 7-diagram job streams its first diagram at ~1.7s and completes
at ~3.3s.

Still missing: nothing is persisted (so there are no sessions, no version
history and no Flow 2), feedback is accepted and discarded, and there is no
training export. Flow 1 is demoable; Flows 2 and 3 are not.

---

## 1. Environment setup (do this first — nothing below runs without it)

These artefacts are gitignored, so they are absent from a fresh clone and from
every new worktree. The whole bootstrap, verified end to end:

```bash
/opt/homebrew/bin/python3.13 -m venv .venv          # system python is 3.9: StrEnum fails
.venv/bin/pip install -r requirements.txt
cd validator && npm install && cd ..                # Mermaid parse gate (jsdom + mermaid)
mkdir -p vendor && curl -sL -o vendor/plantuml.jar \
  https://github.com/plantuml/plantuml/releases/download/v1.2026.0/plantuml-1.2026.0.jar
cp .env.example .env                                # SQLite + MOCK_LLM=1
.venv/bin/python scripts/init_db.py                 # creates the 7 tables in uml.db
```

- **Every** command in every step uses `.venv/bin/python`, never bare `python`.
- The PlantUML jar needs Java and Graphviz on PATH (`java -version`, `dot -V`).
- `.env` defaults are the intended local mode: no Docker, no API key, fully
  runnable.

Smoke test the bootstrap: `.venv/bin/python -m app.generate` prints a valid
`sequence` (Mermaid) and `deployment` (PlantUML) diagram, which exercises the
venv, both validators and the jar in one command.

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

### Ready to start now (no unmet dependencies — 3 in parallel)

| Step | Files | Model | What's missing |
|---|---|---|---|
| **M7** | `app/sessions.py` + session/diagram read handlers in `app/routes.py` | sonnet | Append-only persistence, version linkage, `prior_source()`, and `carry_forward()` so untouched diagram types survive a revision. Every read scoped by `user_id`; another user's session returns **404**, not 403. |
| **M9** | `app/feedback.py` + real `POST /v1/feedback` | sonnet | Insert the Feedback row, honour `training_consent` (store but mark `rejected`), best-effort Redis buffer, return 202. |
| **M12** | `app/cache.py` + lookup in `app/orchestrator.py` | sonnet | Keyed on normalised prompt + type + model + template version. Every Redis error is a cache miss, never an exception. Never cache a FAILED result. |

### Blocked until the above land

| Step | Files | Model | Needs |
|---|---|---|---|
| **M10** | `app/training/reward.py`, `export.py`, `trainer_stub.py` | sonnet | M9. Reward weights are already in `contracts.RewardBreakdown.total`. Export must reconstruct trajectories from stored rows only, mark rows `exported` in the same transaction, and skip non-consenting users. Stops at the ART trainer's input contract — no GPU, no RL step. |
| **M11** | `static/index.html` | sonnet | M9 (M6 has landed). Extend, don't rewrite: session rail, `X-User-Id` from localStorage, version switcher with before/after source, feedback controls, **editable source** (live re-render on change, EDIT signal on blur — the strongest training signal in the system), server-side PlantUML rendering in place of the placeholder note, repair/lint badges. |
| **M13** | `scripts/demo.py` | sonnet | M7, M9. The script you run in front of a reviewer: all three flows end to end with wall-clock deltas and a summary table. |

### Suggested execution waves

```
Wave A   M7 · M9 · M12     (3 parallel)
Wave B   M10 · M11         (2 parallel)
Wave C   M13
```

### What landed in the generation pipeline (M2, M3a, M5, M6)

Context the remaining steps need, since they build directly on it:

- `extract_spec(prompt, prior=None)` — one `effort="high"` call. Passing `prior`
  turns it into a patch: the prior spec goes in the **user** message, never in
  `system`, so the cached prefix survives a revision. `suggested_diagram_types`
  is guaranteed non-empty (falls back to the prior spec's set, then `DEFAULT_TYPES`).
- `generate_diagram(type, spec, prior_source=None)` → `(DiagramResult, list[Usage])`.
  `MAX_ATTEMPTS = 3` (one generate + two repairs). `SHARED_RULES` is byte-identical
  across all 14 types and comes first in `system`; the spec is serialised with
  **sorted keys**. Editing either invalidates the prompt cache for every in-flight
  session.
- `run_generation(req, user_id, prior_sources=None)` yields SSE frames. It takes
  `prior_sources: dict[DiagramType, str]` — that is the seam M7 fills from stored
  versions to make Flow 2 revise instead of re-draw.
- A diagram whose repair budget is exhausted ships as `diagram_complete` with
  `status="failed"`, carrying its last source and `parse_error`, because the UI
  needs that source for the user to hand-fix (and the fix is training data).
  `diagram_failed` is reserved for a diagram with no source at all — a refusal or
  a crash. Both count toward `job_complete.failed`.
- When the request omits `diagram_types`, `job_started.expected` is provisional
  (`DEFAULT_TYPES`) and the spec's suggestions replace it. The announced types are
  unioned back in, never dropped — the client has already drawn a pending tab per
  announced type.

---

## 3. Cross-cutting work the steps assume but don't own

- [ ] **Wire persistence into the orchestrator.** `app/orchestrator.py` has
      landed with no persistence at all: M7 must call `save_spec` after
      `spec_ready` and `save_diagram_version` per completed diagram, and feed
      `run_generation`'s `prior_sources` argument from the prior version so a
      revision revises instead of re-drawing. Nothing else calls those seams today.
- [x] **Store the lint result.** `generate_diagram` now runs `lint()` on every
      diagram that parses and populates `DiagramResult.lint_score` /
      `lint_warnings` (a FAILED diagram reports `0.0`, not the optimistic
      default). M7 still has to persist those two fields on `DiagramVersion` —
      M10's reward reads them from storage, not from the SSE frame.
- [ ] **Lint under-reports on nested PlantUML.** `lint()`'s graph extraction does
      not understand `node "X" { ... }` nesting, so container nodes in a
      deployment diagram are reported as orphans (score 0.75 on a correct
      diagram). It is a cosmetic badge today, but M10 turns it into 20% of the
      reward — fix the extractor or exclude containers before then.
- [ ] **`app/templates/__init__.py` and `app/training/__init__.py` are empty.**
      Fine as-is; no re-exports are expected by any step.
- [ ] **Mock mode must keep passing the validator.** `app/mock_llm.py` is a
      first-class code path, not a test fixture — every new step has to work with
      `MOCK_LLM=1`, since that is the only mode runnable here.

---

## 4. Exit gate for Phase 2

All three flows demonstrable from the UI:

1. **Flow 1** — a fresh prompt streams diagrams in, first one arriving well
   before the last. **Met** on the API (`~1.7s` to first of 7 diagrams, `~3.3s`
   to last, mock mode); the UI consumes that stream unchanged.
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
